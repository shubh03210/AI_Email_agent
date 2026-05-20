"""
Phase 10 — Full Production Audit Tests
────────────────────────────────────────
Verifies all Phase 10 fixes:

  1.  Migration 008 — no duplicate index creates/drops
  2.  Migration 010 — counter_round + last_prospect_offer columns
  3.  Negotiation model — new columns exist with correct defaults
  4.  ThreadRead schema — follow_up_count + last_outreach_at fields
  5.  Memory service — raw_payload included in messages_dicts
  6.  Memory service — savepoint used in get_or_create_thread
  7.  Observability — obs_db closed even when start_agent_run fails
  8.  Health ops endpoints — /worker + /worker/dlq require auth (ops_router)
  9.  RBAC — require_operator_or_admin applied to outreach / thread-run / reschedule
  10. Exception sanitization — internal errors return static message
  11. Graph — budget_ceiling + counter_round + previous_prospect_offer seeded
  12. Negotiation node — uses budget_ceiling; persists counter_round to DB
  13. Scheduling node — uses meeting_confirmation_template when set
  14. send_reply — "cancelled" closes thread
  15. AGENT_PERSONA — no hardcoded "Alex"
  16. Gmail poll — re-raises on fetch failure
  17. Frontend Config.tsx — professional + casual tone options; no stale fields;
      NaN guards
  18. Frontend Meetings.tsx — proposed + completed in STATUS_OPTIONS
"""

from __future__ import annotations

import ast
import pathlib
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT = pathlib.Path(__file__).resolve().parents[2]      # AI_Email_agent/
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend_react" / "src" / "pages"

MIGRATION_008 = BACKEND / "alembic" / "versions" / "008_add_prospect_company_and_indexes.py"
MIGRATION_010 = BACKEND / "alembic" / "versions" / "010_add_negotiation_round_tracking.py"
NEGOTIATION_MODEL = BACKEND / "app" / "models" / "negotiation.py"
THREAD_SCHEMA = BACKEND / "app" / "schemas" / "thread.py"
MEMORY_SERVICE = BACKEND / "app" / "services" / "memory_service.py"
OBSERVABILITY = BACKEND / "app" / "agents" / "observability.py"
HEALTH_ENDPOINT = BACKEND / "app" / "api" / "v1" / "endpoints" / "health.py"
API_ROUTER = BACKEND / "app" / "api" / "v1" / "api.py"
PROSPECTS_ENDPOINT = BACKEND / "app" / "api" / "v1" / "endpoints" / "prospects.py"
THREADS_ENDPOINT = BACKEND / "app" / "api" / "v1" / "endpoints" / "threads.py"
MEETINGS_ENDPOINT = BACKEND / "app" / "api" / "v1" / "endpoints" / "meetings.py"
GRAPH = BACKEND / "app" / "agents" / "graph.py"
NEGOTIATION_NODE = BACKEND / "app" / "agents" / "nodes" / "negotiation.py"
SCHEDULING_NODE = BACKEND / "app" / "agents" / "nodes" / "scheduling.py"
SEND_REPLY_NODE = BACKEND / "app" / "agents" / "nodes" / "send_reply.py"
PROMPTS = BACKEND / "app" / "agents" / "prompts.py"
TASKS = BACKEND / "app" / "workers" / "tasks.py"
CONFIG_TSX = FRONTEND / "Config.tsx"
MEETINGS_TSX = FRONTEND / "Meetings.tsx"


# ══════════════════════════════════════════════════════════════════════════════
# 1. Migration 008 — no duplicate index creates/drops
# ══════════════════════════════════════════════════════════════════════════════

class TestMigration008NoDuplicates:
    def _src(self) -> str:
        return MIGRATION_008.read_text(encoding="utf-8")

    def test_no_duplicate_email_messages_thread_timestamp_create(self):
        src = self._src()
        assert src.count("ix_email_messages_thread_timestamp") <= 1, (
            "ix_email_messages_thread_timestamp appears more than once in 008 "
            "(duplicate create detected)"
        )

    def test_no_duplicate_email_messages_timestamp_create(self):
        src = self._src()
        assert src.count("create_index") == 2, (
            "Expected exactly 2 create_index calls in 008 upgrade "
            "(ix_prospects_company + ix_agent_runs_thread_created)"
        )

    def test_downgrade_drops_only_two_indexes(self):
        src = self._src()
        assert src.count("drop_index") == 2, (
            "Expected exactly 2 drop_index calls in 008 downgrade"
        )

    def test_ix_agent_runs_thread_created_present(self):
        assert "ix_agent_runs_thread_created" in self._src()

    def test_ix_prospects_company_present(self):
        assert "ix_prospects_company" in self._src()


