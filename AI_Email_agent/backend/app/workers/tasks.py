"""
Celery Tasks
─────────────
All background tasks for the Email Wake-Up Agent.

Tasks:
  poll_inbox_task      — Periodic: polls Gmail for new replies, routes to agent
  run_agent_task       — On-demand: runs the LangGraph agent for one thread
  send_outreach_task   — On-demand: generates + sends cold outreach email
  cleanup_old_logs     — Periodic: prunes AgentRun logs older than 30 days

Async pattern:
  Celery workers are synchronous. Async DB/LLM work is bridged via
  asyncio.run() inside each task. A fresh event loop is created per task.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from typing import Any, Optional

# asyncpg requires SelectorEventLoop on Windows
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _run(coro):
    """
    Run an async coroutine from a sync Celery task using a brand-new
    event loop each time. This avoids the 'Future attached to a different
    loop' error that occurs with --pool=solo when asyncpg connections
    linger between calls.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

from celery import Task
from celery.utils.log import get_task_logger
from sqlalchemy import delete, func, select

from app.services.gmail_lock import (
    acquire_message_lock,
    acquire_outreach_lock,
    acquire_thread_lock,
    clear_agent_queued,
    is_agent_queued_or_running,
    mark_agent_queued,
    release_outreach_lock,
    release_thread_lock,
)
from app.workers.celery_app import AgentTask, DefaultTask, GmailTask, celery_app, exponential_backoff

logger = get_task_logger(__name__)
# Per-task base classes (GmailTask, AgentTask, DefaultTask) and
# exponential_backoff() are defined in celery_app.py and imported above.


# ─────────────────────────────────────────────────────────────────────────────
# Task 1: Poll Gmail Inbox
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(
    bind=True,
    base=GmailTask,
    name="app.workers.tasks.poll_inbox_task",
    queue="gmail",
)
def poll_inbox_task(self) -> dict[str, Any]:
    """
    Celery Beat task — runs every GMAIL_POLL_INTERVAL_SECONDS.

    Flow:
      1. Fetch all unread messages from Gmail.
      2. For each message, find or create Prospect + EmailThread in DB.
      3. Save the inbound message to DB.
      4. Enqueue run_agent_task for each thread that has new activity.
      5. Mark Gmail messages as read (label modification).

    Returns:
        Summary dict: {processed, enqueued, errors}
    """
    logger.info("[poll_inbox_task] Starting Gmail poll")
    try:
        return _run(_poll_inbox())
    except Exception as exc:
        logger.exception("[poll_inbox_task] Fatal error")
        raise self.retry(exc=exc, countdown=exponential_backoff(self.request.retries))


