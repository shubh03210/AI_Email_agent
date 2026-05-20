"""
Node: rescheduling
───────────────────
Handles meeting reschedule requests from the prospect.

Flow:
  0. Human-escalation guard — if needs_human_review=True, skip automation.
  1. Validate and normalise prospect timezone (DST-safe via zoneinfo).
  2. Guard: if reschedule_count >= MAX_RESCHEDULE_ATTEMPTS, use
     cancel_meeting_calendar (DB-first, then soft-cancel calendar event).
  3. Fetch new available calendar slots.
  4. LLM selects the best replacement slot.
  5. Atomically reschedule (calendar_ops.reschedule_meeting_atomic):
       a. Create NEW Google Calendar event.
       b. DB commit (update meeting to new event ID).
       c. Only then cancel OLD event (soft-fail; if it fails, set needs_human_review).
     This ordering guarantees the prospect is NEVER left without a meeting.
  6. Set reply_instruction for reply_generation.

Error/failure handling:
  - CalendarOpError → increment calendar_failure_count; if threshold reached,
    set needs_human_review=True in DB and return human-escalation reply.
  - Any other exception → same treatment for consistency.

Output keys added to state:
  available_slots, selected_slot, meeting_status, google_event_id,
  scheduled_at, reschedule_count, reply_instruction,
  needs_human_review, calendar_failure_count
"""

from __future__ import annotations

from typing import Optional

from app.agents.prompts import RESCHEDULE_SYSTEM, RESCHEDULE_USER
from app.agents.state import AgentState
from app.core.config import settings
from app.core.logging import logger
from app.db.session import AsyncSessionLocal
from app.services.calendar_ops import (
    CalendarOpError,
    SchedulingResult,
    cancel_meeting_calendar,
    reschedule_meeting_atomic,
    validate_timezone,
)
from app.services.calendar_service import TimeSlot, get_available_slots
from app.services.llm_service import ReschedulingDecision, get_llm_service
from app.services.memory_service import increment_calendar_failure

# Reply instruction for human-escalation state.
_HUMAN_REVIEW_REPLY = (
    "Apologise sincerely to the prospect for the repeated scheduling difficulties. "
    "Let them know that a team member will reach out directly to arrange a suitable "
    "time, and that you'll make sure it happens shortly. Keep the tone warm and "
    "reassuring."
)


