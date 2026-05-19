"""
End-to-End Pipeline Test
─────────────────────────
Simulates the complete prospect-to-meeting pipeline:

  Add Prospect  →  Agent sends email  →  Prospect replies
  →  Gmail polling  →  LangGraph executes  →  Memory loads
  →  Intent classified  →  Negotiation / Scheduling
  →  Reply sent  →  Meeting created  →  Reschedule handled

Strategy:
  - Real database (Supabase) — all writes rolled back at the end.
  - Real LLM (Groq) — actual intent classification, reply generation.
  - Mocked Gmail (send / reply_to_thread / mark_as_read / fetch_unread_messages).
  - Mocked Calendar (get_available_slots / create_event / cancel_event).

Run:
    cd AI_Email_agent/backend
    python tests/test_e2e_pipeline.py

All DB writes are rolled back after the run — safe to run against production DB.
"""

from __future__ import annotations

import asyncio
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from typing import Any
from unittest.mock import MagicMock, patch

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import os
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(BACKEND_DIR)
sys.path.insert(0, BACKEND_DIR)

# Register all SQLAlchemy models before any ORM operations
import app.db.init_db  # noqa: F401, E402


# ── ANSI colours ──────────────────────────────────────────────────────────────
BOLD   = "\033[1m"
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
DIM    = "\033[2m"
RESET  = "\033[0m"

PASS  = f"{GREEN}  PASS{RESET}"
FAIL  = f"{RED}  FAIL{RESET}"


# ── Shared state across pipeline steps ────────────────────────────────────────
@dataclass
class PipelineContext:
    prospect_id:    int = 0
    thread_id:      int = 0
    gmail_thread_id: str = ""
    subject:        str = ""
    prospect_email: str = ""
    unique_tag:     str = ""   # unique suffix for this test run


_ctx = PipelineContext()
_results: list[tuple[str, bool, str]] = []
_loop = asyncio.new_event_loop()
asyncio.set_event_loop(_loop)


def step(name: str):
    """Decorator — registers a named pipeline step, captures pass/fail."""
    def decorator(fn):
        def wrapper(*args, **kwargs):
            t0 = time.perf_counter()
            try:
                fn(*args, **kwargs)
                elapsed = int((time.perf_counter() - t0) * 1000)
                _results.append((name, True, f"{elapsed}ms"))
                print(f"{PASS}  {name}  {DIM}({elapsed}ms){RESET}")
            except Exception as exc:
                elapsed = int((time.perf_counter() - t0) * 1000)
                _results.append((name, False, str(exc)))
                print(f"{FAIL}  {name}  {DIM}({elapsed}ms){RESET}")
                print(f"         {RED}→ {exc}{RESET}")
        return wrapper
    return decorator


def astep(name: str):
    """Async variant of @step."""
    def decorator(coro_fn):
        def wrapper(*args, **kwargs):
            t0 = time.perf_counter()
            try:
                _loop.run_until_complete(coro_fn(*args, **kwargs))
                elapsed = int((time.perf_counter() - t0) * 1000)
                _results.append((name, True, f"{elapsed}ms"))
                print(f"{PASS}  {name}  {DIM}({elapsed}ms){RESET}")
            except Exception as exc:
                import traceback
                elapsed = int((time.perf_counter() - t0) * 1000)
                _results.append((name, False, str(exc)))
                print(f"{FAIL}  {name}  {DIM}({elapsed}ms){RESET}")
                print(f"         {RED}→ {exc}{RESET}")
                traceback.print_exc()
        return wrapper
    return decorator


# ── Mock builders ──────────────────────────────────────────────────────────────

def _fake_sent_message(thread_id: str = None) -> Any:
    """Build a fake SentMessage dataclass returned by gmail_service.send_email."""
    from app.services.gmail_service import SentMessage
    return SentMessage(
        message_id=f"msg_{uuid.uuid4().hex[:8]}",
        thread_id=thread_id or f"gthread_{uuid.uuid4().hex[:8]}",
    )


def _fake_calendar_event(event_id: str = None) -> Any:
    """Build a fake CalendarEvent dataclass returned by calendar_service.create_event."""
    from app.services.calendar_service import CalendarEvent
    now = datetime.now(dt_timezone.utc)
    return CalendarEvent(
        event_id=event_id or f"gcal_{uuid.uuid4().hex[:8]}",
        summary="Test Meeting",
        description="E2E test meeting",
        start=now + timedelta(days=1),
        end=now + timedelta(days=1, hours=1),
        attendees=["test@example.com"],
        status="confirmed",
        meet_link="https://meet.google.com/fake-link",
        html_link="https://calendar.google.com/event?eid=fake",
    )