async def _poll_inbox() -> dict[str, Any]:
    from app.core.config import settings
    from app.db.session import CelerySessionLocal
    from app.models.prospect import Prospect, ProspectStatus
    from app.services.memory_service import get_or_create_thread, save_message

    processed = 0
    enqueued = 0
    errors = 0
    enqueued_thread_ids: set[int] = set()

    # ── Build targeted Gmail query from all known prospect emails ─────────────
    # Using Gmail's q= parameter so we make ONE API call and only receive
    # messages that can actually match a prospect — newsletters/notifications
    # are automatically excluded.  Much faster and more reliable than fetching
    # the generic "top 50 unread" which gets swamped by junk mail.
    try:
        from app.services.gmail_service import fetch_unread_messages

        # Load all prospect emails from DB
        async with CelerySessionLocal() as db:
            prospect_result = await db.execute(
                select(Prospect).where(Prospect.email.isnot(None))
            )
            all_prospects = prospect_result.scalars().all()
            prospect_emails = [
                p.email.strip().lower() for p in all_prospects if p.email and "@" in p.email
            ]

        if not prospect_emails:
            logger.info("[poll_inbox] No prospects configured — nothing to poll.")
            return {"processed": 0, "enqueued": 0, "errors": 0}

        # Gmail q= "from:a@b.com OR from:c@d.com ... newer_than:3d"
        # We use a 3-day recency window instead of "is:unread" so that messages
        # the user already opened in Gmail are still picked up.  Idempotency is
        # guaranteed by _message_already_saved() checking gmail_msg_id in our DB.
        # Gmail API has a URL length limit; chunk at 30 emails per query.
        messages_raw = []
        seen_msg_ids: set[str] = set()
        chunk_size = 30
        for i in range(0, len(prospect_emails), chunk_size):
            chunk = prospect_emails[i : i + chunk_size]
            q_parts = [f"from:{e}" for e in chunk]
            q = "(" + " OR ".join(q_parts) + ") newer_than:3d"
            chunk_msgs = fetch_unread_messages(max_results=100, q=q)
            for m in chunk_msgs:
                if m.message_id not in seen_msg_ids:
                    messages_raw.append(m)
                    seen_msg_ids.add(m.message_id)

    except Exception as exc:
        logger.error(f"[poll_inbox] Gmail fetch failed: {exc}")
        # Re-raise so Celery applies its retry / backoff policy and eventually
        # routes the task to the DLQ.  A swallowed exception marks the task
        # SUCCESS which hides inbox outages from monitoring.
        raise

    # Normalise ParsedMessage dataclasses to plain dicts
    messages = [
        {
            "id":        m.message_id,
            "thread_id": m.thread_id,
            "from":      m.sender,
            "subject":   m.subject,
            "body":      m.body,
            "timestamp": m.timestamp.isoformat(),
        }
        for m in messages_raw
    ]

    if not messages:
        logger.info("[poll_inbox] No new replies from known prospects in the last 3 days.")
        return {"processed": 0, "enqueued": 0, "errors": 0}

    logger.info(f"[poll_inbox] Found {len(messages)} message(s) from known prospects")

    async with CelerySessionLocal() as db:
        for raw_msg in messages:
            try:
                sender_raw: str = raw_msg.get("from", "").strip()
                # Extract bare email from "Name <email>" or "email" format
                sender_email: str = _parse_email_address(sender_raw)
                body: str = raw_msg.get("body", "").strip()
                gmail_thread_id: str = raw_msg.get("thread_id", "")
                subject: str = raw_msg.get("subject", "(No Subject)")
                timestamp_str: Optional[str] = raw_msg.get("timestamp")
                gmail_msg_id: str = raw_msg.get("id", "")

                if not sender_email or not gmail_thread_id:
                    logger.warning(f"[poll_inbox] Skipping malformed message: {raw_msg}")
                    continue

                # ── Distributed lock — skip if another worker is already ──────
                # processing this exact Gmail message (NX Redis key, 300s TTL).
                if not acquire_message_lock(gmail_msg_id):
                    logger.info(
                        f"[poll_inbox] Message {gmail_msg_id} already locked — "
                        "skipping (concurrent worker processing)"
                    )
                    continue

                # Parse timestamp
                try:
                    msg_ts = (
                        datetime.fromisoformat(timestamp_str)
                        if timestamp_str
                        else datetime.now(dt_timezone.utc)
                    )
                    if msg_ts.tzinfo is None:
                        msg_ts = msg_ts.replace(tzinfo=dt_timezone.utc)
                except Exception:
                    msg_ts = datetime.now(dt_timezone.utc)

                # Look up existing prospect — only process known contacts.
                # Unknown senders (newsletters, notifications, etc.) are
                # intentionally ignored; prospects must be added manually.
                result = await db.execute(
                    select(Prospect).where(
                        func.lower(Prospect.email) == sender_email
                    )
                )
                prospect = result.scalar_one_or_none()

                if prospect is None:
                    logger.debug(
                        f"[poll_inbox] Unknown sender '{sender_email}' — skipping "
                        "(add them manually via the Prospects page to enable agent processing)"
                    )
                    continue

                # Find or create EmailThread
                thread = await get_or_create_thread(
                    db,
                    gmail_thread_id=gmail_thread_id,
                    prospect_id=prospect.id,
                    subject=subject,
                )

                # ── Indexed idempotency check using gmail_message_id column ───
                # This replaces the old JSONB scan (raw_payload->>'id') with
                # a fast B-tree index lookup on the new dedicated column.
                already_saved = await _message_already_saved(db, gmail_msg_id)
                if not already_saved:
                    await save_message(
                        db=db,
                        thread_id=thread.id,
                        sender=sender_email,
                        body=body,
                        timestamp=msg_ts,
                        raw_payload=raw_msg,
                        gmail_message_id=gmail_msg_id,
                    )
                    processed += 1

                # Enqueue agent for new replies OR when a saved prospect message
                # still has no agent response (worker was down, task failed, etc.).
                needs_agent = (
                    not already_saved
                    or await _thread_awaiting_agent_reply(db, thread.id)
                )
                if needs_agent:
                    if (
                        thread.id not in enqueued_thread_ids
                        and not is_agent_queued_or_running(thread.id)
                    ):
                        run_agent_task.apply_async(
                            kwargs={"thread_id": thread.id},
                            queue="agent",
                        )
                        mark_agent_queued(thread.id)
                        enqueued_thread_ids.add(thread.id)
                        enqueued += 1
                        logger.info(
                            f"[poll_inbox] Enqueued agent run for thread {thread.id}"
                        )
                    else:
                        logger.debug(
                            f"[poll_inbox] Agent run already queued/running for "
                            f"thread {thread.id} — skipping duplicate enqueue"
                        )

                # Mark message as read in Gmail
                try:
                    from app.services.gmail_service import mark_as_read
                    mark_as_read(gmail_msg_id)
                except Exception as mark_err:
                    logger.warning(
                        f"[poll_inbox] Failed to mark {gmail_msg_id} as read: {mark_err}"
                    )

            except Exception as msg_exc:
                logger.exception("[poll_inbox] Error processing message")
                errors += 1
                continue

        await db.commit()

    logger.info(
        f"[poll_inbox] Done | processed={processed} enqueued={enqueued} errors={errors}"
    )
    return {"processed": processed, "enqueued": enqueued, "errors": errors}


