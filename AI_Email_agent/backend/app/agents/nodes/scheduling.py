"""
Node: scheduling
─────────────────
Books a meeting when the prospect is ready (intent: interested / accepted offer).

Flow:
  0. Human-escalation guard — if needs_human_review=True, skip automation.
  1. Validate and normalise prospect timezone (DST-safe via zoneinfo).
  2. Fetch available calendar slots via calendar_service.get_available_slots().
  3. LLM selects the best slot for the prospect's timezone.
  4. Atomically create the Google Calendar event and persist the DB record
     (calendar_ops.schedule_meeting_atomic).  On DB failure the calendar event
     is automatically deleted (compensation) so no orphaned events can accumulate.
  5. Set reply_instruction for reply_generation.

Error/failure handling:
  - CalendarOpError → increment calendar_failure_count; if threshold reached,
    set needs_human_review=True in DB and return escalation reply.
  - Any other exception → same treatment for consistency.

Output keys added to state:
  available_slots, selected_slot, meeting_status, google_event_id,
  scheduled_at, reply_instruction, needs_human_review, calendar_failure_count
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone as dt_timezone
from typing import Optional

from app.agents.prompts import SCHEDULING_SYSTEM, SCHEDULING_USER
from app.agents.state import AgentState
from app.core.config import settings
from app.core.logging import logger
from app.db.session import AsyncSessionLocal
from app.services.calendar_ops import (
    CalendarOpError,
    SchedulingResult,
    schedule_meeting_atomic,
    validate_timezone,
)
from app.services.calendar_service import TimeSlot, get_available_slots
from app.services.llm_service import SchedulingDecision, get_llm_service
from app.services.memory_service import increment_calendar_failure

# Reply instruction surfaced when the meeting is flagged for human review.
_HUMAN_REVIEW_REPLY = (
    "Apologise sincerely to the prospect for the repeated scheduling difficulties. "
    "Let them know that a team member will reach out directly to arrange a suitable "
    "time, and that you'll make sure it happens shortly. Keep the tone warm and "
    "reassuring."
)


async def scheduling(state: AgentState) -> AgentState:
    """
    Find a free slot and book a meeting with the prospect.

    Reads:
        thread_id, conversation_text, prospect_timezone, prospect_name,
        prospect_email, needs_human_review, calendar_failure_count

    Writes:
        available_slots, selected_slot, meeting_status, google_event_id,
        scheduled_at, reply_instruction, needs_human_review, calendar_failure_count
    """
    thread_id: int = state.get("thread_id")
    conversation_text: str = state.get("conversation_text", "")
    prospect_name: str = state.get("prospect_name", "there")
    prospect_email: str = state.get("prospect_email", "")

    # ── 0. Human-escalation guard ─────────────────────────────────────────────
    if state.get("needs_human_review"):
        logger.warning(
            f"[scheduling] thread={thread_id} is flagged for human review — "
            "skipping automation"
        )
        return {
            **state,
            "reply_instruction": _HUMAN_REVIEW_REPLY,
            "error": "Meeting flagged for human review — scheduling skipped.",
            "error_node": "scheduling",
        }

    # ── 1. Timezone validation ────────────────────────────────────────────────
    raw_tz: Optional[str] = state.get("prospect_timezone")
    prospect_timezone: str = validate_timezone(
        raw_tz, fallback=settings.AGENT_DEFAULT_TIMEZONE
    )
    if prospect_timezone != raw_tz:
        logger.warning(
            f"[scheduling] thread={thread_id} — invalid prospect_timezone "
            f"'{raw_tz}' corrected to '{prospect_timezone}'"
        )

    logger.info(f"[scheduling] thread={thread_id} tz={prospect_timezone}")

    try:
        # ── 2. Fetch free slots ───────────────────────────────────────────────
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

        slots = _slots_to_dicts(raw_slots)
        slots_text = _format_slots_text(slots)

        # ── 3. LLM selects best slot ──────────────────────────────────────────
        llm = get_llm_service()
        decision: SchedulingDecision = await llm.async_generate_structured(
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

        # ── 4. Atomic create event + DB persist ───────────────────────────────
        # schedule_meeting_atomic:
        #   • Creates calendar event
        #   • Commits to DB
        #   • On DB failure: deletes calendar event (compensation)
        result: SchedulingResult = await schedule_meeting_atomic(
            thread_id=thread_id,
            prospect_name=prospect_name,
            prospect_email=prospect_email,
            slot=chosen_slot,
        )
        event_id = result.google_event_id
        meet_link = result.meet_link

        # ── 5. Build reply instruction ────────────────────────────────────────
        # Use the operator-configured template when present (Phase 8 config),
        # otherwise fall back to the built-in confirmation message.
        confirmation_template = state.get("meeting_confirmation_template")
        if confirmation_template:
            reply_instruction = (
                confirmation_template
                .replace("{name}", prospect_name)
                .replace("{slot}", _human_readable_slot(chosen_slot, prospect_timezone))
                .replace("{meet_link}", meet_link)
            )
        else:
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
        logger.exception(f"[scheduling] thread={thread_id} failed: {exc}")

        # Increment failure counter; escalate if threshold reached
        new_count, needs_review = _handle_calendar_failure(thread_id)

        reply_instruction = _HUMAN_REVIEW_REPLY if needs_review else (
            "Let the prospect know you're excited to connect and will send "
            "calendar availability shortly. Apologise for any delay."
        )

        return {
            **state,
            "available_slots": [],
            "needs_human_review": needs_review,
            "calendar_failure_count": new_count,
            "reply_instruction": reply_instruction,
            "error": str(exc),
            "error_node": "scheduling",
        }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _handle_calendar_failure(thread_id: int) -> tuple[int, bool]:
    """
    Synchronous wrapper: increments calendar_failure_count in DB and returns
    (new_count, needs_human_review).  Uses a fresh event loop so it can be
    called from a synchronous except block.  Fails silently if DB is unreachable.
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
                f"[scheduling] Could not update failure count for "
                f"thread={thread_id}: {db_err}"
            )
            return 0, False

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We are already inside an async context (LangGraph) — schedule
            # the coroutine as a concurrent task on the existing loop.
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, _inner())
                return future.result(timeout=10)
        else:
            return loop.run_until_complete(_inner())
    except Exception as err:
        logger.warning(f"[scheduling] Failure count update skipped: {err}")
        return 0, False


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
    """Return a human-readable slot string in the prospect's timezone (DST-safe)."""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tz_name)
        dt = slot.start.astimezone(tz)
        day = str(dt.day)
        hour = dt.strftime("%I").lstrip("0") or "12"
        return dt.strftime(f"%A, %B {day} at {hour}:%M %p %Z")
    except Exception:
        return slot.start.isoformat()
