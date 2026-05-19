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
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from typing import Any, Optional

from celery import Task
from celery.utils.log import get_task_logger
from sqlalchemy import delete, select

from app.workers.celery_app import celery_app

logger = get_task_logger(__name__)


# ── Base Task with retry defaults ─────────────────────────────────────────────

class BaseTask(Task):
    """All tasks inherit from this for consistent retry behaviour."""
    abstract = True
    max_retries = 3
    default_retry_delay = 30  # seconds


# ─────────────────────────────────────────────────────────────────────────────
# Task 1: Poll Gmail Inbox
# ─────────────────────────────────────────────────────────────────────────────

@celery_app.task(
    bind=True,
    base=BaseTask,
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
        return asyncio.run(_poll_inbox())
    except Exception as exc:
        logger.error(f"[poll_inbox_task] Fatal error: {exc}", exc_info=True)
        raise self.retry(exc=exc)


async def _poll_inbox() -> dict[str, Any]:
    from app.core.config import settings
    from app.db.session import AsyncSessionLocal
    from app.models.prospect import Prospect, ProspectStatus
    from app.services.gmail_service import GmailService
    from app.services.memory_service import get_or_create_thread, save_message

    processed = 0
    enqueued = 0
    errors = 0
    enqueued_thread_ids: set[int] = set()

    try:
        from app.services.gmail_service import fetch_unread_messages
        messages_raw = fetch_unread_messages(max_results=50)
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
    except Exception as exc:
        logger.error(f"[poll_inbox] Gmail fetch failed: {exc}")
        return {"processed": 0, "enqueued": 0, "errors": 1}

    if not messages:
        logger.info("[poll_inbox] No new messages.")
        return {"processed": 0, "enqueued": 0, "errors": 0}

    logger.info(f"[poll_inbox] Found {len(messages)} unread message(s)")

    async with AsyncSessionLocal() as db:
        for raw_msg in messages:
            try:
                sender_email: str = raw_msg.get("from", "").strip()
                body: str = raw_msg.get("body", "").strip()
                gmail_thread_id: str = raw_msg.get("thread_id", "")
                subject: str = raw_msg.get("subject", "(No Subject)")
                timestamp_str: Optional[str] = raw_msg.get("timestamp")
                gmail_msg_id: str = raw_msg.get("id", "")

                if not sender_email or not gmail_thread_id:
                    logger.warning(f"[poll_inbox] Skipping malformed message: {raw_msg}")
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

                # Find or create Prospect by sender email
                result = await db.execute(
                    select(Prospect).where(Prospect.email == sender_email)
                )
                prospect = result.scalar_one_or_none()

                if prospect is None:
                    # Auto-create unknown senders as prospects
                    prospect = Prospect(
                        name=_extract_name(sender_email),
                        email=sender_email,
                        timezone=settings.AGENT_DEFAULT_TIMEZONE,
                        status=ProspectStatus.CONTACTED.value,
                    )
                    db.add(prospect)
                    await db.flush()
                    logger.info(
                        f"[poll_inbox] New prospect created: {sender_email} "
                        f"id={prospect.id}"
                    )

                # Find or create EmailThread
                thread = await get_or_create_thread(
                    db,
                    gmail_thread_id=gmail_thread_id,
                    prospect_id=prospect.id,
                    subject=subject,
                )

                # Save inbound message (idempotent — check gmail_msg_id via raw_payload)
                already_saved = await _message_already_saved(db, thread.id, gmail_msg_id)
                if not already_saved:
                    await save_message(
                        db=db,
                        thread_id=thread.id,
                        sender=sender_email,
                        body=body,
                        timestamp=msg_ts,
                        raw_payload=raw_msg,
                    )
                    processed += 1

                # Enqueue agent run for this thread (deduplicate per poll cycle)
                if thread.id not in enqueued_thread_ids:
                    run_agent_task.apply_async(
                        kwargs={"thread_id": thread.id},
                        queue="agent",
                    )
                    enqueued_thread_ids.add(thread.id)
                    enqueued += 1
                    logger.info(
                        f"[poll_inbox] Enqueued agent run for thread {thread.id}"
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
                logger.error(
                    f"[poll_inbox] Error processing message: {msg_exc}", exc_info=True
                )
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
    base=BaseTask,
    name="app.workers.tasks.run_agent_task",
    queue="agent",
    # Soft timeout: 5 min per run; hard kill at 7 min
    soft_time_limit=300,
    time_limit=420,
)
def run_agent_task(self, thread_id: int) -> dict[str, Any]:
    """
    Run the LangGraph agent for a specific email thread.

    Args:
        thread_id: DB primary key of the EmailThread.

    Returns:
        Summary dict: {thread_id, intent, reply_sent, error}
    """
    logger.info(f"[run_agent_task] thread_id={thread_id}")
    try:
        result = asyncio.run(_run_agent(thread_id))
        logger.info(
            f"[run_agent_task] Done | thread={thread_id} "
            f"intent={result.get('intent')} reply_sent={result.get('reply_sent')}"
        )
        return result
    except Exception as exc:
        logger.error(f"[run_agent_task] thread={thread_id} error: {exc}", exc_info=True)
        raise self.retry(exc=exc, countdown=60)


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
    base=BaseTask,
    name="app.workers.tasks.send_outreach_task",
    queue="gmail",
    soft_time_limit=120,
    time_limit=180,
)
def send_outreach_task(self, prospect_id: int) -> dict[str, Any]:
    """
    Generate and send a cold outreach email for a prospect.

    Flow:
      1. Load prospect from DB.
      2. Load active AgentConfig for gig description and tone.
      3. Generate cold outreach email via LLM.
      4. Send via Gmail.
      5. Create EmailThread + save outbound message in DB.
      6. Update prospect status to CONTACTED.

    Args:
        prospect_id: DB primary key of the Prospect.

    Returns:
        Summary dict: {prospect_id, gmail_thread_id, subject, sent}
    """
    logger.info(f"[send_outreach_task] prospect_id={prospect_id}")
    try:
        result = asyncio.run(_send_outreach(prospect_id))
        logger.info(
            f"[send_outreach_task] Done | prospect={prospect_id} "
            f"sent={result.get('sent')} thread={result.get('gmail_thread_id')}"
        )
        return result
    except Exception as exc:
        logger.error(
            f"[send_outreach_task] prospect={prospect_id} error: {exc}", exc_info=True
        )
        raise self.retry(exc=exc, countdown=120)


