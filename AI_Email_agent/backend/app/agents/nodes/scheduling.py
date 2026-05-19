"""
Node: scheduling
─────────────────
Books a meeting when the prospect is ready (intent: interested / accepted offer).

Flow:
  1. Fetches available calendar slots via calendar_service.get_available_slots().
  2. Uses LLM to pick the best slot for the prospect's timezone.
  3. Creates the Google Calendar event via calendar_service.create_event().
  4. Persists meeting record to DB via memory_service.
  5. Sets reply_instruction for reply_generation.

Output keys added to state:
  available_slots, selected_slot, meeting_status, google_event_id,
  scheduled_at, reply_instruction
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone as dt_timezone

from app.agents.prompts import SCHEDULING_SYSTEM, SCHEDULING_USER
from app.agents.state import AgentState
from app.core.config import settings
from app.core.logging import logger
from app.db.session import AsyncSessionLocal
from app.services.calendar_service import (
    get_available_slots,
    create_event,
    TimeSlot,
)
from app.services.llm_service import SchedulingDecision, get_llm_service
from app.services.memory_service import confirm_meeting, get_or_create_meeting


async def scheduling(state: AgentState) -> AgentState:
    """
    Find a free slot and book a meeting with the prospect.

    Reads:
        thread_id, conversation_text, prospect_timezone, prospect_name, prospect_email

    Writes:
        available_slots, selected_slot, meeting_status, google_event_id,
        scheduled_at, reply_instruction
    """
    thread_id = state.get("thread_id")
    conversation_text = state.get("conversation_text", "")
    prospect_name = state.get("prospect_name", "there")
    prospect_email = state.get("prospect_email", "")
    prospect_timezone = state.get("prospect_timezone") or settings.AGENT_DEFAULT_TIMEZONE

    logger.info(f"[scheduling] thread={thread_id} tz={prospect_timezone}")

    try:
        # 1 — Get free 30-minute slots for the next 5 working days
        raw_slots: list[TimeSlot] = _fetch_slots(prospect_timezone)

        if not raw_slots:
            logger.warning(f"[scheduling] No slots available for thread {thread_id}")
            return {
                **state,
                "available_slots": [],
                "reply_instruction": (
                    "Let the prospect know you're checking your calendar and will "
                    "send available times within the next few hours. Apologise for "
                    "the slight delay."
                ),
                "error": "No calendar slots available.",
                "error_node": "scheduling",
            }

        # Serialise to dicts for JSON-safe state storage and LLM prompt
        slots = _slots_to_dicts(raw_slots)
        slots_text = _format_slots_text(slots)

        # 2 — LLM picks the best slot
        llm = get_llm_service()
        decision: SchedulingDecision = llm.generate_structured(
            system_prompt=SCHEDULING_SYSTEM,
            user_message=SCHEDULING_USER.format(
                conversation_text=conversation_text,
                prospect_timezone=prospect_timezone,
                slots_text=slots_text,
                selected_slot_index=0,
                selected_slot_dt=slots[0]["start"],
            ),
            output_schema=SchedulingDecision,
        )

        idx = max(0, min(decision.selected_slot_index, len(raw_slots) - 1))
        chosen_slot = raw_slots[idx]
        chosen_dict = slots[idx]

        logger.info(
            f"[scheduling] Chosen slot index={idx} start={chosen_dict['start']} "
            f"thread={thread_id}"
        )

        # 3 — Create Google Calendar event
        event = create_event(
            summary=f"Intro call with {prospect_name}",
            description="Introduction call scheduled via AI Email Agent.",
            start=chosen_slot.start,
            attendee_emails=[prospect_email] if prospect_email else [],
            duration_minutes=30,
            tz_name="UTC",
            add_meet_link=True,
        )
        event_id: str = event.event_id
        meet_link: str = event.meet_link or event.html_link or ""

        # 4 — Persist meeting to DB
        async with AsyncSessionLocal() as db:
            meeting = await get_or_create_meeting(db, thread_id)
            await confirm_meeting(db, meeting, event_id, chosen_slot.start)
            await db.commit()

        # 5 — Build reply instruction
        reply_instruction = (
            f"Confirm the meeting with {prospect_name} for "
            f"{_human_readable_slot(chosen_slot, prospect_timezone)}. "
            f"Include this Google Meet link: {meet_link}. "
            "Ask them to confirm it works and let them know a calendar invite is on its way."
        )

        return {
            **state,
            "available_slots": slots,
            "selected_slot": chosen_dict,
            "meeting_status": "confirmed",
            "google_event_id": event_id,
            "scheduled_at": chosen_slot.start.isoformat(),
            "reply_instruction": reply_instruction,
        }

    except Exception as exc:
        logger.error(f"[scheduling] thread={thread_id} error={exc}", exc_info=True)
        return {
            **state,
            "available_slots": [],
            "reply_instruction": (
                "Let the prospect know you're excited to connect and will send "
                "calendar availability shortly. Apologise for any delay."
            ),
            "error": str(exc),
            "error_node": "scheduling",
        }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fetch_slots(tz_name: str) -> list[TimeSlot]:
    """Fetch free 30-minute slots in the configured working-hours window."""
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
        logger.error(f"[scheduling] calendar fetch error: {exc}")
        return []


def _slots_to_dicts(slots: list[TimeSlot]) -> list[dict]:
    """Convert TimeSlot dataclasses to plain dicts for LLM prompt and state."""
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
    """Return a human-readable slot string in the prospect's timezone (cross-platform)."""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tz_name)
        dt = slot.start.astimezone(tz)
        # Use %d (zero-padded) — works on Windows and Linux alike
        day = str(dt.day)          # no leading zero
        hour = dt.strftime("%I").lstrip("0") or "12"
        return dt.strftime(f"%A, %B {day} at {hour}:%M %p %Z")
    except Exception:
        return slot.start.isoformat()
