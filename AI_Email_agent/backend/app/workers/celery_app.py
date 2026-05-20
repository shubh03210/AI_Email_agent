"""
Celery Application
───────────────────
Configures the Celery app, task queues, routing, beat schedule, and
production-grade task infrastructure.

Phase 6 — Task Infrastructure Hardening:
  Crash-safe ack:     task_reject_on_worker_lost=True ensures a task on a
                      crashed worker is REJECTED (returned to queue) rather
                      than silently dropped.

  Per-task base classes:
    GmailTask      — 5 retries, exponential back-off, for Gmail/outreach tasks.
    AgentTask      — 3 retries, for LangGraph agent runs.
    DefaultTask    — 3 retries, for cleanup and misc tasks.

  Dead-letter queue:  When a task exhausts all retries the task_failure signal
                      serialises the task context to a Redis list (key
                      "celery:dlq").  Entries are capped at TASK_DLQ_MAX_SIZE.
                      Operators can inspect failed tasks via GET /health/worker.

Queues:
  default  — general tasks (cleanup, misc)
  agent    — AI agent runs (CPU/IO heavy, can scale independently)
  gmail    — Gmail polling + email sending (rate-limited by Gmail API)

Beat Schedule:
  poll_inbox          — runs every GMAIL_POLL_INTERVAL_SECONDS (default 60s)
  cleanup_old_logs    — runs daily at 02:00 UTC

Structured lifecycle logging is wired via Celery signals so that every
task start, success, retry, and failure emits a consistent structured log
line through the shared loguru logger — no duplicate logging code in tasks.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timezone as dt_timezone

import redis  # imported at module level so tests can patch app.workers.celery_app.redis

from celery import Celery, Task
from celery.schedules import crontab
from celery.signals import (
    task_failure,
    task_postrun,
    task_prerun,
    task_retry,
)
from kombu import Exchange, Queue

from app.core.config import settings
from app.core.logging import logger


# ── App ───────────────────────────────────────────────────────────────────────

celery_app = Celery(
    "email_agent",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.workers.tasks"],
)

# ── Serialisation & Reliability ───────────────────────────────────────────────

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # Store results for 24 h then expire
    result_expires=86400,
    # Acknowledge task only AFTER it completes (prevents silent loss on crash)
    task_acks_late=True,
    # REJECT (re-queue) a task when the worker executing it is lost/killed.
    # Without this, a task on a crashed worker stays unacknowledged until the
    # connection timeout, then may or may not be re-queued depending on the
    # broker.  With True, it is immediately re-queued on disconnect.
    task_reject_on_worker_lost=True,
    # Don't prefetch more than 1 task per worker slot (prevents starvation and
    # ensures that long-running agent tasks don't block Gmail tasks on the same
    # worker process).
    worker_prefetch_multiplier=1,
)

# ── Queues & Routing ──────────────────────────────────────────────────────────

default_exchange = Exchange("default", type="direct")
agent_exchange   = Exchange("agent",   type="direct")
gmail_exchange   = Exchange("gmail",   type="direct")

celery_app.conf.task_queues = (
    Queue("default", default_exchange, routing_key="default"),
    Queue("agent",   agent_exchange,   routing_key="agent"),
    Queue("gmail",   gmail_exchange,   routing_key="gmail"),
)

celery_app.conf.task_default_queue        = "default"
celery_app.conf.task_default_exchange     = "default"
celery_app.conf.task_default_routing_key  = "default"

celery_app.conf.task_routes = {
    "app.workers.tasks.run_agent_task":               {"queue": "agent",   "routing_key": "agent"},
    "app.workers.tasks.send_outreach_task":           {"queue": "gmail",   "routing_key": "gmail"},
    "app.workers.tasks.poll_inbox_task":              {"queue": "gmail",   "routing_key": "gmail"},
    "app.workers.tasks.follow_up_silent_prospects":   {"queue": "gmail",   "routing_key": "gmail"},
    "app.workers.tasks.cleanup_old_logs":             {"queue": "default", "routing_key": "default"},
}

# ── Beat Schedule ─────────────────────────────────────────────────────────────

celery_app.conf.beat_schedule = {
    "poll-inbox": {
        "task":     "app.workers.tasks.poll_inbox_task",
        "schedule": settings.GMAIL_POLL_INTERVAL_SECONDS,  # seconds
        "options":  {"queue": "gmail"},
    },
    "follow-up-silent-prospects": {
        "task":     "app.workers.tasks.follow_up_silent_prospects",
        "schedule": crontab(hour=8, minute=0),             # daily at 08:00 UTC
        "options":  {"queue": "gmail"},
    },
    "cleanup-old-logs": {
        "task":     "app.workers.tasks.cleanup_old_logs",
        "schedule": crontab(hour=2, minute=0),             # daily at 02:00 UTC
        "options":  {"queue": "default"},
    },
}


# ── Per-task base classes ─────────────────────────────────────────────────────
# Each queue has its own base class with tuned retry limits.
# All tasks use exponential_backoff() to compute per-attempt delay.

class GmailTask(Task):
    """
    Base for Gmail and outreach tasks.

    Gmail API has aggressive rate-limiting (429 errors) and transient
    5xx failures that warrant more retry attempts with longer back-off.
    5 retries: 30 s → 60 s → 120 s → 240 s → 480 s (≈ 16 min total wait).
    """
    abstract = True
    max_retries = settings.TASK_MAX_RETRIES_GMAIL


class AgentTask(Task):
    """
    Base for LangGraph agent-run tasks.

    Agent runs are expensive (LLM calls + DB writes).  Fewer retries with
    a moderate delay avoids hammering the LLM during transient rate limits.
    3 retries: 60 s → 120 s → 240 s.
    """
    abstract = True
    max_retries = settings.TASK_MAX_RETRIES_AGENT


class DefaultTask(Task):
    """
    Base for cleanup and miscellaneous tasks.

    These tasks are idempotent and short-lived.  3 retries at a short
    base delay.
    """
    abstract = True
    max_retries = settings.TASK_MAX_RETRIES_DEFAULT


def exponential_backoff(
    retries: int,
    base: int | None = None,
    cap: int | None = None,
    jitter: bool = True,
) -> int:
    """
    Compute an exponential back-off delay for a retry attempt.

    Delay = min(base * 2^retries, cap), optionally jittered by ±10 %
    to prevent thundering-herd re-bursts when many tasks fail together.

    Args:
        retries:  Number of attempts already made (0-based Celery request.retries).
        base:     Base delay in seconds (default: TASK_RETRY_BACKOFF_BASE).
        cap:      Maximum delay in seconds (default: TASK_RETRY_BACKOFF_CAP).
        jitter:   If True, add ±10% random jitter to the computed delay.

    Returns:
        Integer seconds to wait before the next retry.

    Examples:
        exponential_backoff(0)  →  30
        exponential_backoff(1)  →  60
        exponential_backoff(2)  →  120
        exponential_backoff(4)  →  480  (capped at 600 by default)
    """
    _base = base if base is not None else settings.TASK_RETRY_BACKOFF_BASE
    _cap  = cap  if cap  is not None else settings.TASK_RETRY_BACKOFF_CAP
    delay = min(_base * (2 ** retries), _cap)
    if jitter:
        delta = delay * 0.10
        delay += random.uniform(-delta, delta)
    return max(1, int(delay))


# ── Dead-letter Queue ─────────────────────────────────────────────────────────

_DLQ_KEY = "celery:dlq"


def _dlq_write(
    task_id: str,
    task_name: str,
    queue: str,
    args: tuple,
    kwargs: dict,
    exception: Exception,
    retry_count: int,
    einfo: object,
) -> None:
    """
    Persist a permanently-failed task to the Redis dead-letter queue.

    The entry is prepended to the ``celery:dlq`` Redis list as a JSON
    string and the list is trimmed to TASK_DLQ_MAX_SIZE entries.

    Called synchronously from the task_failure signal handler so it must
    be fast and never raise.
    """
    try:
        client = redis.from_url(
            settings.CELERY_BROKER_URL,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        entry = json.dumps(
            {
                "task_id":          task_id,
                "task_name":        task_name,
                "queue":            queue,
                "args":             _safe_serialise(args),
                "kwargs":           _safe_serialise(kwargs),
                "exception_type":   type(exception).__name__,
                "exception_message": str(exception)[:500],
                "retry_count":      retry_count,
                "failed_at":        datetime.now(dt_timezone.utc).isoformat(),
                "traceback":        str(einfo)[:1000] if einfo else None,
            },
            default=str,
        )
        pipe = client.pipeline()
        pipe.lpush(_DLQ_KEY, entry)
        pipe.ltrim(_DLQ_KEY, 0, settings.TASK_DLQ_MAX_SIZE - 1)
        pipe.execute()

        logger.error(
            f"[dlq] Task permanently failed — written to DLQ | "
            f"task_id={task_id} task={task_name} queue={queue} "
            f"exception={type(exception).__name__}: {str(exception)[:200]}"
        )
    except Exception as dlq_exc:
        logger.error(f"[dlq] Failed to write to DLQ: {dlq_exc}")


def _safe_serialise(value: object) -> object:
    """Return a JSON-safe representation, truncating large values."""
    try:
        text = json.dumps(value, default=str)
        if len(text) > 2000:
            return f"<truncated {len(text)} chars>"
        return value
    except Exception:
        return repr(value)[:500]


def dlq_read(offset: int = 0, limit: int = 20) -> list[dict]:
    """
    Read entries from the Redis dead-letter queue.

    Returns a list of dicts (newest first) suitable for the health
    monitoring API endpoint.  Returns an empty list if Redis is
    unreachable.

    Args:
        offset: Number of entries to skip (for pagination).
        limit:  Maximum entries to return.
    """
    try:
        client = redis.from_url(
            settings.CELERY_BROKER_URL,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        raw_entries = client.lrange(_DLQ_KEY, offset, offset + limit - 1)
        result = []
        for raw in raw_entries:
            try:
                result.append(json.loads(raw))
            except Exception:
                result.append({"raw": raw})
        return result
    except Exception as exc:
        logger.warning(f"[dlq] Could not read DLQ: {exc}")
        return []


def dlq_length() -> int:
    """Return the number of entries in the DLQ (0 if Redis unreachable)."""
    try:
        client = redis.from_url(
            settings.CELERY_BROKER_URL,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        return client.llen(_DLQ_KEY) or 0
    except Exception:
        return 0


def queue_depth(queue_name: str) -> int:
    """
    Return the number of pending tasks in a Celery queue.

    In Celery's Redis transport tasks are stored in a Redis list whose
    key matches the queue name.  LLEN returns the pending (not-yet-
    consumed) task count.

    Returns 0 if Redis is unreachable.
    """
    try:
        client = redis.from_url(
            settings.CELERY_BROKER_URL,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        return client.llen(queue_name) or 0
    except Exception:
        return 0


# ── Structured task lifecycle signals ─────────────────────────────────────────
# These fire for EVERY task — no per-task boilerplate needed.

@task_prerun.connect
def _on_task_prerun(task_id: str, task, args, kwargs, **_kw) -> None:
    """Emitted just before a task is executed by a worker."""
    logger.info(
        f"[celery] task_start name={task.name} "
        f"task_id={task_id} args={args} kwargs={kwargs}"
    )


@task_postrun.connect
def _on_task_postrun(
    task_id: str, task, args, kwargs, retval, state: str, **_kw
) -> None:
    """Emitted after a task has finished (success or failure)."""
    if state == "SUCCESS":
        logger.info(
            f"[celery] task_success name={task.name} "
            f"task_id={task_id} result={retval}"
        )
    else:
        logger.warning(
            f"[celery] task_finish name={task.name} "
            f"task_id={task_id} state={state}"
        )


@task_retry.connect
def _on_task_retry(request, reason, einfo, **_kw) -> None:
    """Emitted when a task is about to be retried."""
    logger.warning(
        f"[celery] task_retry name={request.task} "
        f"task_id={request.id} reason={reason} "
        f"retries={request.retries}"
    )


@task_failure.connect
def _on_task_failure(
    task_id: str,
    exception: Exception,
    traceback,
    einfo,
    args: tuple,
    kwargs: dict,
    sender,
    **_kw,
) -> None:
    """
    Emitted when a task raises an unhandled exception (all retries exhausted).

    Phase 6: writes the failed task to the Redis dead-letter queue so
    operators can inspect and optionally reprocess permanently-failed tasks.
    """
    logger.error(
        f"[celery] task_failure task_id={task_id} "
        f"exception={type(exception).__name__}: {exception}"
    )

    # Determine which queue this task belongs to from the routing config
    task_name = getattr(sender, "name", "unknown")
    route_info = celery_app.conf.task_routes.get(task_name, {})
    queue = route_info.get("queue", "unknown")
    retry_count = getattr(getattr(sender, "request", None), "retries", 0)

    _dlq_write(
        task_id=task_id,
        task_name=task_name,
        queue=queue,
        args=args or (),
        kwargs=kwargs or {},
        exception=exception,
        retry_count=retry_count,
        einfo=einfo,
    )