async def rescheduling(state: AgentState) -> AgentState:
    """
    Handle a prospect's request to change their meeting time.

    Reads:
        thread_id, conversation_text, prospect_timezone, prospect_name,
        prospect_email, google_event_id, scheduled_at, reschedule_count,
        needs_human_review, calendar_failure_count

    Writes:
        available_slots, selected_slot, meeting_status, google_event_id,
        scheduled_at, reschedule_count, reply_instruction,
        needs_human_review, calendar_failure_count
    """
    thread_id: int = state.get("thread_id")
    conversation_text: str = state.get("conversation_text", "")
    prospect_name: str = state.get("prospect_name", "there")
    prospect_email: str = state.get("prospect_email", "")
    existing_event_id: Optional[str] = state.get("google_event_id")
    previous_scheduled_at: str = state.get("scheduled_at", "")
    reschedule_count: int = state.get("reschedule_count") or 0
    max_attempts: int = settings.AGENT_MAX_RESCHEDULE_ATTEMPTS

    # ── 0. Human-escalation guard ─────────────────────────────────────────────
    if state.get("needs_human_review"):
        logger.warning(
            f"[rescheduling] thread={thread_id} is flagged for human review — "
            "skipping automation"
        )
        return {
            **state,
            "reply_instruction": _HUMAN_REVIEW_REPLY,
            "error": "Meeting flagged for human review — rescheduling skipped.",
            "error_node": "rescheduling",
        }

    # ── 1. Timezone validation ────────────────────────────────────────────────
    raw_tz: Optional[str] = state.get("prospect_timezone")
    prospect_timezone: str = validate_timezone(
        raw_tz, fallback=settings.AGENT_DEFAULT_TIMEZONE
    )
    if prospect_timezone != raw_tz:
        logger.warning(
            f"[rescheduling] thread={thread_id} — invalid prospect_timezone "
            f"'{raw_tz}' corrected to '{prospect_timezone}'"
        )

    logger.info(
        f"[rescheduling] thread={thread_id} count={reschedule_count}/{max_attempts} "
        f"tz={prospect_timezone}"
    )

    # ── 2. Max reschedules guard ──────────────────────────────────────────────
    if reschedule_count >= max_attempts:
        logger.warning(
            f"[rescheduling] Max reschedule attempts reached for thread {thread_id}"
        )
        try:
            # DB-first cancel, then soft-cancel calendar event
            await cancel_meeting_calendar(
                thread_id=thread_id,
                event_id=existing_event_id,
            )
        except CalendarOpError as cancel_err:
            logger.error(
                f"[rescheduling] cancel_meeting_calendar failed for thread "
                f"{thread_id}: {cancel_err}"
            )

        return {
            **state,
            "meeting_status": "cancelled",
            "reply_instruction": (
                f"Let {prospect_name} know that after {reschedule_count} "
                "reschedule attempts you're going to leave the calendar clear for now. "
                "Invite them to reach out again when they have a free window. "
                "Keep it warm and brief — no hard feelings."
            ),
        }

    try:
        # ── 3. Fetch new slots ────────────────────────────────────────────────
        raw_slots: list[TimeSlot] = _fetch_slots(prospect_timezone)
        if not raw_slots:
            return {
                **state,
                "reply_instruction": (
                    f"Apologise to {prospect_name} for the scheduling difficulty. "
                    "Let them know you'll send updated availability within a few hours."
                ),
                "error": "No calendar slots available for reschedule.",
                "error_node": "rescheduling",
            }

        slots = _slots_to_dicts(raw_slots)
        slots_text = _format_slots_text(slots)

        # ── 4. LLM selects new slot ───────────────────────────────────────────
        llm = get_llm_service()
        decision: ReschedulingDecision = await llm.async_generate_structured(
            system_prompt=RESCHEDULE_SYSTEM,
            user_message=RESCHEDULE_USER.format(
                conversation_text=conversation_text,
                prospect_timezone=prospect_timezone,
                previous_scheduled_at=previous_scheduled_at or "not yet confirmed",
                reschedule_count=reschedule_count,
                slots_text=slots_text,
                selected_slot_index=0,
                selected_slot_dt=slots[0]["start"],
            ),
            output_schema=ReschedulingDecision,
        )

        idx = max(0, min(decision.new_slot_index, len(raw_slots) - 1))
        chosen_slot = raw_slots[idx]
        chosen_dict = slots[idx]

        logger.info(
            f"[rescheduling] New slot index={idx} start={chosen_dict['start']} "
            f"thread={thread_id}"
        )

        # ── 5. Atomic reschedule (new-event-first order) ──────────────────────
        # reschedule_meeting_atomic:
        #   a. Creates new event
        #   b. Commits DB update to new event ID
        #   c. If DB fails → deletes new event (compensation), old event still valid
        #   d. Cancels old event AFTER successful DB commit (soft-fail)
        #   e. If old cancel fails → sets needs_human_review on meeting
        result: SchedulingResult = await reschedule_meeting_atomic(
            thread_id=thread_id,
            old_event_id=existing_event_id,
            prospect_name=prospect_name,
            prospect_email=prospect_email,
            slot=chosen_slot,
        )
        event_id = result.google_event_id
        meet_link = result.meet_link
        new_count = reschedule_count + 1

        # ── 6. Build reply instruction ────────────────────────────────────────
        reply_instruction = (
            f"{decision.apology_message} "
            f"Propose the new time: {_human_readable_slot(chosen_slot, prospect_timezone)}. "
            f"Include the Google Meet link: {meet_link}. "
            "Ask them to confirm and note a new calendar invite is on its way."
        )
        if new_count >= max_attempts - 1:
            reply_instruction += (
                " Add a brief, friendly note that this is your calendar's last "
                "available window for the next few days."
            )

        return {
            **state,
            "available_slots": slots,
            "selected_slot": chosen_dict,
            "meeting_status": "rescheduled",
            "google_event_id": event_id,
            "scheduled_at": chosen_slot.start.isoformat(),
            "reschedule_count": new_count,
            "reply_instruction": reply_instruction,
        }

    except Exception as exc:
        logger.exception(f"[rescheduling] thread={thread_id} failed: {exc}")

        # Increment failure counter; escalate if threshold reached
        new_count, needs_review = _handle_calendar_failure(thread_id)

        reply_instruction = _HUMAN_REVIEW_REPLY if needs_review else (
            f"Apologise to {prospect_name} for the inconvenience. "
            "Let them know you're sorting out a new time and will follow up shortly."
        )

        return {
            **state,
            "needs_human_review": needs_review,
            "calendar_failure_count": new_count,
            "reply_instruction": reply_instruction,
            "error": str(exc),
            "error_node": "rescheduling",
        }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _handle_calendar_failure(thread_id: int) -> tuple[int, bool]:
    """
    Synchronous bridge: increments calendar_failure_count in DB.
    Returns (new_count, needs_human_review).  Fails silently on DB error.
    """
    import asyncio

    async def _inner() -> tuple[int, bool]:
        try:
            async with AsyncSessionLocal() as db:
                count, review = await increment_calendar_failure(
                    db, thread_id, settings.CALENDAR_MAX_FAILURES
                )
                await db.commit()
                return count, review
        except Exception as db_err:
            logger.warning(
                f"[rescheduling] Could not update failure count for "
                f"thread={thread_id}: {db_err}"
            )
            return 0, False

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, _inner())
                return future.result(timeout=10)
        else:
            return loop.run_until_complete(_inner())
    except Exception as err:
        logger.warning(f"[rescheduling] Failure count update skipped: {err}")
        return 0, False


def _fetch_slots(tz_name: str) -> list[TimeSlot]:
    try:
        return get_available_slots(
            duration_minutes=30,
            days_ahead=5,
            tz_name=tz_name,
            working_hours_start=settings.AGENT_DEFAULT_WORKING_HOURS_START,
            working_hours_end=settings.AGENT_DEFAULT_WORKING_HOURS_END,
            max_slots=8,
        )
    except Exception as exc:
        logger.error(f"[rescheduling] calendar fetch error: {exc}")
        return []


def _slots_to_dicts(slots: list[TimeSlot]) -> list[dict]:
    return [
        {"start": s.start.isoformat(), "end": s.end.isoformat()}
        for s in slots
    ]


def _format_slots_text(slots: list[dict]) -> str:
    return "\n".join(
        f"  [{i}] {s['start']} → {s['end']}"
        for i, s in enumerate(slots)
    )


def _human_readable_slot(slot: TimeSlot, tz_name: str) -> str:
    """Return a human-readable slot string (DST-safe)."""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tz_name)
        dt = slot.start.astimezone(tz)
        day = str(dt.day)
        hour = dt.strftime("%I").lstrip("0") or "12"
        return dt.strftime(f"%A, %B {day} at {hour}:%M %p %Z")
    except Exception:
        return slot.start.isoformat()