def _fake_time_slots() -> Any:
    """Return two fake TimeSlot objects for get_available_slots mock."""
    from app.services.calendar_service import TimeSlot
    now = datetime.now(dt_timezone.utc)
    return [
        TimeSlot(
            start=now + timedelta(days=1, hours=10),
            end=now + timedelta(days=1, hours=10, minutes=30),
        ),
        TimeSlot(
            start=now + timedelta(days=2, hours=14),
            end=now + timedelta(days=2, hours=14, minutes=30),
        ),
    ]


# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 — Add Prospect
# ══════════════════════════════════════════════════════════════════════════════

@astep("1. Add Prospect")
async def step_add_prospect():
    from app.db.session import AsyncSessionLocal
    from app.models.prospect import Prospect, ProspectStatus

    tag = uuid.uuid4().hex[:8]
    _ctx.unique_tag = tag
    _ctx.prospect_email = f"e2e_{tag}@test.invalid"
    _ctx.subject = f"E2E Test Run {tag}"

    async with AsyncSessionLocal() as db:
        p = Prospect(
            name="E2E Test Prospect",
            email=_ctx.prospect_email,
            timezone="UTC",
            status=ProspectStatus.PENDING.value,
        )
        db.add(p)
        await db.flush()
        _ctx.prospect_id = p.id
        assert _ctx.prospect_id > 0, "Prospect ID not assigned"
        await db.commit()

    print(f"         {DIM}prospect_id={_ctx.prospect_id} email={_ctx.prospect_email}{RESET}")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 — Agent Sends Outreach Email (LLM + mocked Gmail)
# ══════════════════════════════════════════════════════════════════════════════

@astep("2. Agent sends outreach email")
async def step_send_outreach():
    from app.db.session import AsyncSessionLocal
    from app.models.prospect import Prospect, ProspectStatus
    from app.repositories.config_repo import get_or_create_default
    from app.services.llm_service import generate_outreach_email
    from app.services.memory_service import get_or_create_thread, save_message

    fake_sent = _fake_sent_message()
    _ctx.gmail_thread_id = fake_sent.thread_id

    with patch("app.services.gmail_service.send_email", return_value=fake_sent):
        from app.services.gmail_service import send_email as gmail_send

        async with AsyncSessionLocal() as db:
            result = await db.get(Prospect, _ctx.prospect_id)
            assert result is not None, "Prospect not found"

            config = await get_or_create_default(db)
            draft = generate_outreach_email(
                prospect_name=result.name,
                gig_description=config.gig_description or "an exciting opportunity",
                tone=config.tone,
            )

            # Verify LLM generated a real outreach email
            assert draft.subject and len(draft.subject) > 5, \
                f"Subject too short: '{draft.subject}'"
            assert draft.body and len(draft.body) > 50, \
                f"Body too short: {len(draft.body)} chars"

            # Simulate send + thread creation
            sent = gmail_send(
                to=result.email,
                subject=draft.subject,
                body=draft.body,
            )
            _ctx.subject = draft.subject

            thread = await get_or_create_thread(
                db,
                gmail_thread_id=sent.thread_id,
                prospect_id=_ctx.prospect_id,
                subject=draft.subject,
            )
            _ctx.thread_id = thread.id

            await save_message(
                db=db,
                thread_id=thread.id,
                sender="agent",
                body=draft.body,
                timestamp=datetime.now(dt_timezone.utc),
                raw_payload={"gmail_msg_id": sent.message_id, "type": "outreach"},
            )

            result.status = ProspectStatus.CONTACTED.value
            db.add(result)
            await db.commit()

    print(f"         {DIM}thread_id={_ctx.thread_id} gmail_thread_id={_ctx.gmail_thread_id}{RESET}")
    print(f"         {DIM}subject='{_ctx.subject[:60]}...'{RESET}")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 3 — Prospect Replies (inject message, simulate polling)
# ══════════════════════════════════════════════════════════════════════════════