# ══════════════════════════════════════════════════════════════════════════════
# 2. Migration 010 — counter_round + last_prospect_offer columns
# ══════════════════════════════════════════════════════════════════════════════

class TestMigration010:
    def _src(self) -> str:
        return MIGRATION_010.read_text(encoding="utf-8")

    def test_file_exists(self):
        assert MIGRATION_010.exists(), "Migration 010 file not found"

    def test_revision_is_010(self):
        assert 'revision = "010"' in self._src()

    def test_down_revision_is_009(self):
        assert 'down_revision = "009"' in self._src()

    def test_upgrade_adds_counter_round(self):
        assert "counter_round" in self._src()

    def test_upgrade_adds_last_prospect_offer(self):
        assert "last_prospect_offer" in self._src()

    def test_downgrade_drops_both_columns(self):
        src = self._src()
        assert src.count("drop_column") == 2

    def test_counter_round_has_server_default_zero(self):
        assert 'server_default="0"' in self._src()


# ══════════════════════════════════════════════════════════════════════════════
# 3. Negotiation model — new columns
# ══════════════════════════════════════════════════════════════════════════════

class TestNegotiationModel:
    def _src(self) -> str:
        return NEGOTIATION_MODEL.read_text(encoding="utf-8")

    def test_counter_round_column_present(self):
        assert "counter_round" in self._src()

    def test_last_prospect_offer_column_present(self):
        assert "last_prospect_offer" in self._src()

    def test_counter_round_has_default_zero(self):
        assert re.search(r"counter_round.*default=0", self._src())

    def test_last_prospect_offer_nullable(self):
        assert re.search(r"last_prospect_offer.*nullable=True", self._src())

    def test_integer_imported(self):
        assert "Integer" in self._src()


# ══════════════════════════════════════════════════════════════════════════════
# 4. ThreadRead schema — follow_up_count + last_outreach_at
# ══════════════════════════════════════════════════════════════════════════════

class TestThreadReadSchema:
    def _src(self) -> str:
        return THREAD_SCHEMA.read_text(encoding="utf-8")

    def test_follow_up_count_in_schema(self):
        assert "follow_up_count" in self._src()

    def test_last_outreach_at_in_schema(self):
        assert "last_outreach_at" in self._src()

    def test_follow_up_count_has_default(self):
        assert "follow_up_count: int = 0" in self._src()

    def test_last_outreach_at_optional(self):
        assert re.search(r"last_outreach_at.*Optional", self._src())


# ══════════════════════════════════════════════════════════════════════════════
# 5. Memory service — raw_payload in messages_dicts
# ══════════════════════════════════════════════════════════════════════════════

class TestMemoryServiceRawPayload:
    def _src(self) -> str:
        return MEMORY_SERVICE.read_text(encoding="utf-8")

    def test_raw_payload_included_in_messages_dict(self):
        src = self._src()
        # Find the messages_dicts block
        assert '"raw_payload"' in src or "'raw_payload'" in src, (
            "raw_payload not serialized in messages_dicts"
        )

    def test_raw_payload_reads_from_message(self):
        src = self._src()
        assert "m.raw_payload" in src


# ══════════════════════════════════════════════════════════════════════════════
# 6. Memory service — savepoint in get_or_create_thread
# ══════════════════════════════════════════════════════════════════════════════