async def _send_outreach(prospect_id: int) -> dict[str, Any]:
    from app.core.config import settings
    from app.db.session import AsyncSessionLocal
    from app.models.prospect import Prospect, ProspectStatus
    from app.repositories.config_repo import get_or_create_default
    from app.services.gmail_service import GmailService
    from app.services.llm_service import generate_outreach_email
    from app.services.memory_service import get_or_create_thread, save_message

    async with AsyncSessionLocal() as db:
        # Load prospect
        result = await db.execute(
            select(Prospect).where(Prospect.id == prospect_id)
        )
        prospect = result.scalar_one_or_none()
        if not prospect:
            raise ValueError(f"Prospect {prospect_id} not found")

        # Load config
        config = await get_or_create_default(db)

        # Generate outreach via LLM
        draft = generate_outreach_email(
            prospect_name=prospect.name,
            gig_description=config.gig_description or "an exciting opportunity",
            tone=config.tone,
        )

        # Send via Gmail
        from app.services.gmail_service import send_email as gmail_send
        sent_msg = gmail_send(
            to=prospect.email,
            subject=draft.subject,
            body=draft.body,
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
            body=draft.body,
            timestamp=datetime.now(dt_timezone.utc),
            raw_payload={"gmail_msg_id": gmail_msg_id, "type": "outreach"},
        )

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
    base=BaseTask,
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
        return asyncio.run(_cleanup_old_logs(days))
    except Exception as exc:
        logger.error(f"[cleanup_old_logs] Error: {exc}", exc_info=True)
        raise self.retry(exc=exc)


async def _cleanup_old_logs(days: int) -> dict[str, Any]:
    from app.db.session import AsyncSessionLocal
    from app.models.agent_run import AgentRun

    cutoff = datetime.now(dt_timezone.utc) - timedelta(days=days)

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            delete(AgentRun).where(AgentRun.created_at < cutoff)
        )
        deleted = result.rowcount
        await db.commit()

    logger.info(f"[cleanup_old_logs] Deleted {deleted} old log row(s)")
    return {"deleted_count": deleted}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_name(email: str) -> str:
    """Best-effort name from email address (e.g. 'john.doe@...' → 'John Doe')."""
    local = email.split("@")[0]
    name = local.replace(".", " ").replace("_", " ").replace("-", " ").title()
    return name or email


async def _message_already_saved(db: Any, thread_id: int, gmail_msg_id: str) -> bool:
    """
    Check if a Gmail message has already been saved to prevent duplicate entries.
    Looks for gmail_msg_id in the raw_payload JSONB column.
    """
    if not gmail_msg_id:
        return False
    from sqlalchemy import cast
    from sqlalchemy.dialects.postgresql import JSONB
    from app.models.email_message import EmailMessage
    result = await db.execute(
        select(EmailMessage.id).where(
            EmailMessage.thread_id == thread_id,
            EmailMessage.raw_payload.op("->>")(  # JSONB field access
                "id"
            ) == gmail_msg_id,
        ).limit(1)
    )
    return result.scalar_one_or_none() is not None