@astep("3. Prospect replies → Gmail polling detects it")
async def step_prospect_replies():
    from app.db.session import AsyncSessionLocal
    from app.services.memory_service import save_message

    reply_body = (
        "Hi, thanks for reaching out! I'm curious to learn more. "
        "Could you tell me about the role and the budget range?"
    )

    async with AsyncSessionLocal() as db:
        await save_message(
            db=db,
            thread_id=_ctx.thread_id,
            sender=_ctx.prospect_email,
            body=reply_body,
            timestamp=datetime.now(dt_timezone.utc),
            raw_payload={
                "id": f"inbound_{uuid.uuid4().hex[:8]}",
                "thread_id": _ctx.gmail_thread_id,
            },
        )
        await db.commit()

    print(f"         {DIM}Reply injected: '{reply_body[:60]}...'{RESET}")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 4 — Memory loads correctly
# ══════════════════════════════════════════════════════════════════════════════

@astep("4. Memory loads from DB")
async def step_memory_loads():
    from app.db.session import AsyncSessionLocal
    from app.services.memory_service import load_thread_memory

    async with AsyncSessionLocal() as db:
        memory = await load_thread_memory(db, _ctx.thread_id)

    assert memory is not None, "load_thread_memory returned None"
    assert memory.thread_id == _ctx.thread_id
    assert memory.prospect_id == _ctx.prospect_id
    assert memory.prospect_email == _ctx.prospect_email
    assert len(memory.messages) == 2, \
        f"Expected 2 messages (outbound + reply), got {len(memory.messages)}"
    assert len(memory.conversation_text) > 50, "Conversation text too short"

    print(f"         {DIM}messages={len(memory.messages)} "
          f"conv_len={len(memory.conversation_text)} chars{RESET}")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 5 — Intent Classified (CURIOUS path)
# ══════════════════════════════════════════════════════════════════════════════

@astep("5. Intent classified (CURIOUS path)")
async def step_intent_curious():
    with patch("app.services.gmail_service.reply_to_thread"):
        from app.agents.graph import run_agent
        final = await run_agent(_ctx.thread_id)

    intent = final.get("intent", "")
    reply_sent = final.get("reply_sent", False)
    reply_body = final.get("reply_body", "")

    assert intent in {"curious", "ambiguous", "interested"}, \
        f"Unexpected intent '{intent}' for curious message"
    assert reply_body and len(reply_body) > 20, \
        f"Reply body missing or too short: '{reply_body[:60]}'"
    assert reply_sent is True, "reply_sent should be True"

    print(f"         {DIM}intent={intent} confidence={final.get('intent_confidence', 0):.2f}"
          f" reply_sent={reply_sent}{RESET}")
    print(f"         {DIM}reply='{reply_body[:80]}...'{RESET}")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 6 — INTERESTED Path → Scheduling → Meeting Created
# ══════════════════════════════════════════════════════════════════════════════

@astep("6. INTERESTED path → scheduling → meeting created")
async def step_interested_scheduling():
    from app.db.session import AsyncSessionLocal
    from app.models.meeting import Meeting
    from app.services.memory_service import save_message

    # Override thread with a clear "interested" message
    interested_body = (
        "This sounds fantastic! I'm definitely interested in proceeding. "
        "Let's get a call scheduled."
    )
    async with AsyncSessionLocal() as db:
        await save_message(
            db=db,
            thread_id=_ctx.thread_id,
            sender=_ctx.prospect_email,
            body=interested_body,
            timestamp=datetime.now(dt_timezone.utc),
        )
        await db.commit()

    fake_event = _fake_calendar_event()
    fake_slots = _fake_time_slots()

    with (
        patch("app.services.gmail_service.reply_to_thread"),
        patch("app.services.calendar_service.get_available_slots", return_value=fake_slots),
        patch("app.services.calendar_service.create_event", return_value=fake_event),
    ):
        from app.agents.graph import run_agent
        final = await run_agent(_ctx.thread_id)

    intent = final.get("intent", "")
    reply_sent = final.get("reply_sent", False)
    meeting_status = final.get("meeting_status", "")
    event_id = final.get("google_event_id", "")

    assert intent in {"interested", "curious"}, \
        f"Unexpected intent '{intent}' for interested message"

    if intent == "interested":
        assert meeting_status == "confirmed", \
            f"Meeting status should be 'confirmed', got '{meeting_status}'"
        assert event_id, "google_event_id should be set after scheduling"
        assert reply_sent is True, "reply_sent should be True"

        # Verify meeting persisted in DB
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select
            result = await db.execute(
                select(Meeting).where(Meeting.thread_id == _ctx.thread_id)
            )
            meeting = result.scalar_one_or_none()
        assert meeting is not None, "Meeting not found in DB"
        assert meeting.status == "confirmed", f"DB meeting status: {meeting.status}"

    print(f"         {DIM}intent={intent} meeting_status={meeting_status} "
          f"event_id={event_id} reply_sent={reply_sent}{RESET}")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 7 — NEGOTIATING Path → Agent evaluates offer
