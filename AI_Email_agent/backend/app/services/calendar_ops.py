"""
Transactional Calendar Operations
────────────────────────────────────
Wraps Google Calendar API calls with atomic DB transactions and compensation
logic to prevent calendar/database split-brain states.

Transaction ordering rules (ensure DB is always the source of truth):
─────────────────────────────────────────────────────────────────────
1. schedule_meeting_atomic (create):
   a. CREATE calendar event
   b. DB commit (confirm meeting with event ID)
   c. If (b) fails → DELETE calendar event (compensation)

2. reschedule_meeting_atomic (cancel-old + create-new):
   a. CREATE new calendar event first
   b. DB commit (update meeting to new event ID)
   c. If (b) fails → DELETE new event (compensation); old event still valid
   d. CANCEL old event only after (b) succeeds (soft-fail — DB already correct)
   e. If (d) fails → set needs_human_review=True (duplicate invite possible)

3. cancel_meeting_calendar (cancel only):
   a. DB commit (mark meeting as cancelled) — DB-first
   b. CANCEL calendar event (soft-fail — DB already correct)
   c. If (b) fails → set needs_human_review=True

This ordering ensures the meeting state in the DB is always correct and
calendar orphans are surfaced for human review rather than silently lost.

Timezone safety:
  All event datetimes are UTC-aware.  validate_timezone() checks IANA zone
  strings before use and falls back to UTC on invalid input to prevent
  ZoneInfoNotFoundError crashes in downstream calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.logging import logger
from app.db.session import AsyncSessionLocal


# ── Custom exception ──────────────────────────────────────────────────────────

class CalendarOpError(Exception):
    """
    Raised when a calendar operation cannot be completed and the system has
    already performed any feasible compensation (e.g. deleting an orphaned
    event).  The caller should increment the failure counter and return a
    graceful reply to the prospect.
    """


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class SchedulingResult:
    """Returned by schedule_meeting_atomic and reschedule_meeting_atomic."""
    google_event_id: str
    meet_link: str


# ── Timezone validation ────────────────────────────────────────────────────────

def validate_timezone(tz_name: Optional[str], fallback: str = "UTC") -> str:
    """
    Validate that *tz_name* is a known IANA timezone identifier.

    Returns *tz_name* unchanged if valid, or *fallback* (default "UTC") if:
      - the string is None or empty
      - it is not recognised by the system's tzdata (ZoneInfoNotFoundError)

    Never raises.  DST transitions are handled correctly by ZoneInfo for all
    valid zone strings.
    """
    if not tz_name:
        return fallback
    try:
        ZoneInfo(tz_name)
        return tz_name
    except Exception:
        # Catches ZoneInfoNotFoundError, KeyError, and on Windows OSError for
        # invalid filename characters that can appear in malformed tz strings.
        logger.warning(
            f"[calendar_ops] Unknown timezone '{tz_name}' — "
            f"falling back to '{fallback}'"
        )
        return fallback


# ── Internal helpers ──────────────────────────────────────────────────────────

def _safe_cancel_event(event_id: str) -> None:
    """
    Cancel a Google Calendar event, logging any failure without raising.
    Used as a compensation step after a DB commit fails.
    """
    try:
        from app.services.calendar_service import cancel_event
        cancel_event(event_id)
        logger.info(f"[calendar_ops] Compensation: orphaned event {event_id} deleted")
    except Exception as exc:
        logger.error(
            f"[calendar_ops] Compensation FAILED — event {event_id} is orphaned "
            f"and must be deleted manually: {exc}"
        )


async def _mark_needs_review(thread_id: int) -> None:
    """
    Set needs_human_review=True on the meeting for *thread_id*.
    Opens a fresh DB session so it is safe to call from any context.
    Silently ignores any DB error (best-effort escalation).
    """
    try:
        from app.services.memory_service import get_or_create_meeting
        async with AsyncSessionLocal() as db:
            meeting = await get_or_create_meeting(db, thread_id)
            meeting.needs_human_review = True
            db.add(meeting)
            await db.commit()
        logger.info(f"[calendar_ops] Meeting for thread {thread_id} flagged for human review")
    except Exception as exc:
        logger.warning(f"[calendar_ops] Could not set needs_human_review for thread {thread_id}: {exc}")


# ── Public API ────────────────────────────────────────────────────────────────

async def schedule_meeting_atomic(
    thread_id: int,
    prospect_name: str,
    prospect_email: str,
    slot,                          # calendar_service.TimeSlot
) -> SchedulingResult:
    """
    Atomically create a Google Calendar event and persist it to the DB.

    Steps:
      1. CREATE Google Calendar event (returns event_id).
      2. DB: confirm_meeting(event_id, scheduled_at).
      3. DB: commit.
      If step 2 or 3 fails → DELETE the calendar event (compensation) then
      raise CalendarOpError.

    Args:
        thread_id:       DB primary key of the EmailThread.
        prospect_name:   Prospect's display name (used in event title).
        prospect_email:  Prospect's email (added as calendar attendee).
        slot:            calendar_service.TimeSlot — UTC-aware start/end.

    Returns:
        SchedulingResult(google_event_id, meet_link)

    Raises:
        CalendarOpError: if creation or DB commit fails (compensation attempted).
    """
    from app.services.calendar_service import create_event
    from app.services.memory_service import confirm_meeting, get_or_create_meeting

    # 1 — Create Google Calendar event
    try:
        event = create_event(
            summary=f"Intro call with {prospect_name}",
            description="Introduction call scheduled via AI Email Agent.",
            start=slot.start,
            attendee_emails=[prospect_email] if prospect_email else [],
            duration_minutes=30,
            tz_name="UTC",
            add_meet_link=True,
        )
    except Exception as cal_exc:
        raise CalendarOpError(f"Calendar event creation failed: {cal_exc}") from cal_exc

    event_id = event.event_id
    meet_link = event.meet_link or event.html_link or ""

    logger.info(
        f"[calendar_ops] Event created | id={event_id} thread={thread_id} "
        f"start={slot.start.isoformat()}"
    )

    # 2 — DB: confirm meeting (compensation if this fails)
    try:
        async with AsyncSessionLocal() as db:
            meeting = await get_or_create_meeting(db, thread_id)
            await confirm_meeting(db, meeting, event_id, slot.start)
            await db.commit()
    except Exception as db_exc:
        logger.error(
            f"[calendar_ops] DB commit failed after creating event {event_id} — "
            "compensating by deleting the event"
        )
        _safe_cancel_event(event_id)
        raise CalendarOpError(
            f"DB update failed for scheduled event {event_id}: {db_exc}"
        ) from db_exc

    logger.info(
        f"[calendar_ops] schedule_meeting_atomic OK | "
        f"thread={thread_id} event={event_id}"
    )
    return SchedulingResult(google_event_id=event_id, meet_link=meet_link)


async def reschedule_meeting_atomic(
    thread_id: int,
    old_event_id: Optional[str],
    prospect_name: str,
    prospect_email: str,
    slot,                          # calendar_service.TimeSlot
) -> SchedulingResult:
    """
    Atomically reschedule a meeting: create new event, commit to DB, then
    cancel the old event.

    Critical ordering: NEW event is created BEFORE the old is cancelled.
    This prevents a "no meeting" state if any step fails:

      Steps:
        1. CREATE new Google Calendar event.
        2. DB: reschedule_meeting(new_event_id, new_scheduled_at).
        3. DB: commit.
           If step 2 or 3 fails → DELETE new event (compensation), old event
           still valid → raise CalendarOpError.
        4. CANCEL old event (soft-fail — DB is already consistent).
           If step 4 fails → mark needs_human_review=True and log.
           (Duplicate invite possible; human must manually remove old event.)

    Args:
        thread_id:       DB primary key of the EmailThread.
        old_event_id:    Google Calendar event ID to cancel (may be None).
        prospect_name:   Prospect's display name.
        prospect_email:  Prospect's email.
        slot:            New TimeSlot (UTC-aware).

    Returns:
        SchedulingResult(google_event_id, meet_link)

    Raises:
        CalendarOpError: if new event creation or DB commit fails.
    """
    from app.services.calendar_service import cancel_event, create_event
    from app.services.memory_service import get_or_create_meeting, reschedule_meeting

    # 1 — Create new calendar event first (can be compensated by DELETE)
    try:
        event = create_event(
            summary=f"Rescheduled: Intro call with {prospect_name}",
            description="Rescheduled introduction call — AI Email Agent.",
            start=slot.start,
            attendee_emails=[prospect_email] if prospect_email else [],
            duration_minutes=30,
            tz_name="UTC",
            add_meet_link=True,
        )
    except Exception as cal_exc:
        raise CalendarOpError(
            f"Failed to create replacement event: {cal_exc}"
        ) from cal_exc

    new_event_id = event.event_id
    meet_link = event.meet_link or event.html_link or ""

    logger.info(
        f"[calendar_ops] New event created | id={new_event_id} thread={thread_id}"
    )

    # 2 — DB: update meeting to new event ID (compensation if this fails)
    try:
        async with AsyncSessionLocal() as db:
            meeting = await get_or_create_meeting(db, thread_id)
            await reschedule_meeting(db, meeting, new_event_id, slot.start)
            await db.commit()
    except Exception as db_exc:
        logger.error(
            f"[calendar_ops] DB commit failed after creating new event "
            f"{new_event_id} — compensating by deleting it"
        )
        _safe_cancel_event(new_event_id)
        raise CalendarOpError(
            f"DB update failed for rescheduled event {new_event_id}: {db_exc}"
        ) from db_exc

    logger.info(
        f"[calendar_ops] DB updated for reschedule | thread={thread_id} "
        f"new_event={new_event_id}"
    )

    # 3 — Cancel old event (soft-fail — DB is already correct)
    if old_event_id:
        try:
            cancel_event(old_event_id)
            logger.info(
                f"[calendar_ops] Old event {old_event_id} cancelled "
                f"(thread={thread_id})"
            )
        except Exception as cancel_exc:
            logger.warning(
                f"[calendar_ops] Soft fail: could not cancel old event "
                f"{old_event_id} for thread {thread_id}: {cancel_exc}. "
                "DB is consistent — flagging meeting for human review "
                "(duplicate calendar invite possible)."
            )
            # Best-effort escalation: mark meeting for human review
            await _mark_needs_review(thread_id)

    return SchedulingResult(google_event_id=new_event_id, meet_link=meet_link)


async def cancel_meeting_calendar(
    thread_id: int,
    event_id: Optional[str],
) -> None:
    """
    DB-first cancellation: mark meeting cancelled in DB, then cancel the
    Google Calendar event (soft-fail).

    Steps:
      1. DB: cancel_meeting (commit) — DB-first so the record is always correct.
      2. CANCEL calendar event (soft-fail).
         If step 2 fails → mark needs_human_review=True and log.
         (Prospect's calendar still shows the event; human must remove it.)

    Args:
        thread_id: DB primary key of the EmailThread.
        event_id:  Google Calendar event ID to cancel (may be None if not yet set).
    """
    from app.services.calendar_service import cancel_event
    from app.services.memory_service import cancel_meeting, get_or_create_meeting

    # 1 — DB first: mark meeting as cancelled
    try:
        async with AsyncSessionLocal() as db:
            meeting = await get_or_create_meeting(db, thread_id)
            await cancel_meeting(db, meeting)
            await db.commit()
        logger.info(
            f"[calendar_ops] Meeting for thread {thread_id} marked cancelled in DB"
        )
    except Exception as db_exc:
        raise CalendarOpError(
            f"DB cancel failed for thread {thread_id}: {db_exc}"
        ) from db_exc

    # 2 — Calendar: cancel (soft-fail — DB already correct)
    if event_id:
        try:
            cancel_event(event_id)
            logger.info(
                f"[calendar_ops] Calendar event {event_id} cancelled "
                f"(thread={thread_id})"
            )
        except Exception as cancel_exc:
            logger.warning(
                f"[calendar_ops] Soft fail: could not cancel calendar event "
                f"{event_id} for thread {thread_id}: {cancel_exc}. "
                "DB is correct — flagging for human review."
            )
            await _mark_needs_review(thread_id)
