"""
Memory Service
───────────────
Persistent conversation memory backed by PostgreSQL.

Responsibilities:
  - Save incoming and outgoing email messages
  - Load full thread history formatted for LLM context
  - Load negotiation state for a thread
  - Load meeting state for a thread
  - Record and update AgentRun execution logs
  - Build a complete memory snapshot for the LangGraph agent state

The agent state is reconstructed from the DB on every run — no in-process
state is kept. This makes the agent stateless, restartable, and fault-tolerant.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from datetime import timezone as dt_timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.models.agent_run import AgentRun, RunStatus
from app.models.email_message import EmailMessage
from app.models.email_thread import EmailThread, ThreadStatus
from app.models.meeting import Meeting, MeetingStatus
from app.models.negotiation import Negotiation, NegotiationStatus
from app.models.prospect import Prospect


# ── Memory Snapshot ───────────────────────────────────────────────────────────

@dataclass
class ThreadMemory:
    """
    Complete memory snapshot for a thread — passed into the LangGraph agent state.
    Loaded fresh from DB at the start of each agent run.
    """
    thread_id: int
    gmail_thread_id: str
    prospect_id: int
    prospect_name: str
    prospect_email: str
    prospect_timezone: str
    subject: str
    thread_status: str

    # Conversation history
    messages: list[dict[str, Any]] = field(default_factory=list)
    conversation_text: str = ""         # formatted for LLM prompts

    # Negotiation memory
    negotiation_status: Optional[str] = None
    max_budget: Optional[float] = None
    current_offer: Optional[float] = None

    # Meeting memory
    meeting_status: Optional[str] = None
    google_event_id: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    reschedule_count: int = 0


# ── Message Persistence ───────────────────────────────────────────────────────

async def save_message(
    db: AsyncSession,
    thread_id: int,
    sender: str,
    body: str,
    timestamp: datetime,
    intent: Optional[str] = None,
    raw_payload: Optional[dict[str, Any]] = None,
) -> EmailMessage:
    """
    Persist an email message (inbound or outbound) to the DB.

    Args:
        db:          Async DB session.
        thread_id:   DB ID of the parent EmailThread.
        sender:      Sender email address or "agent".
        body:        Plain-text email body.
        timestamp:   Message timestamp (timezone-aware).
        intent:      Detected intent label (optional, set after classification).
        raw_payload: Raw Gmail API payload dict (optional).

    Returns:
        Saved EmailMessage ORM instance.
    """
    msg = EmailMessage(
        thread_id=thread_id,
        sender=sender,
        body=body,
        timestamp=timestamp,
        intent=intent,
        raw_payload=raw_payload,
    )
    db.add(msg)
    await db.flush()
    logger.debug(
        f"Message saved | thread_id={thread_id} sender={sender} "
        f"intent={intent} id={msg.id}"
    )
    return msg


async def update_message_intent(
    db: AsyncSession,
    message_id: int,
    intent: str,
) -> None:
    """Update the intent label of an already-saved message."""
    result = await db.execute(
        select(EmailMessage).where(EmailMessage.id == message_id)
    )
    msg = result.scalar_one_or_none()
    if msg:
        msg.intent = intent
        db.add(msg)
        await db.flush()
        logger.debug(f"Message {message_id} intent updated to '{intent}'")


# ── Thread History ────────────────────────────────────────────────────────────

async def load_thread_history(
    db: AsyncSession,
    thread_id: int,
) -> list[EmailMessage]:
    """
    Load all messages for a thread in chronological order.

    Returns:
        List of EmailMessage ORM objects, oldest first.
    """
    result = await db.execute(
        select(EmailMessage)
        .where(EmailMessage.thread_id == thread_id)
        .order_by(EmailMessage.timestamp.asc())
    )
    messages = result.scalars().all()
    logger.debug(f"Loaded {len(messages)} message(s) for thread {thread_id}")
    return list(messages)


def format_conversation_for_llm(messages: list[EmailMessage]) -> str:
    """
    Format a list of EmailMessage objects into a clean conversation string
    suitable for inclusion in an LLM prompt.

    Format:
        [2026-05-19 10:00 UTC] Prospect (rahul@example.com):
        Hi, I'm interested. What's the rate?

        [2026-05-19 10:05 UTC] Agent:
        Thank you for reaching out! The rate is...
    """
    if not messages:
        return "(No messages yet)"

    lines = []
    for msg in messages:
        ts = msg.timestamp.strftime("%Y-%m-%d %H:%M UTC") if msg.timestamp else "unknown time"
        label = "Agent" if msg.sender == "agent" else f"Prospect ({msg.sender})"
        intent_note = f" [intent: {msg.intent}]" if msg.intent else ""
        lines.append(f"[{ts}] {label}{intent_note}:")
        lines.append(msg.body.strip())
        lines.append("")

    return "\n".join(lines).strip()


# ── Thread Operations ─────────────────────────────────────────────────────────

async def get_or_create_thread(
    db: AsyncSession,
    gmail_thread_id: str,
    prospect_id: int,
    subject: str = "(No Subject)",
) -> EmailThread:
    """
    Fetch an existing thread by Gmail thread ID, or create a new one.

    Handles UniqueConstraint races by re-fetching on IntegrityError so a
    duplicate insert by a concurrent task never crashes the poll loop.

    Args:
        db:              Async DB session.
        gmail_thread_id: Gmail's thread ID string.
        prospect_id:     DB ID of the associated Prospect.
        subject:         Email subject line.

    Returns:
        EmailThread ORM instance.
    """
    from sqlalchemy.exc import IntegrityError

    result = await db.execute(
        select(EmailThread).where(EmailThread.gmail_thread_id == gmail_thread_id)
    )
    thread = result.scalar_one_or_none()

    if thread is None:
        try:
            thread = EmailThread(
                gmail_thread_id=gmail_thread_id,
                prospect_id=prospect_id,
                subject=subject,
                status=ThreadStatus.PENDING.value,
            )
            db.add(thread)
            await db.flush()
            logger.info(
                f"Thread created | gmail_thread_id={gmail_thread_id} "
                f"prospect_id={prospect_id} id={thread.id}"
            )
        except IntegrityError:
            # Another concurrent task already inserted this thread — roll back
            # the savepoint and re-fetch the existing row.
            await db.rollback()
            result = await db.execute(
                select(EmailThread).where(EmailThread.gmail_thread_id == gmail_thread_id)
            )
            thread = result.scalar_one()
            logger.debug(
                f"Thread race resolved (re-fetched) | gmail_thread_id={gmail_thread_id} id={thread.id}"
            )

    return thread


async def update_thread_status(
    db: AsyncSession,
    thread_id: int,
    status: str,
) -> None:
    """Update the status of an email thread."""
    result = await db.execute(
        select(EmailThread).where(EmailThread.id == thread_id)
    )
    thread = result.scalar_one_or_none()
    if thread:
        thread.status = status
        db.add(thread)
        await db.flush()
        logger.debug(f"Thread {thread_id} status → '{status}'")


# ── Full Memory Snapshot ──────────────────────────────────────────────────────

async def load_thread_memory(
    db: AsyncSession,
    thread_id: int,
) -> Optional[ThreadMemory]:
    """
    Load a complete ThreadMemory snapshot for a given thread DB ID.
    This is the primary method called by the LangGraph agent at startup.

    Loads:
      - Thread + prospect details
      - All messages (formatted as conversation text)
      - Negotiation state
      - Meeting state

    Args:
        db:        Async DB session.
        thread_id: DB ID of the EmailThread.

    Returns:
        ThreadMemory dataclass, or None if thread not found.
    """
    # Load thread
    thread_result = await db.execute(
        select(EmailThread).where(EmailThread.id == thread_id)
    )
    thread = thread_result.scalar_one_or_none()
    if not thread:
        logger.warning(f"Thread {thread_id} not found in DB")
        return None

    # Load prospect
    prospect_result = await db.execute(
        select(Prospect).where(Prospect.id == thread.prospect_id)
    )
    prospect = prospect_result.scalar_one_or_none()
    if not prospect:
        logger.warning(f"Prospect {thread.prospect_id} not found for thread {thread_id}")
        return None

    # Load messages
    messages = await load_thread_history(db, thread_id)
    conversation_text = format_conversation_for_llm(messages)

    # Serialize messages for agent state
    messages_dicts = [
        {
            "id": m.id,
            "sender": m.sender,
            "body": m.body,
            "intent": m.intent,
            "timestamp": m.timestamp.isoformat() if m.timestamp else None,
        }
        for m in messages
    ]

    # Load negotiation
    neg_result = await db.execute(
        select(Negotiation).where(Negotiation.thread_id == thread_id)
    )
    negotiation = neg_result.scalar_one_or_none()

    # Load meeting
    meet_result = await db.execute(
        select(Meeting).where(Meeting.thread_id == thread_id)
    )
    meeting = meet_result.scalar_one_or_none()

    memory = ThreadMemory(
        thread_id=thread.id,
        gmail_thread_id=thread.gmail_thread_id,
        prospect_id=prospect.id,
        prospect_name=prospect.name,
        prospect_email=prospect.email,
        prospect_timezone=prospect.timezone,
        subject=thread.subject,
        thread_status=thread.status,
        messages=messages_dicts,
        conversation_text=conversation_text,
        # Negotiation
        negotiation_status=negotiation.status if negotiation else None,
        max_budget=negotiation.max_budget if negotiation else None,
        current_offer=negotiation.current_offer if negotiation else None,
        # Meeting
        meeting_status=meeting.status if meeting else None,
        google_event_id=meeting.google_event_id if meeting else None,
        scheduled_at=meeting.scheduled_at if meeting else None,
        reschedule_count=meeting.reschedule_count if meeting else 0,
    )

    logger.info(
        f"Memory loaded | thread={thread_id} messages={len(messages)} "
        f"neg={memory.negotiation_status} meeting={memory.meeting_status} "
        f"reschedules={memory.reschedule_count}"
    )
    return memory


# ── Agent Run Logging ─────────────────────────────────────────────────────────

async def start_agent_run(
    db: AsyncSession,
    thread_id: int,
    node_name: str,
    input_payload: Optional[dict[str, Any]] = None,
) -> tuple[AgentRun, float]:
    """
    Create an AgentRun record in RUNNING state and return the start time.

    Returns:
        (AgentRun, start_time_perf_counter) — call finish_agent_run() when done.
    """
    run = AgentRun(
        thread_id=thread_id,
        node_name=node_name,
        input_payload=input_payload,
        status=RunStatus.RUNNING.value,
    )
    db.add(run)
    await db.flush()
    logger.debug(f"AgentRun started | node={node_name} thread={thread_id} id={run.id}")
    return run, time.perf_counter()


async def finish_agent_run(
    db: AsyncSession,
    run: AgentRun,
    start_time: float,
    status: str,
    output_payload: Optional[dict[str, Any]] = None,
) -> AgentRun:
    """
    Finalize an AgentRun with status, output, and latency.

    Args:
        db:             Async DB session.
        run:            The AgentRun returned by start_agent_run().
        start_time:     The perf_counter value from start_agent_run().
        status:         "success", "error", or "skipped".
        output_payload: The node's output dict (optional).

    Returns:
        Updated AgentRun instance.
    """
    run.latency_ms = int((time.perf_counter() - start_time) * 1000)
    run.status = status
    run.output_payload = output_payload
    db.add(run)
    await db.flush()
    logger.debug(
        f"AgentRun finished | node={run.node_name} "
        f"status={status} latency={run.latency_ms}ms"
    )
    return run


async def load_agent_runs(
    db: AsyncSession,
    thread_id: int,
    limit: int = 50,
) -> list[AgentRun]:
    """Load recent AgentRun logs for a thread, newest first."""
    result = await db.execute(
        select(AgentRun)
        .where(AgentRun.thread_id == thread_id)
        .order_by(AgentRun.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


# ── Meeting State ─────────────────────────────────────────────────────────────

async def get_or_create_meeting(
    db: AsyncSession,
    thread_id: int,
) -> Meeting:
    """Fetch the meeting for a thread, or create a new proposed one."""
    result = await db.execute(
        select(Meeting).where(Meeting.thread_id == thread_id)
    )
    meeting = result.scalar_one_or_none()

    if meeting is None:
        meeting = Meeting(
            thread_id=thread_id,
            status=MeetingStatus.PROPOSED.value,
            reschedule_count=0,
        )
        db.add(meeting)
        await db.flush()
        logger.info(f"Meeting record created for thread {thread_id}")

    return meeting


async def confirm_meeting(
    db: AsyncSession,
    meeting: Meeting,
    google_event_id: str,
    scheduled_at: datetime,
) -> Meeting:
    """Mark a meeting as confirmed with a Google Calendar event ID."""
    meeting.google_event_id = google_event_id
    meeting.scheduled_at = scheduled_at
    meeting.status = MeetingStatus.CONFIRMED.value
    db.add(meeting)
    await db.flush()
    logger.info(
        f"Meeting {meeting.id} confirmed | "
        f"event_id={google_event_id} at={scheduled_at.isoformat()}"
    )
    return meeting


async def reschedule_meeting(
    db: AsyncSession,
    meeting: Meeting,
    new_event_id: str,
    new_scheduled_at: datetime,
) -> Meeting:
    """
    Update a meeting after rescheduling.
    Increments reschedule_count and replaces the Google event ID.
    """
    meeting.google_event_id = new_event_id
    meeting.scheduled_at = new_scheduled_at
    meeting.status = MeetingStatus.RESCHEDULED.value
    meeting.reschedule_count += 1
    db.add(meeting)
    await db.flush()
    logger.info(
        f"Meeting {meeting.id} rescheduled (count={meeting.reschedule_count}) "
        f"→ event_id={new_event_id} at={new_scheduled_at.isoformat()}"
    )
    return meeting


async def cancel_meeting(
    db: AsyncSession,
    meeting: Meeting,
) -> Meeting:
    """Mark a meeting as cancelled."""
    meeting.status = MeetingStatus.CANCELLED.value
    db.add(meeting)
    await db.flush()
    logger.info(f"Meeting {meeting.id} cancelled.")
    return meeting


# ── Negotiation State ─────────────────────────────────────────────────────────

async def load_negotiation_state(
    db: AsyncSession,
    thread_id: int,
) -> Optional[Negotiation]:
    """Load the negotiation record for a thread. Returns None if not started."""
    result = await db.execute(
        select(Negotiation).where(Negotiation.thread_id == thread_id)
    )
    return result.scalar_one_or_none()