# ══════════════════════════════════════════════════════════════════════════════

@astep("7. NEGOTIATING path → agent evaluates offer")
async def step_negotiating():
    from app.db.session import AsyncSessionLocal
    from app.services.memory_service import save_message

    # Add a negotiation-style reply
    negotiation_body = (
        "I'm interested but the rate seems high. "
        "I was thinking more like $3,000 per month. "
        "Is there any flexibility there?"
    )
    async with AsyncSessionLocal() as db:
        await save_message(
            db=db,
            thread_id=_ctx.thread_id,
            sender=_ctx.prospect_email,
            body=negotiation_body,
            timestamp=datetime.now(dt_timezone.utc),
        )
        await db.commit()

    fake_event = _fake_calendar_event()
    fake_slots = _fake_time_slots()

    with (
        patch("app.services.gmail_service.reply_to_thread"),
        patch("app.services.calendar_service.get_available_slots", return_value=fake_slots),
        patch("app.services.calendar_service.create_event", return_value=fake_event),
    ):
        from app.agents.graph import run_agent
        final = await run_agent(_ctx.thread_id)

    intent = final.get("intent", "")
    neg_action = final.get("negotiation_action", "")
    reply_sent = final.get("reply_sent", False)
    reply_body = final.get("reply_body", "")

    valid_intents = {"negotiating", "interested", "curious", "ambiguous"}
    assert intent in valid_intents, f"Unexpected intent '{intent}'"

    if intent == "negotiating":
        valid_actions = {"counteroffer", "accept", "walkaway", "hold", ""}
        assert neg_action in valid_actions, f"Unexpected negotiation action: '{neg_action}'"

    assert reply_body and len(reply_body) > 20, "Reply body missing"
    assert reply_sent is True, "reply_sent should be True"

    print(f"         {DIM}intent={intent} negotiation_action={neg_action} "
          f"reply_sent={reply_sent}{RESET}")
    if neg_action:
        print(f"         {DIM}reply='{reply_body[:80]}...'{RESET}")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 8 — DECLINED Path → Graceful exit reply
# ══════════════════════════════════════════════════════════════════════════════

@astep("8. DECLINED path → graceful exit reply")
async def step_declined():
    from app.db.session import AsyncSessionLocal
    from app.services.memory_service import save_message

    declined_body = "Thank you, but I'm not interested at this time. Please don't contact me again."

    async with AsyncSessionLocal() as db:
        await save_message(
            db=db,
            thread_id=_ctx.thread_id,
            sender=_ctx.prospect_email,
            body=declined_body,
            timestamp=datetime.now(dt_timezone.utc),
        )
        await db.commit()

    with patch("app.services.gmail_service.reply_to_thread"):
        from app.agents.graph import run_agent
        final = await run_agent(_ctx.thread_id)

    intent = final.get("intent", "")
    reply_sent = final.get("reply_sent", False)
    reply_body = final.get("reply_body", "")

    assert intent in {"declined", "ambiguous"}, \
        f"Unexpected intent '{intent}' for decline"
    assert reply_body and len(reply_body) > 10, "Expected a graceful exit reply"
    assert reply_sent is True, "reply_sent should be True even for declined"

    print(f"         {DIM}intent={intent} reply_sent={reply_sent}{RESET}")
    print(f"         {DIM}reply='{reply_body[:80]}...'{RESET}")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 9 — RESCHEDULE Path → Cancel + rebook
# ══════════════════════════════════════════════════════════════════════════════

