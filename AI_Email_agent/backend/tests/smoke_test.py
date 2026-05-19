"""
Integration Smoke Test
───────────────────────
Validates every critical integration point before a demo.
Run from the backend/ directory:

    cd AI_Email_agent/backend
    python -m pytest tests/smoke_test.py -v
    # or without pytest:
    python tests/smoke_test.py

Checks (in order):
  1.  .env loaded correctly (no empty required keys)
  2.  DB reachable (asyncpg connection)
  3.  Gmail OAuth token valid (no interactive browser needed)
  4.  Calendar OAuth token valid
  5.  Groq LLM — basic text generation
  6.  LLM structured output (IntentClassification)
  7.  Negotiation service — pure logic (no DB)
  8.  Memory service — DB read/write roundtrip
  9.  Agent graph — compiles without error
  10. API layer — FastAPI app imports without error

Each check prints PASS / FAIL with a reason.
"""

from __future__ import annotations

import asyncio
import os
import sys
import traceback

# ── Windows asyncio fix (asyncpg needs SelectorEventLoop on Windows) ──────────
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# ── Ensure we run from backend/ so .env is found ─────────────────────────────
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(BACKEND_DIR)
sys.path.insert(0, BACKEND_DIR)

# ── Helpers ───────────────────────────────────────────────────────────────────

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
SKIP = "\033[93m  SKIP\033[0m"

results: list[tuple[str, bool, str]] = []

# Single event loop shared across all async tests to avoid
# asyncpg "attached to a different loop" errors on Python 3.9
_shared_loop = asyncio.new_event_loop()
asyncio.set_event_loop(_shared_loop)


def check(name: str, fn, *, skip_if: str = "") -> bool:
    if skip_if:
        results.append((name, True, f"SKIPPED — {skip_if}"))
        return True
    try:
        fn()
        results.append((name, True, ""))
        return True
    except Exception as exc:
        results.append((name, False, str(exc)))
        return False


def acheck(name: str, coro_fn, *, skip_if: str = "") -> bool:
    """
    Run an async test in the shared event loop.
    All async tests share one loop to avoid asyncpg 'attached to different loop' errors.
    """
    if skip_if:
        results.append((name, True, f"SKIPPED — {skip_if}"))
        return True
    try:
        _shared_loop.run_until_complete(coro_fn())
        results.append((name, True, ""))
        return True
    except Exception as exc:
        results.append((name, False, str(exc)))
        return False


# ══════════════════════════════════════════════════════════════════════════════

def test_env():
    """Verify all required .env keys are non-empty."""
    from app.core.config import settings
    required = {
        "GROQ_API_KEY":   settings.GROQ_API_KEY,
        "DATABASE_URL":   settings.DATABASE_URL,
        "SECRET_KEY":     settings.SECRET_KEY,
    }
    missing = [k for k, v in required.items() if not v or v.startswith("change-me")]
    if missing:
        raise ValueError(f"Missing / placeholder values: {missing}")


async def test_db():
    """Open a DB connection and run a trivial query."""
    from app.db.session import check_db_connection
    ok = await check_db_connection()
    if not ok:
        raise ConnectionError("DB connection returned False")