# ─────────────────────────────────────────────────────────────────────────────
# Task 2: Run Agent for a Thread
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(
    bind=True,
    base=AgentTask,
    name="app.workers.tasks.run_agent_task",
    queue="agent",
    # Soft timeout: 5 min per run; hard kill at 7 min
    soft_time_limit=300,
    time_limit=420,
)
def run_agent_task(self, thread_id: int) -> dict[str, Any]:
    """
    Run the LangGraph agent for a specific email thread.

    A Redis thread lock (lock:thread:{thread_id}, 300 s TTL) prevents two
    concurrent workers from running the agent on the same thread simultaneously.
    If the lock is already held the task returns immediately with a "locked"
    status so the calling thread's message is still counted as processed.

    Args:
        thread_id: DB primary key of the EmailThread.

    Returns:
        Summary dict: {thread_id, intent, reply_sent, error}
    """
    logger.info(f"[run_agent_task] thread_id={thread_id}")

    if not acquire_thread_lock(thread_id):
        logger.info(
            f"[run_agent_task] thread={thread_id} already locked — "
            "skipping (concurrent agent run in progress)"
        )
        return {
            "thread_id": thread_id,
            "intent": None,
            "reply_sent": False,
            "error": "locked",
        }

    try:
        result = _run(_run_agent(thread_id))
        if not result.get("error"):
            clear_agent_queued(thread_id)
        logger.info(
            f"[run_agent_task] Done | thread={thread_id} "
            f"intent={result.get('intent')} reply_sent={result.get('reply_sent')}"
        )
        return result
    except Exception as exc:
        logger.exception(f"[run_agent_task] thread={thread_id} failed")
        raise self.retry(
            exc=exc,
            countdown=exponential_backoff(self.request.retries, base=60),
        )
    finally:
        release_thread_lock(thread_id)


