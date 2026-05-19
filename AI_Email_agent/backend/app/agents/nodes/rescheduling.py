"""
Node: rescheduling
───────────────────
Handles meeting reschedule requests from the prospect.

Flow:
  1. Cancels the existing Google Calendar event (if any).
  2. Fetches new available slots via calendar_service.get_available_slots().
  3. Uses LLM to pick the best replacement slot.
  4. Creates a new calendar event via calendar_service.create_event().
  5. Persists the updated meeting record to DB.
  6. Sets reply_instruction for reply_generation.
  7. If reschedule_count >= MAX_RESCHEDULE_ATTEMPTS, walks away gracefully.

Output keys added to state:
  available_slots, selected_slot, meeting_status, google_event_id,
  scheduled_at, reschedule_count, reply_instruction
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone as dt_timezone

from app.agents.prompts import RESCHEDULE_SYSTEM, RESCHEDULE_USER
from app.agents.state import AgentState
from app.core.config import settings
from app.core.logging import logger
from app.db.session import AsyncSessionLocal
from app.services.calendar_service import (
    cancel_event,
    create_event,
    get_available_slots,
    TimeSlot,
)
from app.services.llm_service import ReschedulingDecision, get_llm_service
from app.services.memory_service import (
    cancel_meeting,
    get_or_create_meeting,
    reschedule_meeting,
)


async def rescheduling(state: AgentState) -> AgentState:
    """
    Handle a prospect's request to change their meeting time.

    Reads:
        thread_id, conversation_text, prospect_timezone, prospect_name,
        prospect_email, google_event_id, scheduled_at, reschedule_count

    Writes:
        available_slots, selected_slot, meeting_status, google_event_id,
        scheduled_at, reschedule_count, reply_instruction
    """
    thread_id = state.get("thread_id")
    conversation_text = state.get("conversation_text", "")
    prospect_name = state.get("prospect_name", "there")
    prospect_email = state.get("prospect_email", "")
    prospect_timezone = state.get("prospect_timezone") or settings.AGENT_DEFAULT_TIMEZONE
    existing_event_id = state.get("google_event_id")
    previous_scheduled_at = state.get("scheduled_at", "")
    reschedule_count: int = state.get("reschedule_count") or 0
    max_attempts: int = settings.AGENT_MAX_RESCHEDULE_ATTEMPTS

    logger.info(
        f"[rescheduling] thread={thread_id} count={reschedule_count}/{max_attempts}"
    )

    # Guard: too many reschedules → graceful walkaway
    if reschedule_count >= max_attempts:
        logger.warning(
            f"[rescheduling] Max reschedule attempts reached for thread {thread_id}"
        )
        async with AsyncSessionLocal() as db:
            meeting = await get_or_create_meeting(db, thread_id)
            await cancel_meeting(db, meeting)
            await db.commit()

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
        # 1 — Cancel the existing Google Calendar event
        if existing_event_id:
            try:
                cancel_event(existing_event_id)
                logger.info(
                    f"[rescheduling] Cancelled event {existing_event_id} "
                    f"for thread {thread_id}"
                )
            except Exception as cancel_err:
                logger.warning(
                    f"[rescheduling] Failed to cancel event {existing_event_id}: "
                    f"{cancel_err}"
                )

        # 2 — Fetch new slots
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

        # 3 — LLM picks the best new slot
        llm = get_llm_service()
        decision: ReschedulingDecision = llm.generate_structured(
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

        # 4 — Create new Google Calendar event
        event = create_event(
            summary=f"Rescheduled: Intro call with {prospect_name}",
            description="Rescheduled introduction call — AI Email Agent.",
            start=chosen_slot.start,
            attendee_emails=[prospect_email] if prospect_email else [],
            duration_minutes=30,
            tz_name="UTC",
            add_meet_link=True,
        )
        event_id: str = event.event_id
        meet_link: str = event.meet_link or event.html_link or ""

        # 5 — Persist to DB
        new_count = reschedule_count + 1
        async with AsyncSessionLocal() as db:
            meeting = await get_or_create_meeting(db, thread_id)
            await reschedule_meeting(db, meeting, event_id, chosen_slot.start)
            await db.commit()

        # 6 — Build reply instruction
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
        logger.exception(f"[rescheduling] thread={thread_id} failed")
        return {
            **state,
            "reply_instruction": (
                f"Apologise to {prospect_name} for the inconvenience. "
                "Let them know you're sorting out a new time and will follow up shortly."
            ),
            "error": str(exc),
            "error_node": "rescheduling",
        }


# ── Helpers ───────────────────────────────────────────────────────────────────

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
    """Return a human-readable slot string (cross-platform, no %-d)."""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tz_name)
        dt = slot.start.astimezone(tz)
        day = str(dt.day)
        hour = dt.strftime("%I").lstrip("0") or "12"
        return dt.strftime(f"%A, %B {day} at {hour}:%M %p %Z")
    except Exception:
        return slot.start.isoformat()