def test_gmail_token():
    """Check Gmail OAuth token exists and is valid (no browser prompt)."""
    from app.core.config import settings
    import os
    token_path = settings.GMAIL_TOKEN_JSON
    if not os.path.exists(token_path):
        raise FileNotFoundError(f"Gmail token not found at {token_path}")
    from google.oauth2.credentials import Credentials
    creds = Credentials.from_authorized_user_file(token_path, settings.GMAIL_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            from google.auth.transport.requests import Request
            creds.refresh(Request())
        else:
            raise ValueError("Gmail token invalid and cannot be refreshed silently")


def test_calendar_token():
    """Check Calendar OAuth token exists and is valid."""
    from app.core.config import settings
    import os
    token_path = settings.CALENDAR_TOKEN_JSON
    if not os.path.exists(token_path):
        raise FileNotFoundError(f"Calendar token not found at {token_path}")
    from google.oauth2.credentials import Credentials
    creds = Credentials.from_authorized_user_file(token_path, settings.CALENDAR_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            from google.auth.transport.requests import Request
            creds.refresh(Request())
        else:
            raise ValueError("Calendar token invalid and cannot be refreshed silently")


def test_groq_basic():
    """Fire a single-token generation to verify Groq API key works."""
    from app.services.llm_service import get_llm_service
    llm = get_llm_service()
    result = llm.generate(
        system_prompt="You are a test assistant.",
        user_message="Reply with the single word: OK",
    )
    if not result or len(result.strip()) == 0:
        raise ValueError("Empty response from Groq")


def test_groq_structured():
    """Verify structured output parsing works end-to-end."""
    from app.services.llm_service import IntentClassification, get_llm_service
    llm = get_llm_service()
    result: IntentClassification = llm.generate_structured(
        system_prompt="Classify the intent of prospect emails.",
        user_message=(
            "Classify this email: "
            "'Hi, I'm interested in the opportunity. When can we talk?'"
        ),
        output_schema=IntentClassification,
    )
    valid_intents = {
        "interested", "curious", "negotiating", "declined",
        "ambiguous", "reschedule", "unavailable", "unknown",
    }
    if result.intent.lower() not in valid_intents:
        raise ValueError(f"Unexpected intent: {result.intent}")
    if not (0.0 <= result.confidence <= 1.0):
        raise ValueError(f"Confidence out of range: {result.confidence}")


def test_negotiation_logic():
    """Pure business logic — no DB, no LLM."""
    from app.services.negotiation_service import evaluate_offer, NegotiationAction
    # Should ACCEPT
    r = evaluate_offer(prospect_offer=4000, max_budget=5000, current_offer=None, counter_round=0)
    assert r.action == NegotiationAction.ACCEPT, f"Expected ACCEPT, got {r.action}"
    # Should COUNTEROFFER
    r2 = evaluate_offer(prospect_offer=7000, max_budget=5000, current_offer=None, counter_round=0)
    assert r2.action == NegotiationAction.COUNTEROFFER, f"Expected COUNTEROFFER, got {r2.action}"
    # Should WALKAWAY after max rounds
    r3 = evaluate_offer(prospect_offer=7000, max_budget=5000, current_offer=4500, counter_round=3)
    assert r3.action == NegotiationAction.WALKAWAY, f"Expected WALKAWAY, got {r3.action}"


async def test_memory_service():
    """Create a thread, save a message, load memory — all in-transaction."""
    from app.db.session import AsyncSessionLocal
    import app.db.init_db  # register all models
    from app.models.prospect import Prospect, ProspectStatus
    from app.services.memory_service import get_or_create_thread, save_message, load_thread_memory
    from datetime import datetime, timezone as dt_timezone

    async with AsyncSessionLocal() as db:
        # Create a test prospect
        p = Prospect(
            name="Smoke Test User",
            email=f"smoke_test_{int(datetime.now().timestamp())}@test.invalid",
            timezone="UTC",
            status=ProspectStatus.PENDING.value,
        )
        db.add(p)
        await db.flush()

        # Create thread
        t = await get_or_create_thread(
            db,
            gmail_thread_id=f"smoke_thread_{p.id}",
            prospect_id=p.id,
            subject="Smoke Test Thread",
        )

        # Save a message
        await save_message(
            db=db,
            thread_id=t.id,
            sender="smoke@test.invalid",
            body="This is a smoke test message.",
            timestamp=datetime.now(dt_timezone.utc),
        )
        await db.flush()

        # Load memory
        mem = await load_thread_memory(db, t.id)
        assert mem is not None, "load_thread_memory returned None"
        assert len(mem.messages) >= 1, "No messages loaded"

        # Rollback — don't pollute the DB with test data
        await db.rollback()


def test_graph_compiles():
    """Build the LangGraph agent graph — catches import and config errors."""
    from app.agents.graph import build_graph
    g = build_graph()
    assert g is not None, "build_graph() returned None"


def test_api_imports():
    """Import the FastAPI app — catches missing dependencies and broken routes."""
    from app.main import app
    assert app is not None, "FastAPI app is None"
    routes = [r.path for r in app.routes]
    assert any("/health" in r for r in routes), "Health route not registered"


# ══════════════════════════════════════════════════════════════════════════════
# Run
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("\n" + "═" * 60)
    print("  Email Wake-Up Agent — Integration Smoke Test")
    print("═" * 60)

    check("1. .env keys loaded",            test_env)
    acheck("2. Database reachable",         test_db)
    check("3. Gmail OAuth token valid",     test_gmail_token)
    check("4. Calendar OAuth token valid",  test_calendar_token)
    check("5. Groq LLM — basic generation", test_groq_basic)
    check("6. Groq LLM — structured output", test_groq_structured)
    check("7. Negotiation service logic",   test_negotiation_logic)
    acheck("8. Memory service DB roundtrip", test_memory_service)
    check("9. Agent graph compilation",     test_graph_compiles)
    check("10. FastAPI app imports",        test_api_imports)

    print("\n" + "─" * 60)
    passed = sum(1 for _, ok, r in results if ok and not r.startswith("SKIPPED"))
    failed = sum(1 for _, ok, _ in results if not ok)
    skipped = sum(1 for _, ok, r in results if ok and r.startswith("SKIPPED"))

    for name, ok, reason in results:
        if ok and reason.startswith("SKIPPED"):
            tag = SKIP
        else:
            tag = PASS if ok else FAIL
        line = f"{tag}  {name}"
        if reason and not reason.startswith("SKIPPED"):
            line += f"\n        → {reason}"
        elif reason.startswith("SKIPPED"):
            line += f"  ({reason})"
        print(line)

    print("─" * 60)
    print(f"  {passed} passed · {failed} failed · {skipped} skipped")
    print("═" * 60 + "\n")

    sys.exit(0 if failed == 0 else 1)