async def _run_agent(thread_id: int) -> dict[str, Any]:
    from app.agents.graph import run_agent

    final_state = await run_agent(thread_id)
    return {
        "thread_id": thread_id,
        "intent":     final_state.get("intent"),
        "reply_sent": final_state.get("reply_sent", False),
        "error":      final_state.get("error"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Task 3: Send Cold Outreach Email
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(
    bind=True,
    base=GmailTask,
    name="app.workers.tasks.send_outreach_task",
    queue="gmail",
    soft_time_limit=120,
    time_limit=180,
)
def send_outreach_task(self, prospect_id: int) -> dict[str, Any]:
    """
    Generate and send a cold outreach email for a prospect.

    Phase 6 — Idempotency:
      An idempotency lock (``idempotency:outreach:{prospect_id}``, 24 h TTL)
      is acquired BEFORE calling Gmail.  If the task is retried after the
      email was sent but the DB write failed, the lock is already set and
      the duplicate send is skipped.  If Gmail fails (email never sent),
      the lock is released so the next retry can attempt the send again.

    Flow:
      1. Acquire outreach idempotency lock — abort if already held.
      2. Load prospect from DB.
      3. Load active AgentConfig for gig description and tone.
      4. Generate cold outreach email via LLM.
      5. Send via Gmail.
      6. Create EmailThread + save outbound message in DB.
      7. Update prospect status to CONTACTED.

    Args:
        prospect_id: DB primary key of the Prospect.

    Returns:
        Summary dict: {prospect_id, gmail_thread_id, subject, sent}
    """
    logger.info(f"[send_outreach_task] prospect_id={prospect_id}")

    # ── Idempotency guard ─────────────────────────────────────────────────────
    if not acquire_outreach_lock(prospect_id):
        logger.warning(
            f"[send_outreach_task] Idempotency key already set for "
            f"prospect_id={prospect_id} — outreach was already sent; skipping."
        )
        return {
            "prospect_id": prospect_id,
            "sent": False,
            "skipped": True,
            "reason": "idempotency_key_exists",
        }

    try:
        result = _run(_send_outreach(prospect_id))
        logger.info(
            f"[send_outreach_task] Done | prospect={prospect_id} "
            f"sent={result.get('sent')} thread={result.get('gmail_thread_id')}"
        )
        return result
    except Exception as exc:
        logger.exception(f"[send_outreach_task] prospect={prospect_id} failed")
        # Release idempotency lock so the next retry can attempt the send.
        # Only release if the email was NOT sent (we can't know for certain,
        # but if we're in this except block the overall task failed — the DB
        # write may have failed after a successful send, in which case a
        # retry with the lock released would double-send.  We accept this
        # edge-case trade-off here; the operator DLQ makes it inspectable.)
        release_outreach_lock(prospect_id)
        raise self.retry(
            exc=exc,
            countdown=exponential_backoff(self.request.retries),
        )


async def _send_outreach(prospect_id: int) -> dict[str, Any]:
    from app.core.config import settings
    from app.db.session import CelerySessionLocal
    from app.models.prospect import Prospect, ProspectStatus
    from app.repositories.config_repo import get_or_create_default
    from app.services.llm_service import generate_outreach_email
    from app.services.memory_service import get_or_create_thread, save_message

    async with CelerySessionLocal() as db:
        # Load prospect
        result = await db.execute(
            select(Prospect).where(Prospect.id == prospect_id)
        )
        prospect = result.scalar_one_or_none()
        if not prospect:
            raise ValueError(f"Prospect {prospect_id} not found")

        # Load config
        config = await get_or_create_default(db)

        # Generate outreach via LLM — use recruiter identity from config (Phase 8)
        recruiter_name  = getattr(config, "recruiter_name",  "Alex")
        recruiter_title = getattr(config, "recruiter_title", "HR Recruiter")
        recruiter_sig   = (getattr(config, "recruiter_signature", "") or "").strip()

        # LLM call is sync — run in thread pool so event loop stays free
        draft = await asyncio.to_thread(
            generate_outreach_email,
            prospect_name=prospect.name,
            gig_description=config.gig_description or "an exciting opportunity",
            tone=config.tone,
            agent_name=recruiter_name,
            agent_title=recruiter_title,
        )

        # Append recruiter signature before sending (Phase 8)
        body = draft.body
        if recruiter_sig:
            body = f"{body}\n\n{recruiter_sig}"

        from app.services.gmail_service import send_email as gmail_send
        # Gmail client is sync — run in thread pool
        sent_msg = await asyncio.to_thread(
            gmail_send,
            to=prospect.email,
            subject=draft.subject,
            body=body,
        )

        gmail_thread_id: str = sent_msg.thread_id
        gmail_msg_id: str = sent_msg.message_id

        # Create thread + save outbound message
        thread = await get_or_create_thread(
            db,
            gmail_thread_id=gmail_thread_id,
            prospect_id=prospect.id,
            subject=draft.subject,
        )
        await save_message(
            db=db,
            thread_id=thread.id,
            sender="agent",
            body=body,
            timestamp=datetime.now(dt_timezone.utc),
            raw_payload={"gmail_msg_id": gmail_msg_id, "type": "outreach"},
            gmail_message_id=gmail_msg_id,
        )

        # Stamp when this outreach was sent — used by follow-up scheduler
        thread.last_outreach_at = datetime.now(dt_timezone.utc)
        db.add(thread)

        # Update prospect status
        prospect.status = ProspectStatus.CONTACTED.value
        db.add(prospect)
        await db.commit()

    logger.info(
        f"[send_outreach] Sent to {prospect.email} | "
        f"subject='{draft.subject}' thread={gmail_thread_id}"
    )
    return {
        "prospect_id":    prospect_id,
        "gmail_thread_id": gmail_thread_id,
        "subject":        draft.subject,
        "sent":           True,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Task 4: Cleanup Old Logs
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(
    bind=True,
    base=DefaultTask,
    name="app.workers.tasks.cleanup_old_logs",
    queue="default",
)
def cleanup_old_logs(self, days: int = 30) -> dict[str, Any]:
    """
    Celery Beat task — runs daily at 02:00 UTC.

    Deletes AgentRun log rows older than `days` days to keep the table lean.

    Args:
        days: Retention window in days (default: 30).

    Returns:
        {deleted_count}
    """
    logger.info(f"[cleanup_old_logs] Pruning logs older than {days} days")
    try:
        return _run(_cleanup_old_logs(days))
    except Exception as exc:
        logger.exception("[cleanup_old_logs] Fatal error")
        raise self.retry(exc=exc, countdown=exponential_backoff(self.request.retries))


async def _cleanup_old_logs(days: int) -> dict[str, Any]:
    from app.db.session import CelerySessionLocal
    from app.models.agent_run import AgentRun

    cutoff = datetime.now(dt_timezone.utc) - timedelta(days=days)

    async with CelerySessionLocal() as db:
        result = await db.execute(
            delete(AgentRun).where(AgentRun.created_at < cutoff)
        )
        deleted = result.rowcount
        await db.commit()

    logger.info(f"[cleanup_old_logs] Deleted {deleted} old log row(s)")
    return {"deleted_count": deleted}


# ─────────────────────────────────────────────────────────────────────────────
# Task 5: Follow-Up Silent Prospects
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(
    bind=True,
    base=GmailTask,
    name="app.workers.tasks.follow_up_silent_prospects",
    queue="gmail",
    soft_time_limit=300,
    time_limit=420,
)
def follow_up_silent_prospects(self) -> dict[str, Any]:
    """
    Celery Beat task — runs daily at 08:00 UTC.

    Finds all contacted threads that have not received a reply within
    `follow_up_days` days and still have remaining follow-up attempts.
    Generates a personalised follow-up email via LLM and sends it.

    Returns:
        {checked, followed_up, skipped, errors}
    """
    logger.info("[follow_up_silent_prospects] Starting silent prospect scan")
    try:
        return _run(_follow_up_silent_prospects())
    except Exception as exc:
        logger.exception("[follow_up_silent_prospects] Fatal error")
        raise self.retry(
            exc=exc,
            countdown=exponential_backoff(self.request.retries, base=60),
        )


async def _follow_up_silent_prospects() -> dict[str, Any]:
    from datetime import timedelta

    import json
    from sqlalchemy import and_, or_

    from app.core.config import settings
    from app.db.session import CelerySessionLocal
    from app.models.email_thread import EmailThread, ThreadStatus
    from app.models.prospect import Prospect, ProspectStatus
    from app.repositories.config_repo import get_or_create_default
    from app.services.gmail_service import reply_to_thread, send_email as gmail_send
    from app.services.llm_service import generate_followup_email
    from app.services.memory_service import save_message

    checked = 0
    followed_up = 0
    skipped = 0
    errors = 0

    async with CelerySessionLocal() as db:
        config = await get_or_create_default(db)

        # ── Parse follow-up cadence ───────────────────────────────────────────
        # Phase 8: cadence is a JSON list of day-offsets per follow-up step.
        # Fall back to the legacy single follow_up_days if cadence is absent/corrupt.
        raw_cadence = getattr(config, "follow_up_cadence", None) or "[1, 3, 7]"
        try:
            cadence: list[int] = json.loads(raw_cadence)
            if not isinstance(cadence, list) or not cadence:
                raise ValueError
        except Exception:
            cadence = [getattr(config, "follow_up_days", 3)]

        max_follow_ups: int = min(
            getattr(config, "max_follow_ups", len(cadence)),
            len(cadence),
        )

        # The shortest cadence interval is the minimum days we need to wait
        # before ANY follow-up is due.  Use this as the DB filter cutoff so
        # we fetch every thread that COULD be due, then filter per-thread below.
        min_cadence_days = min(cadence)
        earliest_cutoff = datetime.now(dt_timezone.utc) - timedelta(days=min_cadence_days)

        result = await db.execute(
            select(EmailThread).where(
                and_(
                    EmailThread.status.in_([
                        ThreadStatus.PENDING.value,
                        ThreadStatus.WAITING.value,
                    ]),
                    EmailThread.last_outreach_at <= earliest_cutoff,
                    EmailThread.last_outreach_at.isnot(None),
                    EmailThread.follow_up_count < max_follow_ups,
                )
            )
        )
        candidate_threads = result.scalars().all()

    # ── Per-thread cadence check ──────────────────────────────────────────────
    # Filter to threads where the specific cadence step for this thread is due.
    now = datetime.now(dt_timezone.utc)
    silent_threads = []
    for thread in candidate_threads:
        step_index = thread.follow_up_count  # 0 = first follow-up, 1 = second, …
        if step_index >= len(cadence):
            continue  # already exhausted cadence
        required_days = cadence[step_index]
        days_elapsed = (now - thread.last_outreach_at).days
        if days_elapsed >= required_days:
            silent_threads.append(thread)

    logger.info(
        f"[follow_up] Found {len(silent_threads)} due thread(s) "
        f"from {len(candidate_threads)} candidates "
        f"(cadence={cadence} max_follow_ups={max_follow_ups})"
    )

    for thread in silent_threads:
        checked += 1
        try:
            async with CelerySessionLocal() as db:
                config = await get_or_create_default(db)
                prospect_result = await db.execute(
                    select(Prospect).where(Prospect.id == thread.prospect_id)
                )
                prospect = prospect_result.scalar_one_or_none()
                if not prospect:
                    logger.warning(f"[follow_up] Prospect not found for thread {thread.id}")
                    skipped += 1
                    continue

                follow_up_number = thread.follow_up_count + 1
                days_since = (now - thread.last_outreach_at).days

                # Recruiter identity from config (Phase 8)
                recruiter_name  = getattr(config, "recruiter_name",  "Alex")
                recruiter_title = getattr(config, "recruiter_title", "HR Recruiter")
                recruiter_sig   = (getattr(config, "recruiter_signature", "") or "").strip()

                logger.info(
                    f"[follow_up] Sending follow-up #{follow_up_number} "
                    f"to {prospect.email} | thread={thread.id} "
                    f"days_since_last={days_since} cadence_step={follow_up_number-1}"
                )

                # LLM call is sync — run in thread pool
                draft = await asyncio.to_thread(
                    generate_followup_email,
                    prospect_name=prospect.name,
                    original_subject=thread.subject,
                    gig_description=config.gig_description or "an exciting opportunity",
                    days_since=days_since,
                    follow_up_number=follow_up_number,
                    max_follow_ups=max_follow_ups,
                    tone=config.tone,
                    agent_name=recruiter_name,
                    agent_title=recruiter_title,
                )

                # Append recruiter signature if configured
                body = draft.body
                if recruiter_sig:
                    body = f"{body}\n\n{recruiter_sig}"

                # Gmail client is sync — run in thread pool
                try:
                    sent_msg = await asyncio.to_thread(
                        reply_to_thread,
                        thread_id=thread.gmail_thread_id,
                        body=body,
                        subject=draft.subject,
                        to=prospect.email,
                    )
                except Exception as reply_exc:
                    logger.warning(
                        f"[follow_up] reply_to_thread failed ({reply_exc}) "
                        "— sending as new email"
                    )
                    sent_msg = await asyncio.to_thread(
                        gmail_send,
                        to=prospect.email,
                        subject=draft.subject,
                        body=body,
                    )

                await save_message(
                    db=db,
                    thread_id=thread.id,
                    sender="agent",
                    body=body,
                    timestamp=now,
                    raw_payload={
                        "type": "follow_up",
                        "follow_up_number": follow_up_number,
                        "cadence_step": follow_up_number - 1,
                        "gmail_msg_id": sent_msg.message_id,
                    },
                    gmail_message_id=sent_msg.message_id,
                )

                thread.follow_up_count = follow_up_number
                thread.last_outreach_at = now
                db.add(thread)

                if follow_up_number >= max_follow_ups:
                    thread.status = ThreadStatus.CLOSED.value
                    prospect.status = ProspectStatus.DECLINED.value
                    db.add(prospect)
                    logger.info(
                        f"[follow_up] Cadence exhausted — closing thread {thread.id} "
                        f"prospect={prospect.email}"
                    )

                await db.commit()
                followed_up += 1

                logger.info(
                    f"[follow_up] Sent follow-up #{follow_up_number} "
                    f"to {prospect.email} | msg={sent_msg.message_id}"
                )

        except Exception as thread_exc:
            logger.exception(f"[follow_up] Error on thread {thread.id}")
            errors += 1
            continue

    logger.info(
        f"[follow_up] Done | checked={checked} followed_up={followed_up} "
        f"skipped={skipped} errors={errors}"
    )
    return {
        "checked": checked,
        "followed_up": followed_up,
        "skipped": skipped,
        "errors": errors,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_name(email: str) -> str:
    """Best-effort name from email address (e.g. 'john.doe@...' → 'John Doe')."""
    local = email.split("@")[0]
    name = local.replace(".", " ").replace("_", " ").replace("-", " ").title()
    return name or email


def _parse_email_address(raw: str) -> str:
    """
    Extract bare email address from Gmail 'From' header.

    Examples:
        'John Doe <john@example.com>'  → 'john@example.com'
        'john@example.com'             → 'john@example.com'
        'John Doe <john@example.com> ' → 'john@example.com'
    """
    raw = raw.strip()
    if "<" in raw and ">" in raw:
        return raw[raw.index("<") + 1 : raw.index(">")].strip().lower()
    return raw.lower()


async def _thread_awaiting_agent_reply(db: Any, thread_id: int) -> bool:
    """
    True when the latest message in the thread is from the prospect (not the agent),
    meaning the agent still needs to read and respond.
    """
    from app.models.email_message import EmailMessage

    result = await db.execute(
        select(EmailMessage.sender)
        .where(EmailMessage.thread_id == thread_id)
        .order_by(EmailMessage.timestamp.desc())
        .limit(1)
    )
    latest_sender = result.scalar_one_or_none()
    if not latest_sender:
        return False
    return latest_sender.strip().lower() != "agent"


async def _message_already_saved(db: Any, gmail_msg_id: str) -> bool:
    """
    Check if a Gmail message has already been saved to prevent duplicate entries.

    Uses the indexed ``gmail_message_id`` column for an O(log n) lookup,
    replacing the old JSONB scan which could not use a B-tree index.

    The check is intentionally scope-free (no thread_id filter) so that
    a duplicate message is caught even if it somehow appears under a different
    thread (e.g. Gmail thread-split edge case).
    """
    if not gmail_msg_id:
        return False
    from app.models.email_message import EmailMessage
    result = await db.execute(
        select(EmailMessage.id).where(
            EmailMessage.gmail_message_id == gmail_msg_id,
        ).limit(1)
    )
    return result.scalar_one_or_none() is not None
