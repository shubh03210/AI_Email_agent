"""
Node: scheduling
─────────────────
Books a meeting when the prospect is ready (intent: interested / accepted offer).

Flow:
  1. Fetches available calendar slots via calendar_service.
  2. Uses LLM to pick the best slot for the prospect's timezone.
  3. Creates the Google Calendar event.
  4. Persists meeting record to DB via memory_service.
  5. Sets reply_instruction for reply_generation.

Output keys added to state:
  available_slots, selected_slot, meeting_status, google_event_id,
  scheduled_at, reply_instruction
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone as dt_timezone

from app.agents.prompts import SCHEDULING_USER
from app.agents.state import AgentState
from app.core.config import settings
from app.core.logging import logger
from app.db.session import AsyncSessionLocal
from app.services.calendar_service import CalendarService
from app.services.llm_service import SchedulingDecision, get_llm_service
from app.services.memory_service import confirm_meeting, get_or_create_meeting


async def scheduling(state: AgentState) -> AgentState:
    """
    Find a free slot and book a meeting with the prospect.

    Reads:
        thread_id, conversation_text, prospect_timezone

    Writes:
        available_slots, selected_slot, meeting_status, google_event_id,
        scheduled_at, reply_instruction

    On error:
        Sets error + error_node, sets a fallback reply_instruction.
    """
    thread_id = state.get("thread_id")
    conversation_text = state.get("conversation_text", "")
    prospect_name = state.get("prospect_name", "there")
    prospect_email = state.get("prospect_email", "")
    prospect_timezone = state.get("prospect_timezone") or settings.AGENT_DEFAULT_TIMEZONE

    logger.info(f"[scheduling] thread={thread_id} tz={prospect_timezone}")

    try:
        cal = CalendarService()

        # 1 — Get free slots (next 5 working days, 30-min blocks)
        slots = await _get_slots(cal, prospect_timezone)

        if not slots:
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

        # 2 — LLM picks the best slot
        slots_text = _format_slots_text(slots)
        llm = get_llm_service()
        decision: SchedulingDecision = llm.generate_structured(
            system_prompt=_build_scheduling_system(),
            user_message=SCHEDULING_USER.format(
                conversation_text=conversation_text,
                prospect_timezone=prospect_timezone,
                slots_text=slots_text,
                selected_slot_index=0,
                selected_slot_dt=slots[0].get("start", ""),
            ),
            output_schema=SchedulingDecision,
        )

        idx = max(0, min(decision.selected_slot_index, len(slots) - 1))
        chosen_slot = slots[idx]
        slot_start: str = chosen_slot.get("start", "")

        logger.info(
            f"[scheduling] Chosen slot index={idx} start={slot_start} "
            f"thread={thread_id}"
        )

        # 3 — Create Google Calendar event
        event_id, event_link = await _create_event(
            cal, chosen_slot, prospect_name, prospect_email
        )

        # 4 — Persist to DB
        scheduled_dt = _parse_iso(slot_start)
        async with AsyncSessionLocal() as db:
            meeting = await get_or_create_meeting(db, thread_id)
            await confirm_meeting(db, meeting, event_id, scheduled_dt)
            await db.commit()

        # 5 — Build reply instruction
        reply_instruction = (
            f"Confirm the meeting with {prospect_name} for "
            f"{_human_readable_slot(chosen_slot, prospect_timezone)}. "
            f"Include this Google Meet link: {event_link}. "
            "Ask them to confirm it works and let them know a calendar invite is on its way."
        )

        return {
            **state,
            "available_slots": slots,
            "selected_slot": chosen_slot,
            "meeting_status": "confirmed",
            "google_event_id": event_id,
            "scheduled_at": slot_start,
            "reply_instruction": reply_instruction,
        }

    except Exception as exc:
        logger.error(
            f"[scheduling] thread={thread_id} error={exc}", exc_info=True
        )
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

async def _get_slots(cal: CalendarService, tz_name: str) -> list[dict]:
    """Fetch free 30-minute slots in the configured working hours window."""
    try:
        slots = cal.get_free_slots(
            duration_minutes=30,
            days_ahead=5,
            working_hours_start=settings.AGENT_DEFAULT_WORKING_HOURS_START,
            working_hours_end=settings.AGENT_DEFAULT_WORKING_HOURS_END,
            tz_name=tz_name,
        )
        return slots[:8]  # Cap at 8 slots for the LLM prompt
    except Exception as exc:
        logger.error(f"[scheduling] calendar fetch error: {exc}")
        return []


async def _create_event(
    cal: CalendarService,
    slot: dict,
    prospect_name: str,
    prospect_email: str,
) -> tuple[str, str]:
    """Create a Google Calendar event and return (event_id, meet_link)."""
    result = cal.create_event(
        title=f"Intro call with {prospect_name}",
        start_iso=slot["start"],
        end_iso=slot["end"],
        attendee_email=prospect_email,
        description="Introduction call scheduled via AI Email Agent.",
        add_meet_link=True,
    )
    event_id: str = result.get("id", "")
    # Google Meet link is in conferenceData.entryPoints
    meet_link = ""
    conf = result.get("conferenceData", {})
    for ep in conf.get("entryPoints", []):
        if ep.get("entryPointType") == "video":
            meet_link = ep.get("uri", "")
            break
    if not meet_link:
        meet_link = result.get("htmlLink", "")
    return event_id, meet_link


def _format_slots_text(slots: list[dict]) -> str:
    lines = []
    for i, s in enumerate(slots):
        lines.append(f"  [{i}] {s.get('start', '')} → {s.get('end', '')}")
    return "\n".join(lines)


def _human_readable_slot(slot: dict, tz_name: str) -> str:
    """Return a human-readable slot string in the prospect's timezone."""
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo(tz_name)
        dt = _parse_iso(slot["start"]).astimezone(tz)
        return dt.strftime("%A, %B %-d at %-I:%M %p %Z")
    except Exception:
        return slot.get("start", "TBD")


def _parse_iso(iso_str: str) -> datetime:
    """Parse an ISO-8601 string to a timezone-aware datetime."""
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=dt_timezone.utc)
        return dt
    except Exception:
        return datetime.now(dt_timezone.utc)


def _build_scheduling_system() -> str:
    from app.agents.prompts import SCHEDULING_SYSTEM
    return SCHEDULING_SYSTEM
