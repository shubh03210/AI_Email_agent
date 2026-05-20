"""
Gmail Message Distributed Lock
────────────────────────────────
Redis-backed distributed lock that prevents concurrent Celery workers from
processing the same Gmail message more than once.

Phase 3 lock namespaces:
  lock:gmail:{message_id}   — one inbound Gmail message (set during poll)
  lock:thread:{thread_id}   — one agent run per DB thread (set during run_agent_task)

Phase 6 additions:
  idempotency:outreach:{prospect_id}
      — prevents duplicate outreach emails when send_outreach_task is retried
        after the Gmail call succeeded but the DB write failed.  TTL =
        TASK_OUTREACH_IDEM_TTL (24 h by default).

  lock:agent_queued:{thread_id}
      — prevents successive Celery Beat poll cycles from re-enqueueing an
        agent run for a thread that already has one queued or running.
        TTL = TASK_AGENT_ENQUEUE_TTL (6 min by default — just over the
        agent task's soft_time_limit).

Design:
  - Lock is acquired with SET NX EX (atomic, safe for concurrent workers).
  - Default TTL = 300 s (5 minutes) — long enough for the full agent pipeline.
  - Fail-open: if Redis is unreachable the lock functions return True (lock
    "acquired") so processing is never silently dropped.
  - Synchronous redis-py client is used because Celery tasks are synchronous
    (async parts are bridged via asyncio.run()).  No aioredis dependency.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Generator, Optional

import redis

from app.core.config import settings
from app.core.logging import logger

# ── Constants ─────────────────────────────────────────────────────────────────

MESSAGE_LOCK_PREFIX  = "lock:gmail:"
THREAD_LOCK_PREFIX   = "lock:thread:"
OUTREACH_IDEM_PREFIX = "idempotency:outreach:"
AGENT_QUEUED_PREFIX  = "lock:agent_queued:"
LOCK_TTL_SECONDS     = 300     # 5 minutes


# ── Redis client ──────────────────────────────────────────────────────────────

def _get_redis() -> redis.Redis:
    """
    Return a synchronous Redis client backed by a connection pool.
    Uses the Celery broker URL so no extra config entry is needed.
    """
    return redis.from_url(
        settings.CELERY_BROKER_URL,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )


# ── Low-level primitives ──────────────────────────────────────────────────────

def _acquire(key: str, ttl: int = LOCK_TTL_SECONDS) -> bool:
    """
    Atomically SET key=1 NX EX ttl.

    Returns:
        True  — lock acquired (key was NOT previously set).
        False — lock already held (key was already set).

    On Redis errors, logs a warning and returns True (fail-open) so that
    messages are never silently dropped due to a Redis outage.
    """
    try:
        client = _get_redis()
        result = client.set(key, "1", nx=True, ex=ttl)
        return bool(result)
    except Exception as exc:
        logger.warning(f"[gmail_lock] Redis unavailable — failing open for key={key}: {exc}")
        return True


def _release(key: str) -> None:
    """
    Delete the lock key.  Silently ignores errors (key may have already expired).
    """
    try:
        _get_redis().delete(key)
    except Exception as exc:
        logger.warning(f"[gmail_lock] Failed to release key={key}: {exc}")


# ── Public API ────────────────────────────────────────────────────────────────

def acquire_message_lock(gmail_message_id: str) -> bool:
    """
    Try to acquire an exclusive processing lock for one Gmail message.

    Returns True if the caller may proceed (lock acquired or Redis down).
    Returns False if another worker is already processing this message.
    """
    key = f"{MESSAGE_LOCK_PREFIX}{gmail_message_id}"
    acquired = _acquire(key)
    if acquired:
        logger.debug(f"[gmail_lock] acquired message lock {gmail_message_id}")
    else:
        logger.info(
            f"[gmail_lock] message {gmail_message_id} already locked — skipping"
        )
    return acquired


def release_message_lock(gmail_message_id: str) -> None:
    """Release the per-message lock after processing is complete."""
    _release(f"{MESSAGE_LOCK_PREFIX}{gmail_message_id}")


def acquire_thread_lock(thread_id: int) -> bool:
    """
    Try to acquire an exclusive agent-run lock for one DB thread.

    Prevents two concurrent Celery workers from running the agent on the
    same thread simultaneously (e.g. two new messages arrive in rapid succession).

    Returns True if the caller may proceed.
    """
    key = f"{THREAD_LOCK_PREFIX}{thread_id}"
    acquired = _acquire(key)
    if acquired:
        logger.debug(f"[gmail_lock] acquired thread lock thread_id={thread_id}")
    else:
        logger.info(
            f"[gmail_lock] thread {thread_id} already locked — agent run skipped"
        )
    return acquired


def release_thread_lock(thread_id: int) -> None:
    """Release the per-thread agent-run lock."""
    _release(f"{THREAD_LOCK_PREFIX}{thread_id}")


@contextmanager
def message_lock(gmail_message_id: str) -> Generator[bool, None, None]:
    """
    Context manager that acquires + auto-releases the per-message lock.

    Usage:
        with message_lock(msg_id) as acquired:
            if not acquired:
                return  # already being processed
            ... process ...
    """
    acquired = acquire_message_lock(gmail_message_id)
    try:
        yield acquired
    finally:
        if acquired:
            release_message_lock(gmail_message_id)


@contextmanager
def thread_lock(thread_id: int) -> Generator[bool, None, None]:
    """
    Context manager that acquires + auto-releases the per-thread agent-run lock.

    Usage:
        with thread_lock(thread_id) as acquired:
            if not acquired:
                return
            ... run agent ...
    """
    acquired = acquire_thread_lock(thread_id)
    try:
        yield acquired
    finally:
        if acquired:
            release_thread_lock(thread_id)


# ── Phase 6: Outreach idempotency ─────────────────────────────────────────────

def acquire_outreach_lock(prospect_id: int) -> bool:
    """
    Acquire an idempotency lock for sending a cold outreach email.

    Prevents ``send_outreach_task`` from double-sending when retried after
    a Gmail call that succeeded but whose DB write subsequently failed.

    The lock uses TASK_OUTREACH_IDEM_TTL (default 24 h) to cover any
    realistic retry window.

    Returns:
        True  — caller may proceed (lock acquired, no prior send in window).
        False — a send was already made for this prospect in the TTL window.
    """
    from app.core.config import settings
    key = f"{OUTREACH_IDEM_PREFIX}{prospect_id}"
    acquired = _acquire(key, ttl=settings.TASK_OUTREACH_IDEM_TTL)
    if not acquired:
        logger.info(
            f"[gmail_lock] Outreach idempotency key exists for "
            f"prospect_id={prospect_id} — skipping duplicate send"
        )
    return acquired


def release_outreach_lock(prospect_id: int) -> None:
    """
    Release the outreach idempotency lock.

    Call this ONLY when the outreach task fails and the email was never
    sent (so the next retry is allowed to proceed).  Do NOT call after a
    successful send — the TTL-based expiry handles cleanup.
    """
    _release(f"{OUTREACH_IDEM_PREFIX}{prospect_id}")


# ── Phase 6: Agent enqueue deduplication ─────────────────────────────────────

def mark_agent_queued(thread_id: int) -> None:
    """
    Mark a thread as having an agent run queued or in-flight.

    Called by ``_poll_inbox`` immediately after ``run_agent_task.apply_async``
    so that subsequent poll cycles (within TASK_AGENT_ENQUEUE_TTL) skip
    re-enqueueing the same thread.

    Uses a separate key prefix (``lock:agent_queued:``) distinct from the
    run-time thread lock (``lock:thread:``) so that:
      - The enqueue guard persists for the full TTL even after the run-time
        lock is released.
      - Clearing one does not accidentally clear the other.
    """
    from app.core.config import settings
    key = f"{AGENT_QUEUED_PREFIX}{thread_id}"
    try:
        client = _get_redis()
        # Overwrite existing key (not NX) so TTL is refreshed on re-enqueue
        client.set(key, "1", ex=settings.TASK_AGENT_ENQUEUE_TTL)
        logger.debug(
            f"[gmail_lock] agent_queued key set for thread_id={thread_id} "
            f"ttl={settings.TASK_AGENT_ENQUEUE_TTL}s"
        )
    except Exception as exc:
        logger.warning(
            f"[gmail_lock] Failed to set agent_queued key for "
            f"thread_id={thread_id}: {exc}"
        )


def is_agent_queued_or_running(thread_id: int) -> bool:
    """
    Check whether an agent run is already queued OR currently running for
    this thread.

    Returns True (suppress new enqueue) if either:
      - ``lock:agent_queued:{thread_id}`` exists   (task queued, not started yet), OR
      - ``lock:thread:{thread_id}`` exists          (task running, run-time lock held).

    Returns False (allow enqueue) when both keys are absent, i.e. the
    thread has no pending or active agent run.

    Fail-open: if Redis is unreachable returns False so processing is never
    silently dropped.
    """
    queued_key = f"{AGENT_QUEUED_PREFIX}{thread_id}"
    running_key = f"{THREAD_LOCK_PREFIX}{thread_id}"
    try:
        client = _get_redis()
        exists = client.exists(queued_key, running_key)
        return bool(exists)
    except Exception as exc:
        logger.warning(
            f"[gmail_lock] Redis unavailable for enqueue check "
            f"thread_id={thread_id}: {exc} — allowing enqueue (fail-open)"
        )
        return False
