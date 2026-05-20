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
    # Round counter persisted in DB so walkaway logic accumulates across emails
    counter_round: int = 0
    # Last prospect offer for Rule 3 (not moving → walkaway)
    last_prospect_offer: Optional[float] = None

    # Meeting memory
    meeting_status: Optional[str] = None
    google_event_id: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    reschedule_count: int = 0

    # Human escalation — calendar-specific (Phase 4)
    needs_human_review: bool = False
    calendar_failure_count: int = 0

    # Agent-level escalation (Phase 5)
    # agent_escalated: True when the agent cannot handle the thread autonomously
    # (repeated ambiguity, low-confidence high-stakes intent, API failures).
    agent_escalated: bool = False
    escalation_reason: Optional[str] = None
    ambiguous_count: int = 0

    # Rolling memory summary (Phase 5)
    # Compressed LLM summary of messages older than the memory window.
    thread_summary: Optional[str] = None


# ── Message Persistence ───────────────────────────────────────────────────────

async def save_message(
    db: AsyncSession,
    thread_id: int,
    sender: str,
    body: str,
    timestamp: datetime,
    intent: Optional[str] = None,
    raw_payload: Optional[dict[str, Any]] = None,
    gmail_message_id: Optional[str] = None,
) -> EmailMessage:
    """
    Persist an email message (inbound or outbound) to the DB.

    Args:
        db:               Async DB session.
        thread_id:        DB ID of the parent EmailThread.
        sender:           Sender email address or "agent".
        body:             Plain-text email body.
        timestamp:        Message timestamp (timezone-aware).
        intent:           Detected intent label (optional, set after classification).
        raw_payload:      Raw Gmail API payload dict (optional).
        gmail_message_id: Gmail API message ID (e.g. "17f9e2f3d8c4a1b2").
                          Stored in a dedicated indexed column for fast idempotency
                          checks.  Always pass this for inbound messages;
                          pass it for outbound messages once the Gmail send returns.

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
        gmail_message_id=gmail_message_id,
    )
    db.add(msg)
    await db.flush()
    logger.debug(
        f"Message saved | thread_id={thread_id} sender={sender} "
        f"gmail_message_id={gmail_message_id} intent={intent} id={msg.id}"
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
            # Use a savepoint so that an IntegrityError on concurrent INSERT
            # only rolls back this one statement, not the entire outer
            # transaction (which may already contain saved messages from the
            # current poll batch).
            async with db.begin_nested():
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
            # Another concurrent task already inserted this thread — the
            # savepoint was rolled back; re-fetch the existing row.
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
    # Use windowed context: full history for short threads, summary + recent
    # messages for long threads — prevents sending megabyte-size prompts.
    from app.core.config import settings
    conversation_text = build_windowed_context(
        messages=messages,
        summary=thread.thread_summary,
        window=settings.MEMORY_WINDOW_MESSAGES,
    )

    # Serialize messages for agent state.
    # raw_payload is included so negotiation._extract_prospect_offer can read
    # structured amount fields that LLM/Gmail parsing may have stored there.
    messages_dicts = [
        {
            "id": m.id,
            "sender": m.sender,
            "body": m.body,
            "intent": m.intent,
            "timestamp": m.timestamp.isoformat() if m.timestamp else None,
            "raw_payload": m.raw_payload,
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
        counter_round=negotiation.counter_round if negotiation else 0,
        last_prospect_offer=negotiation.last_prospect_offer if negotiation else None,
        # Meeting
        meeting_status=meeting.status if meeting else None,
        google_event_id=meeting.google_event_id if meeting else None,
        scheduled_at=meeting.scheduled_at if meeting else None,
        reschedule_count=meeting.reschedule_count if meeting else 0,
        # Calendar escalation (Phase 4)
        needs_human_review=meeting.needs_human_review if meeting else False,
        calendar_failure_count=meeting.calendar_failure_count if meeting else 0,
        # Agent escalation (Phase 5)
        agent_escalated=thread.agent_escalated,
        escalation_reason=thread.escalation_reason,
        ambiguous_count=thread.ambiguous_count,
        thread_summary=thread.thread_summary,
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


async def increment_calendar_failure(
    db: AsyncSession,
    thread_id: int,
    max_failures: int = 3,
) -> tuple[int, bool]:
    """
    Increment the ``calendar_failure_count`` for the meeting associated with
    *thread_id* and conditionally set ``needs_human_review``.

    Called from scheduling and rescheduling nodes when a CalendarOpError is
    caught, so that repeated failures are tracked and escalated.

    Args:
        db:           Async DB session (caller must commit after this returns).
        thread_id:    DB primary key of the EmailThread.
        max_failures: Threshold above which ``needs_human_review`` is set True.
                      Defaults to 3; override via settings.CALENDAR_MAX_FAILURES.

    Returns:
        (new_failure_count, needs_human_review)
    """
    meeting = await get_or_create_meeting(db, thread_id)
    meeting.calendar_failure_count = (meeting.calendar_failure_count or 0) + 1
    needs_review = meeting.calendar_failure_count >= max_failures
    if needs_review and not meeting.needs_human_review:
        meeting.needs_human_review = True
        logger.warning(
            f"Meeting {meeting.id} (thread={thread_id}) reached "
            f"{meeting.calendar_failure_count} calendar failures — "
            "flagged for human review"
        )
    db.add(meeting)
    await db.flush()
    return meeting.calendar_failure_count, meeting.needs_human_review


# ── Rolling Memory (Phase 5) ──────────────────────────────────────────────────

def build_windowed_context(
    messages: list,
    summary: Optional[str] = None,
    window: int = 20,
) -> str:
    """
    Build a conversation context string that fits within the LLM's practical
    token budget.

    Strategy:
      - If the thread has ≤ *window* messages: return the full formatted history
        (existing behaviour, zero regression for short threads).
      - If the thread has > *window* messages: return the stored *summary*
        (if any) prepended to the *window* most-recent messages.
        If no summary exists yet, add a notice about the omitted count.

    Args:
        messages:  All EmailMessage ORM objects for the thread (oldest-first).
        summary:   The EmailThread.thread_summary value (may be None).
        window:    Number of most-recent messages to include verbatim.

    Returns:
        A plain-text string ready for injection into an LLM prompt.
    """
    if len(messages) <= window:
        return format_conversation_for_llm(messages)

    recent = messages[-window:]
    recent_text = format_conversation_for_llm(recent)

    omitted_count = len(messages) - window

    if summary:
        header = (
            f"[Conversation summary — {omitted_count} older message(s) compressed]\n"
            f"{summary}\n\n"
            f"[{window} most recent messages]\n"
        )
    else:
        header = (
            f"[{omitted_count} earlier message(s) not shown — "
            "no summary available yet]\n\n"
        )

    return header + recent_text


async def increment_ambiguous_count(
    db: AsyncSession,
    thread_id: int,
    threshold: int = 3,
) -> tuple[int, bool]:
    """
    Increment ``EmailThread.ambiguous_count`` for *thread_id* and check if
    the escalation threshold has been reached.

    Called when classify_intent returns "ambiguous" (after confidence
    downgrade or genuine ambiguity).

    Args:
        db:        Async DB session (caller must commit after this returns).
        thread_id: DB primary key of the EmailThread.
        threshold: Consecutive-ambiguous count that triggers escalation.

    Returns:
        (new_count, agent_escalated) — agent_escalated is True when the
        threshold has just been reached.
    """
    result = await db.execute(
        select(EmailThread).where(EmailThread.id == thread_id)
    )
    thread = result.scalar_one_or_none()
    if thread is None:
        return 0, False

    thread.ambiguous_count = (thread.ambiguous_count or 0) + 1
    newly_escalated = (
        thread.ambiguous_count >= threshold
        and not thread.agent_escalated
    )
    if newly_escalated:
        thread.agent_escalated = True
        reason = (
            f"Repeated ambiguous intent: {thread.ambiguous_count} consecutive "
            "classifications — forwarding to human operator."
        )
        thread.escalation_reason = reason
        logger.warning(
            f"Thread {thread_id} escalated | reason='{reason}'"
        )
    db.add(thread)
    await db.flush()
    return thread.ambiguous_count, thread.agent_escalated


async def reset_ambiguous_count(
    db: AsyncSession,
    thread_id: int,
) -> None:
    """
    Reset ``EmailThread.ambiguous_count`` to 0 when a clear (non-ambiguous)
    intent is classified.  This prevents stale counters from triggering false
    escalations after a prospect finally responds clearly.

    Args:
        db:        Async DB session (caller must commit after this returns).
        thread_id: DB primary key of the EmailThread.
    """
    result = await db.execute(
        select(EmailThread).where(EmailThread.id == thread_id)
    )
    thread = result.scalar_one_or_none()
    if thread and thread.ambiguous_count > 0:
        thread.ambiguous_count = 0
        db.add(thread)
        await db.flush()
        logger.debug(f"Thread {thread_id} ambiguous_count reset to 0")


async def escalate_thread(
    db: AsyncSession,
    thread_id: int,
    reason: str,
) -> None:
    """
    Set ``agent_escalated=True`` and record the *reason* on the EmailThread.

    Called from any node that determines the agent can no longer handle the
    conversation autonomously (e.g. repeated API failures, negotiation
    deadlock).

    Args:
        db:        Async DB session (caller must commit after this returns).
        thread_id: DB primary key of the EmailThread.
        reason:    Human-readable escalation reason (stored for operator review).
    """
    result = await db.execute(
        select(EmailThread).where(EmailThread.id == thread_id)
    )
    thread = result.scalar_one_or_none()
    if thread and not thread.agent_escalated:
        thread.agent_escalated = True
        thread.escalation_reason = reason[:512]  # respect column length
        db.add(thread)
        await db.flush()
        logger.warning(
            f"Thread {thread_id} escalated | reason='{reason}'"
        )


async def update_thread_summary(
    db: AsyncSession,
    thread_id: int,
    summary: str,
) -> None:
    """
    Persist a new rolling *summary* for the thread.

    Called by the summarization service after generating or updating the
    compressed conversation history.

    Args:
        db:        Async DB session (caller must commit after this returns).
        thread_id: DB primary key of the EmailThread.
        summary:   The new summary text to store.
    """
    result = await db.execute(
        select(EmailThread).where(EmailThread.id == thread_id)
    )
    thread = result.scalar_one_or_none()
    if thread:
        thread.thread_summary = summary
        db.add(thread)
        await db.flush()
        logger.info(
            f"Thread {thread_id} summary updated | length={len(summary)}"
        )


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