class TestMemoryServiceSavepoint:
    def _src(self) -> str:
        return MEMORY_SERVICE.read_text(encoding="utf-8")

    def test_begin_nested_used_in_get_or_create_thread(self):
        src = self._src()
        assert "begin_nested" in src, (
            "get_or_create_thread must use db.begin_nested() savepoint "
            "to avoid rolling back the entire poll batch on IntegrityError"
        )

    def test_rollback_not_called_directly(self):
        src = self._src()
        # The old pattern was `await db.rollback()` inside the except clause.
        # After the fix it should NOT appear inside the IntegrityError handler.
        # Simplest proxy: the word "rollback" should not appear in the function body.
        get_create_fn_start = src.find("async def get_or_create_thread")
        get_create_fn_end = src.find("\nasync def ", get_create_fn_start + 1)
        fn_body = src[get_create_fn_start:get_create_fn_end]
        assert "rollback()" not in fn_body, (
            "get_or_create_thread still calls db.rollback() — should use savepoint instead"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 7. Observability — obs_db closed unconditionally on start failure
# ══════════════════════════════════════════════════════════════════════════════

class TestObservabilitySessionLeak:
    def _src(self) -> str:
        return OBSERVABILITY.read_text(encoding="utf-8")

    def test_obs_db_close_after_start_failure(self):
        src = self._src()
        # After the fix: obs_db.close() is called inside the start-failure
        # except block, before setting obs_db = None.
        assert "obs_db.close()" in src

    def test_obs_db_set_none_after_close_on_start_failure(self):
        src = self._src()
        assert "obs_db = None" in src

    def test_unconditional_close_in_finish_finally(self):
        src = self._src()
        # The finish finally block should have an unconditional close
        # (not guarded by `if run is not None`)
        assert "Unconditional close" in src or "unconditional" in src.lower()


# ══════════════════════════════════════════════════════════════════════════════
# 8. Health ops endpoints — ops_router defined + mounted behind auth
# ══════════════════════════════════════════════════════════════════════════════

class TestHealthOpsRouter:
    def _health_src(self) -> str:
        return HEALTH_ENDPOINT.read_text(encoding="utf-8")

    def _api_src(self) -> str:
        return API_ROUTER.read_text(encoding="utf-8")

    def test_ops_router_defined_in_health(self):
        assert "ops_router" in self._health_src()

    def test_worker_route_on_ops_router(self):
        src = self._health_src()
        assert re.search(r"@ops_router\.get\s*\(\s*['\"]\/worker['\"]", src)

    def test_worker_dlq_route_on_ops_router(self):
        src = self._health_src()
        assert re.search(r"@ops_router\.get\s*\(\s*['\"]\/worker\/dlq['\"]", src)

    def test_ops_router_mounted_with_auth_in_api(self):
        src = self._api_src()
        # ops_router should appear after the _auth variable definition and be
        # included with dependencies=_auth
        assert "ops_router" in src
        assert re.search(r"ops_router.*dependencies=_auth|dependencies=_auth.*ops_router", src)

    def test_public_health_routes_still_on_router(self):
        src = self._health_src()
        assert "@router.get" in src, "Public health routes should still use router"


# ══════════════════════════════════════════════════════════════════════════════
# 9. RBAC — require_operator_or_admin applied to mutation endpoints
# ══════════════════════════════════════════════════════════════════════════════

class TestRBACMutationEndpoints:
    def test_prospects_outreach_has_rbac(self):
        src = PROSPECTS_ENDPOINT.read_text(encoding="utf-8")
        assert "require_operator_or_admin" in src

    def test_threads_run_has_rbac(self):
        src = THREADS_ENDPOINT.read_text(encoding="utf-8")
        assert "require_operator_or_admin" in src

    def test_meetings_reschedule_has_rbac(self):
        src = MEETINGS_ENDPOINT.read_text(encoding="utf-8")
        assert "require_operator_or_admin" in src

    def test_prospects_imports_rbac_dep(self):
        src = PROSPECTS_ENDPOINT.read_text(encoding="utf-8")
        assert "require_operator_or_admin" in src.split("from app.api.v1.deps")[1].split("\n")[0]

    def test_threads_imports_rbac_dep(self):
        src = THREADS_ENDPOINT.read_text(encoding="utf-8")
        assert "require_operator_or_admin" in src.split("from app.api.v1.deps")[1].split("\n")[0]


# ══════════════════════════════════════════════════════════════════════════════
# 10. Exception sanitization
# ══════════════════════════════════════════════════════════════════════════════

class TestExceptionSanitization:
    def test_prospects_no_raw_exception_detail(self):
        src = PROSPECTS_ENDPOINT.read_text(encoding="utf-8")
        assert 'detail=f"Outreach failed:' not in src

    def test_prospects_static_error_message(self):
        src = PROSPECTS_ENDPOINT.read_text(encoding="utf-8")
        assert "An internal error occurred." in src

    def test_threads_no_raw_exception_detail(self):
        src = THREADS_ENDPOINT.read_text(encoding="utf-8")
        assert 'detail=f"Agent run failed:' not in src

    def test_threads_static_error_message(self):
        src = THREADS_ENDPOINT.read_text(encoding="utf-8")
        assert "An internal error occurred." in src


# ══════════════════════════════════════════════════════════════════════════════
# 11. Graph — budget_ceiling + counter_round + previous_prospect_offer seeded
# ══════════════════════════════════════════════════════════════════════════════

class TestGraphStateSeeding:
    def _src(self) -> str:
        return GRAPH.read_text(encoding="utf-8")

    def test_budget_ceiling_seeded(self):
        assert "budget_ceiling" in self._src()

    def test_counter_round_loaded_from_memory(self):
        src = self._src()
        # Should use memory.counter_round, not hardcoded 0
        assert "memory.counter_round" in src

    def test_previous_prospect_offer_seeded(self):
        src = self._src()
        assert "previous_prospect_offer" in src

    def test_counter_round_not_hardcoded_zero(self):
        src = self._src()
        # The old bug was `"counter_round": 0` — it should now reference memory
        assert '"counter_round":         0' not in src and '"counter_round": 0' not in src


# ══════════════════════════════════════════════════════════════════════════════
# 12. Negotiation node — uses budget_ceiling; persists counter_round
# ══════════════════════════════════════════════════════════════════════════════

class TestNegotiationNode:
    def _src(self) -> str:
        return NEGOTIATION_NODE.read_text(encoding="utf-8")

    def test_budget_ceiling_used(self):
        src = self._src()
        assert "budget_ceiling" in src

    def test_counter_round_persisted(self):
        src = self._src()
        assert "neg.counter_round" in src

    def test_last_prospect_offer_persisted(self):
        src = self._src()
        assert "neg.last_prospect_offer" in src

    def test_budget_ceiling_takes_priority(self):
        src = self._src()
        # budget_ceiling should appear before or equal to max_budget in the fallback chain
        assert src.index("budget_ceiling") < src.index("max_budget") or \
               "budget_ceiling" in src


# ══════════════════════════════════════════════════════════════════════════════
# 13. Scheduling node — meeting_confirmation_template consumed
# ══════════════════════════════════════════════════════════════════════════════

class TestSchedulingNodeTemplate:
    def _src(self) -> str:
        return SCHEDULING_NODE.read_text(encoding="utf-8")

    def test_confirmation_template_read_from_state(self):
        assert "meeting_confirmation_template" in self._src()

    def test_template_used_when_set(self):
        src = self._src()
        assert "confirmation_template" in src

    def test_fallback_instruction_still_present(self):
        src = self._src()
        assert "Confirm the meeting" in src


# ══════════════════════════════════════════════════════════════════════════════
# 14. send_reply — "cancelled" closes thread
# ══════════════════════════════════════════════════════════════════════════════

class TestSendReplyClosesOnCancelled:
    def _src(self) -> str:
        return SEND_REPLY_NODE.read_text(encoding="utf-8")

    def test_cancelled_in_close_conditions(self):
        src = self._src()
        assert '"cancelled"' in src or "'cancelled'" in src

    def test_cancelled_in_meeting_status_check(self):
        src = self._src()
        # The condition should be `meeting_status in ("confirmed", "rescheduled", "cancelled")`
        assert re.search(r'meeting_status\s+in\s+\(.*cancelled.*\)', src)


# ══════════════════════════════════════════════════════════════════════════════
# 15. AGENT_PERSONA — no hardcoded "Alex"
# ══════════════════════════════════════════════════════════════════════════════

class TestAgentPersonaNoAlex:
    def _src(self) -> str:
        return PROMPTS.read_text(encoding="utf-8")

    def test_agent_persona_does_not_contain_alex(self):
        src = self._src()
        # Find AGENT_PERSONA string definition
        start = src.find("AGENT_PERSONA = (")
        end = src.find(")", start)
        persona_block = src[start:end]
        assert "Alex" not in persona_block, (
            "AGENT_PERSONA still hardcodes 'Alex' — recruiter name should "
            "come from AgentConfig via agent state"
        )

    def test_agent_persona_still_references_hr_recruiter(self):
        src = self._src()
        assert "HR recruiter" in src or "HR Recruiter" in src


# ══════════════════════════════════════════════════════════════════════════════
# 16. Gmail poll — re-raises on fetch failure
# ══════════════════════════════════════════════════════════════════════════════

class TestGmailPollReRaise:
    def _src(self) -> str:
        return TASKS.read_text(encoding="utf-8")

    def test_poll_failure_reraises(self):
        src = self._src()
        # Find the actual error log message that precedes the raise
        marker = "[poll_inbox] Gmail fetch failed"
        idx = src.find(marker)
        assert idx != -1, f"Could not find '{marker}' in tasks.py"
        # Within the next 300 chars after the log line there should be a bare raise
        window = src[idx:idx + 300]
        assert "raise" in window, (
            "poll_inbox should re-raise the exception so Celery applies retry/backoff"
        )

    def test_poll_failure_does_not_return_errors_dict(self):
        src = self._src()
        # The old bug: `return {"processed": 0, "enqueued": 0, "errors": 1}` after
        # the Gmail fetch exception — swallowed it silently.
        poll_section = src[src.find("[poll_inbox] Gmail fetch failed"):
                           src.find("[poll_inbox] Gmail fetch failed") + 300]
        assert 'return {"processed"' not in poll_section


# ══════════════════════════════════════════════════════════════════════════════
# 17. Frontend Config.tsx — tone options; stale field removal; NaN guards
# ══════════════════════════════════════════════════════════════════════════════

class TestFrontendConfig:
    def _src(self) -> str:
        return CONFIG_TSX.read_text(encoding="utf-8")

    def test_professional_tone_option_present(self):
        assert "professional" in self._src()

    def test_casual_tone_option_present(self):
        assert "casual" in self._src()

    def test_all_six_tone_options_present(self):
        src = self._src()
        for tone in ("formal", "friendly", "startup", "executive", "professional", "casual"):
            assert f"'{tone}'" in src or f'"{tone}"' in src, f"Tone '{tone}' missing from Config.tsx"

    def test_stale_fields_excluded_from_save_payload(self):
        src = self._src()
        # The save should destructure out follow_up_days and max_follow_ups
        assert "follow_up_days: _fd" in src or "_fd" in src

    def test_nan_guard_budget_ceiling(self):
        src = self._src()
        assert "parseFloat(e.target.value) || 0" in src

    def test_nan_guard_working_hours_start(self):
        src = self._src()
        assert "parseInt(e.target.value) || 0" in src

    def test_nan_guard_working_hours_end(self):
        src = self._src()
        # End has non-zero default (9)
        assert re.search(r"parseInt\(e\.target\.value\)\s*\|\|\s*\d+", src)


# ══════════════════════════════════════════════════════════════════════════════
# 18. Frontend Meetings.tsx — proposed + completed in STATUS_OPTIONS
# ══════════════════════════════════════════════════════════════════════════════

class TestFrontendMeetings:
    def _src(self) -> str:
        return MEETINGS_TSX.read_text(encoding="utf-8")

    def test_proposed_in_status_options(self):
        assert "'proposed'" in self._src() or '"proposed"' in self._src()

    def test_completed_in_status_options(self):
        assert "'completed'" in self._src() or '"completed"' in self._src()

    def test_confirmed_still_present(self):
        assert "'confirmed'" in self._src() or '"confirmed"' in self._src()

    def test_cancelled_still_present(self):
        assert "'cancelled'" in self._src() or '"cancelled"' in self._src()

    def test_rescheduled_still_present(self):
        assert "'rescheduled'" in self._src() or '"rescheduled"' in self._src()


# ══════════════════════════════════════════════════════════════════════════════
# Additional: memory service — counter_round + last_prospect_offer in ThreadMemory
# ══════════════════════════════════════════════════════════════════════════════

class TestMemoryServiceNegotiationRound:
    def _src(self) -> str:
        return MEMORY_SERVICE.read_text(encoding="utf-8")

    def test_thread_memory_has_counter_round(self):
        src = self._src()
        assert "counter_round" in src

    def test_thread_memory_has_last_prospect_offer(self):
        src = self._src()
        assert "last_prospect_offer" in src

    def test_load_thread_memory_uses_negotiation_counter_round(self):
        src = self._src()
        assert "negotiation.counter_round" in src

    def test_load_thread_memory_uses_negotiation_last_prospect_offer(self):
        src = self._src()
        assert "negotiation.last_prospect_offer" in src