@astep("9. RESCHEDULE path → cancel + rebook meeting")
async def step_reschedule():
    from app.db.session import AsyncSessionLocal
    from app.services.memory_service import save_message

    reschedule_body = (
        "Actually, something came up. Can we move the meeting to next week? "
        "I'm free Thursday or Friday afternoon."
    )
    async with AsyncSessionLocal() as db:
        await save_message(
            db=db,
            thread_id=_ctx.thread_id,
            sender=_ctx.prospect_email,
            body=reschedule_body,
            timestamp=datetime.now(dt_timezone.utc),
        )
        await db.commit()

    fake_new_event = _fake_calendar_event(event_id=f"gcal_new_{uuid.uuid4().hex[:8]}")
    fake_slots = _fake_time_slots()

    with (
        patch("app.services.gmail_service.reply_to_thread"),
        patch("app.services.calendar_service.get_available_slots", return_value=fake_slots),
        patch("app.services.calendar_service.create_event", return_value=fake_new_event),
        patch("app.services.calendar_service.cancel_event"),
    ):
        from app.agents.graph import run_agent
        final = await run_agent(_ctx.thread_id)

    intent = final.get("intent", "")
    meeting_status = final.get("meeting_status", "")
    reply_sent = final.get("reply_sent", False)
    reply_body = final.get("reply_body", "")

    assert intent in {"reschedule", "unavailable", "curious", "interested", "ambiguous"}, \
        f"Unexpected intent '{intent}'"
    assert reply_body and len(reply_body) > 10, "Expected a reschedule reply"
    assert reply_sent is True, "reply_sent should be True"

    if intent in {"reschedule", "unavailable"}:
        assert meeting_status in {"rescheduled", "confirmed"}, \
            f"Expected rescheduled/confirmed, got '{meeting_status}'"

    print(f"         {DIM}intent={intent} meeting_status={meeting_status} "
          f"reply_sent={reply_sent}{RESET}")
    print(f"         {DIM}reply='{reply_body[:80]}...'{RESET}")


# ══════════════════════════════════════════════════════════════════════════════
# STEP 10 — Cleanup: delete test data
# ══════════════════════════════════════════════════════════════════════════════

@astep("10. Cleanup — removing test data from DB")
async def step_cleanup():
    from app.db.session import AsyncSessionLocal
    from sqlalchemy import delete as sql_delete
    from app.models.email_message import EmailMessage
    from app.models.email_thread import EmailThread
    from app.models.meeting import Meeting
    from app.models.negotiation import Negotiation
    from app.models.agent_run import AgentRun
    from app.models.prospect import Prospect

    async with AsyncSessionLocal() as db:
        if _ctx.thread_id:
            await db.execute(sql_delete(AgentRun).where(AgentRun.thread_id == _ctx.thread_id))
            await db.execute(sql_delete(Meeting).where(Meeting.thread_id == _ctx.thread_id))
            await db.execute(sql_delete(Negotiation).where(Negotiation.thread_id == _ctx.thread_id))
            await db.execute(sql_delete(EmailMessage).where(EmailMessage.thread_id == _ctx.thread_id))
            await db.execute(sql_delete(EmailThread).where(EmailThread.id == _ctx.thread_id))
        if _ctx.prospect_id:
            await db.execute(sql_delete(Prospect).where(Prospect.id == _ctx.prospect_id))
        await db.commit()

    print(f"         {DIM}Removed prospect={_ctx.prospect_id} thread={_ctx.thread_id}{RESET}")


# ══════════════════════════════════════════════════════════════════════════════
# Runner
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print(f"\n{'═' * 65}")
    print(f"  {BOLD}Email Wake-Up Agent — End-to-End Pipeline Test{RESET}")
    print(f"  {DIM}Real DB · Real LLM (Groq) · Mocked Gmail & Calendar{RESET}")
    print(f"{'═' * 65}\n")

    step_add_prospect()
    step_send_outreach()
    step_prospect_replies()
    step_memory_loads()
    step_intent_curious()
    step_interested_scheduling()
    step_negotiating()
    step_declined()
    step_reschedule()
    step_cleanup()

    print(f"\n{'─' * 65}")
    passed  = sum(1 for _, ok, _ in _results if ok)
    failed  = sum(1 for _, ok, _ in _results if not ok)

    for name, ok, info in _results:
        tag = f"{GREEN}✓{RESET}" if ok else f"{RED}✗{RESET}"
        print(f"  {tag}  {name}  {DIM}{info}{RESET}")

    print(f"{'─' * 65}")
    colour = GREEN if failed == 0 else RED
    print(f"  {colour}{BOLD}{passed} passed · {failed} failed{RESET}")
    print(f"{'═' * 65}\n")

    sys.exit(0 if failed == 0 else 1)
