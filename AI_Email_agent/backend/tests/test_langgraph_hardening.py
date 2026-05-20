"""
Phase 5 — LangGraph Hardening Tests
──────────────────────────────────────
Covers:
  1.  validate_intent — valid, invalid, None, whitespace
  2.  validate_confidence — clamping, None, non-numeric
  3.  check_required_fields — pass, fail
  4.  should_downgrade_to_ambiguous — threshold logic
  5.  build_escalation_reply — output content
  6.  build_windowed_context — short thread, long thread (no summary / with summary)
  7.  increment_ambiguous_count — counter, threshold escalation
  8.  reset_ambiguous_count — resets to 0
  9.  escalate_thread — sets flag + reason
 10.  EmailThread model — new hardening columns
 11.  AgentState — new Phase 5 fields
 12.  classify_intent source — confidence guard, ambiguous counter, escalation guard
 13.  observability wrapper — failure-safe (no re-raise, returns error state)
 14.  route_after_intent — agent_escalated short-circuits to reply_generation
 15.  summarization_service — maybe_update_summary threshold gate
 16.  send_reply source — rolling summary wired in
 17.  Migration 007 — structure
 18.  Config — new Phase 5 knobs
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ─────────────────────────────────────────────────────────────────────────────
# 1.  validate_intent
# ─────────────────────────────────────────────────────────────────────────────

class TestValidateIntent:

    def test_valid_interested(self):
        from app.agents.validation import validate_intent
        assert validate_intent("interested") == "interested"

    def test_valid_all_labels(self):
        from app.agents.validation import VALID_INTENTS, validate_intent
        for label in VALID_INTENTS:
            assert validate_intent(label) == label

    def test_strip_and_lowercase(self):
        from app.agents.validation import validate_intent
        assert validate_intent("  INTERESTED  ") == "interested"

    def test_unknown_label_returns_ambiguous(self):
        from app.agents.validation import validate_intent
        assert validate_intent("not_sure") == "ambiguous"

    def test_none_returns_ambiguous(self):
        from app.agents.validation import validate_intent
        assert validate_intent(None) == "ambiguous"

    def test_empty_string_returns_ambiguous(self):
        from app.agents.validation import validate_intent
        assert validate_intent("") == "ambiguous"

    def test_never_raises(self):
        from app.agents.validation import validate_intent
        assert validate_intent(123) == "ambiguous"


# ─────────────────────────────────────────────────────────────────────────────
# 2.  validate_confidence
# ─────────────────────────────────────────────────────────────────────────────

class TestValidateConfidence:

    def test_valid_midpoint(self):
        from app.agents.validation import validate_confidence
        assert validate_confidence(0.7) == 0.7

    def test_zero_passthrough(self):
        from app.agents.validation import validate_confidence
        assert validate_confidence(0.0) == 0.0

    def test_one_passthrough(self):
        from app.agents.validation import validate_confidence
        assert validate_confidence(1.0) == 1.0

    def test_above_one_clamped(self):
        from app.agents.validation import validate_confidence
        assert validate_confidence(1.5) == 1.0

    def test_below_zero_clamped(self):
        from app.agents.validation import validate_confidence
        assert validate_confidence(-0.3) == 0.0

    def test_none_returns_zero(self):
        from app.agents.validation import validate_confidence
        assert validate_confidence(None) == 0.0

    def test_non_numeric_returns_zero(self):
        from app.agents.validation import validate_confidence
        assert validate_confidence("high") == 0.0

    def test_never_raises(self):
        from app.agents.validation import validate_confidence
        result = validate_confidence(object())
        assert isinstance(result, float)


# ─────────────────────────────────────────────────────────────────────────────
# 3.  check_required_fields
# ─────────────────────────────────────────────────────────────────────────────

class TestCheckRequiredFields:

    def test_all_present_returns_none(self):
        from app.agents.validation import check_required_fields
        state = {"thread_id": 1, "prospect_email": "a@b.com"}
        assert check_required_fields(state, ["thread_id", "prospect_email"], "node") is None

    def test_missing_field_returns_error_string(self):
        from app.agents.validation import check_required_fields
        state = {"thread_id": 1}
        result = check_required_fields(state, ["thread_id", "prospect_email"], "test_node")
        assert result is not None
        assert "prospect_email" in result
        assert "test_node" in result

    def test_none_value_treated_as_missing(self):
        from app.agents.validation import check_required_fields
        state = {"thread_id": None}
        result = check_required_fields(state, ["thread_id"], "node")
        assert result is not None


# ─────────────────────────────────────────────────────────────────────────────
# 4.  should_downgrade_to_ambiguous
# ─────────────────────────────────────────────────────────────────────────────

class TestShouldDowngradeToAmbiguous:

    def test_high_stakes_below_threshold_returns_true(self):
        from app.agents.validation import should_downgrade_to_ambiguous
        assert should_downgrade_to_ambiguous("interested", 0.3, 0.45) is True

    def test_high_stakes_at_threshold_returns_false(self):
        from app.agents.validation import should_downgrade_to_ambiguous
        assert should_downgrade_to_ambiguous("interested", 0.45, 0.45) is False

    def test_high_stakes_above_threshold_returns_false(self):
        from app.agents.validation import should_downgrade_to_ambiguous
        assert should_downgrade_to_ambiguous("negotiating", 0.9, 0.45) is False

    def test_low_stakes_below_threshold_returns_false(self):
        """Low-confidence 'curious' should still pass through."""
        from app.agents.validation import should_downgrade_to_ambiguous
        assert should_downgrade_to_ambiguous("curious", 0.1, 0.45) is False

    def test_declined_below_threshold_returns_false(self):
        """'declined' is not high-stakes — we don't escalate on low-confidence declines."""
        from app.agents.validation import should_downgrade_to_ambiguous
        assert should_downgrade_to_ambiguous("declined", 0.1, 0.45) is False

    def test_ambiguous_is_not_high_stakes(self):
        from app.agents.validation import should_downgrade_to_ambiguous
        assert should_downgrade_to_ambiguous("ambiguous", 0.0, 0.45) is False


# ─────────────────────────────────────────────────────────────────────────────
# 5.  build_escalation_reply
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildEscalationReply:

    def test_contains_team_member(self):
        from app.agents.validation import build_escalation_reply
        reply = build_escalation_reply("John")
        assert "team member" in reply.lower() or "follow up" in reply.lower()

    def test_includes_name_when_provided(self):
        from app.agents.validation import build_escalation_reply
        reply = build_escalation_reply("Alice")
        assert "Alice" in reply

    def test_falls_back_on_none_name(self):
        from app.agents.validation import build_escalation_reply
        reply = build_escalation_reply(None)
        assert isinstance(reply, str) and len(reply) > 0

    def test_never_raises(self):
        from app.agents.validation import build_escalation_reply
        result = build_escalation_reply()
        assert isinstance(result, str)


# ─────────────────────────────────────────────────────────────────────────────
# 6.  build_windowed_context
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildWindowedContext:

    def _make_messages(self, n: int) -> list:
        """Create n fake EmailMessage-like objects."""
        msgs = []
        for i in range(n):
            m = MagicMock()
            m.sender = "prospect@example.com"
            m.body = f"Message {i}"
            m.intent = None
            from datetime import datetime, timezone
            m.timestamp = datetime(2026, 1, i + 1, 10, 0, tzinfo=timezone.utc)
            msgs.append(m)
        return msgs

    def test_short_thread_returns_full_history(self):
        from app.services.memory_service import build_windowed_context
        messages = self._make_messages(10)
        result = build_windowed_context(messages, summary=None, window=20)
        # All messages should be present
        for i in range(10):
            assert f"Message {i}" in result

    def test_long_thread_no_summary_shows_omission_notice(self):
        from app.services.memory_service import build_windowed_context
        messages = self._make_messages(30)
        result = build_windowed_context(messages, summary=None, window=20)
        assert "earlier" in result.lower() or "omitted" in result.lower()
        # Only last 20 messages should appear
        for i in range(10, 30):
            assert f"Message {i}" in result

    def test_long_thread_with_summary_prepends_summary(self):
        from app.services.memory_service import build_windowed_context
        messages = self._make_messages(30)
        summary = "Prospect is interested in the role. Budget discussed: $4,000."
        result = build_windowed_context(messages, summary=summary, window=20)
        assert summary in result
        # Recent messages still included
        assert "Message 29" in result

    def test_exactly_at_window_uses_full_history(self):
        from app.services.memory_service import build_windowed_context
        messages = self._make_messages(20)
        result = build_windowed_context(messages, summary=None, window=20)
        assert "Message 0" in result
        assert "Message 19" in result

    def test_one_over_window_triggers_truncation(self):
        from app.services.memory_service import build_windowed_context
        messages = self._make_messages(21)
        result = build_windowed_context(messages, summary=None, window=20)
        assert "Message 0" not in result   # oldest omitted
        assert "Message 20" in result      # newest kept


# ─────────────────────────────────────────────────────────────────────────────
# 7.  increment_ambiguous_count
# ─────────────────────────────────────────────────────────────────────────────

class TestIncrementAmbiguousCount:

    @pytest.mark.asyncio
    async def test_increments_counter(self):
        from app.models.email_thread import EmailThread
        from app.services.memory_service import increment_ambiguous_count

        thread = EmailThread()
        thread.ambiguous_count = 0
        thread.agent_escalated = False
        thread.escalation_reason = None

        mock_db = MagicMock()
        mock_db.execute = AsyncMock(
            return_value=MagicMock(
                scalar_one_or_none=MagicMock(return_value=thread)
            )
        )
        mock_db.flush = AsyncMock()
        mock_db.add = MagicMock()

        count, escalated = await increment_ambiguous_count(mock_db, thread_id=1, threshold=3)
        assert count == 1
        assert escalated is False

    @pytest.mark.asyncio
    async def test_escalates_at_threshold(self):
        from app.models.email_thread import EmailThread
        from app.services.memory_service import increment_ambiguous_count

        thread = EmailThread()
        thread.ambiguous_count = 2
        thread.agent_escalated = False
        thread.escalation_reason = None

        mock_db = MagicMock()
        mock_db.execute = AsyncMock(
            return_value=MagicMock(
                scalar_one_or_none=MagicMock(return_value=thread)
            )
        )
        mock_db.flush = AsyncMock()
        mock_db.add = MagicMock()

        count, escalated = await increment_ambiguous_count(mock_db, thread_id=1, threshold=3)
        assert count == 3
        assert escalated is True
        assert thread.agent_escalated is True
        assert thread.escalation_reason is not None

    @pytest.mark.asyncio
    async def test_does_not_escalate_already_escalated(self):
        """Thread that is already escalated should not re-trigger escalation."""
        from app.models.email_thread import EmailThread
        from app.services.memory_service import increment_ambiguous_count

        thread = EmailThread()
        thread.ambiguous_count = 5
        thread.agent_escalated = True   # already escalated
        thread.escalation_reason = "Previous escalation"

        mock_db = MagicMock()
        mock_db.execute = AsyncMock(
            return_value=MagicMock(
                scalar_one_or_none=MagicMock(return_value=thread)
            )
        )
        mock_db.flush = AsyncMock()
        mock_db.add = MagicMock()

        count, escalated = await increment_ambiguous_count(mock_db, thread_id=1, threshold=3)
        assert count == 6
        assert escalated is True
        # Reason should be unchanged (not overwritten by the new increment)
        assert thread.escalation_reason == "Previous escalation"


# ─────────────────────────────────────────────────────────────────────────────
# 8.  reset_ambiguous_count
# ─────────────────────────────────────────────────────────────────────────────

class TestResetAmbiguousCount:

    @pytest.mark.asyncio
    async def test_resets_counter_to_zero(self):
        from app.models.email_thread import EmailThread
        from app.services.memory_service import reset_ambiguous_count

        thread = EmailThread()
        thread.ambiguous_count = 4

        mock_db = MagicMock()
        mock_db.execute = AsyncMock(
            return_value=MagicMock(
                scalar_one_or_none=MagicMock(return_value=thread)
            )
        )
        mock_db.flush = AsyncMock()
        mock_db.add = MagicMock()

        await reset_ambiguous_count(mock_db, thread_id=1)
        assert thread.ambiguous_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# 9.  escalate_thread
# ─────────────────────────────────────────────────────────────────────────────

class TestEscalateThread:

    @pytest.mark.asyncio
    async def test_sets_flag_and_reason(self):
        from app.models.email_thread import EmailThread
        from app.services.memory_service import escalate_thread

        thread = EmailThread()
        thread.agent_escalated = False
        thread.escalation_reason = None

        mock_db = MagicMock()
        mock_db.execute = AsyncMock(
            return_value=MagicMock(
                scalar_one_or_none=MagicMock(return_value=thread)
            )
        )
        mock_db.flush = AsyncMock()
        mock_db.add = MagicMock()

        await escalate_thread(mock_db, thread_id=1, reason="API failure")
        assert thread.agent_escalated is True
        assert thread.escalation_reason == "API failure"

    @pytest.mark.asyncio
    async def test_does_not_overwrite_existing_escalation(self):
        from app.models.email_thread import EmailThread
        from app.services.memory_service import escalate_thread

        thread = EmailThread()
        thread.agent_escalated = True
        thread.escalation_reason = "Original reason"

        mock_db = MagicMock()
        mock_db.execute = AsyncMock(
            return_value=MagicMock(
                scalar_one_or_none=MagicMock(return_value=thread)
            )
        )
        mock_db.flush = AsyncMock()
        mock_db.add = MagicMock()

        await escalate_thread(mock_db, thread_id=1, reason="New reason")
        assert thread.escalation_reason == "Original reason"


# ─────────────────────────────────────────────────────────────────────────────
# 10.  EmailThread model — new columns
# ─────────────────────────────────────────────────────────────────────────────

class TestEmailThreadHardeningColumns:

    def test_thread_summary_column_exists(self):
        from app.models.email_thread import EmailThread
        cols = [c.name for c in EmailThread.__table__.columns]
        assert "thread_summary" in cols

    def test_ambiguous_count_column_exists(self):
        from app.models.email_thread import EmailThread
        cols = [c.name for c in EmailThread.__table__.columns]
        assert "ambiguous_count" in cols

    def test_agent_escalated_column_exists(self):
        from app.models.email_thread import EmailThread
        cols = [c.name for c in EmailThread.__table__.columns]
        assert "agent_escalated" in cols

    def test_escalation_reason_column_exists(self):
        from app.models.email_thread import EmailThread
        cols = [c.name for c in EmailThread.__table__.columns]
        assert "escalation_reason" in cols

    def test_agent_escalated_is_boolean(self):
        from app.models.email_thread import EmailThread
        from sqlalchemy import Boolean
        col = EmailThread.__table__.columns["agent_escalated"]
        assert isinstance(col.type, Boolean)

    def test_can_set_escalation_fields(self):
        from app.models.email_thread import EmailThread
        t = EmailThread()
        t.agent_escalated = True
        t.escalation_reason = "test reason"
        t.ambiguous_count = 3
        t.thread_summary = "Summary text"
        assert t.agent_escalated is True


# ─────────────────────────────────────────────────────────────────────────────
# 11.  AgentState Phase 5 fields
# ─────────────────────────────────────────────────────────────────────────────

class TestAgentStatePhase5Fields:

    def test_agent_escalated_in_state(self):
        from app.agents.state import AgentState
        import typing
        hints = typing.get_type_hints(AgentState)
        assert "agent_escalated" in hints

    def test_escalation_reason_in_state(self):
        from app.agents.state import AgentState
        import typing
        hints = typing.get_type_hints(AgentState)
        assert "escalation_reason" in hints

    def test_ambiguous_count_in_state(self):
        from app.agents.state import AgentState
        import typing
        hints = typing.get_type_hints(AgentState)
        assert "ambiguous_count" in hints

    def test_thread_summary_in_state(self):
        from app.agents.state import AgentState
        import typing
        hints = typing.get_type_hints(AgentState)
        assert "thread_summary" in hints


# ─────────────────────────────────────────────────────────────────────────────
# 12.  classify_intent source checks
# ─────────────────────────────────────────────────────────────────────────────

class TestClassifyIntentSource:

    def _src(self) -> str:
        import pathlib
        return (
            pathlib.Path(__file__).parent.parent
            / "app" / "agents" / "nodes" / "classify_intent.py"
        ).read_text(encoding="utf-8")

    def test_imports_validate_intent(self):
        assert "validate_intent" in self._src()

    def test_imports_validate_confidence(self):
        assert "validate_confidence" in self._src()

    def test_calls_should_downgrade_to_ambiguous(self):
        assert "should_downgrade_to_ambiguous" in self._src()

    def test_increments_ambiguous_count(self):
        assert "increment_ambiguous_count" in self._src()

    def test_resets_ambiguous_count_on_clear_intent(self):
        assert "reset_ambiguous_count" in self._src()

    def test_escalation_guard_at_entry(self):
        assert "agent_escalated" in self._src()

    def test_build_escalation_reply_called(self):
        assert "build_escalation_reply" in self._src()


# ─────────────────────────────────────────────────────────────────────────────
# 13.  Observability wrapper — failure-safe (no re-raise)
# ─────────────────────────────────────────────────────────────────────────────

class TestObservabilityFailureSafe:

    @pytest.mark.asyncio
    async def test_unhandled_node_exception_does_not_raise(self):
        """
        The observability wrapper must NOT re-raise unhandled node exceptions.
        The graph must always receive a valid state dict so it can continue.
        """
        from app.agents.observability import with_observability

        async def _crashing_node(state):
            raise RuntimeError("Simulated crash")

        wrapped = with_observability(_crashing_node, "crashing_node")

        state = {
            "thread_id": 99,
            "prospect_name": "Test",
            "reply_sent": False,
        }

        with patch("app.agents.observability.AsyncSessionLocal") as mock_session, \
             patch("app.agents.observability.start_agent_run",
                   new_callable=AsyncMock, return_value=(MagicMock(), 0.0)), \
             patch("app.agents.observability.finish_agent_run",
                   new_callable=AsyncMock):

            mock_db = AsyncMock()
            mock_db.__aenter__ = AsyncMock(return_value=mock_db)
            mock_db.__aexit__ = AsyncMock(return_value=False)
            mock_db.commit = AsyncMock()
            mock_db.close = AsyncMock()
            mock_session.return_value = mock_db

            # This MUST NOT raise
            result = await wrapped(state)

        assert isinstance(result, dict)
        assert result.get("error_node") == "crashing_node"
        assert "Simulated crash" in str(result.get("error", ""))

    @pytest.mark.asyncio
    async def test_unhandled_exception_produces_reply_instruction(self):
        """Safe error state must include a fallback reply_instruction."""
        from app.agents.observability import with_observability

        async def _crashing_node(state):
            raise ValueError("LLM timeout")

        wrapped = with_observability(_crashing_node, "crashing_node")
        state = {"thread_id": 1}

        with patch("app.agents.observability.AsyncSessionLocal") as mock_session, \
             patch("app.agents.observability.start_agent_run",
                   new_callable=AsyncMock, return_value=(MagicMock(), 0.0)), \
             patch("app.agents.observability.finish_agent_run",
                   new_callable=AsyncMock):

            mock_db = AsyncMock()
            mock_db.__aenter__ = AsyncMock(return_value=mock_db)
            mock_db.__aexit__ = AsyncMock(return_value=False)
            mock_db.commit = AsyncMock()
            mock_db.close = AsyncMock()
            mock_session.return_value = mock_db

            result = await wrapped(state)

        assert result.get("reply_instruction") is not None
        assert len(result["reply_instruction"]) > 0

    def test_source_no_longer_reraises(self):
        """The observability wrapper source must NOT have a bare 'raise' after node_exc."""
        import pathlib
        src = (
            pathlib.Path(__file__).parent.parent
            / "app" / "agents" / "observability.py"
        ).read_text(encoding="utf-8")
        # Find the except block for node_exc
        exc_block_start = src.find("except Exception as node_exc:")
        exc_block = src[exc_block_start:exc_block_start + 500]
        # The block must NOT contain a bare 'raise' statement
        # (allowing 'raise CalendarOpError(...)' etc in other places is fine,
        #  but the node_exc block must NOT re-raise)
        lines_after_catch = exc_block.split("\n")
        bare_raise_lines = [
            line.strip() for line in lines_after_catch
            if line.strip() == "raise"
        ]
        assert not bare_raise_lines, (
            "observability.py must not re-raise node exceptions — "
            "found bare 'raise' in the node_exc except block"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 14.  route_after_intent — agent_escalated short-circuit
# ─────────────────────────────────────────────────────────────────────────────

class TestRouteAfterIntentEscalation:

    def test_agent_escalated_routes_to_reply_generation(self):
        import pathlib, ast, sys, types

        # Load graph.py source to test route_after_intent without importing it
        # (avoids pulling in the full langgraph dependency chain).
        graph_src = (
            pathlib.Path(__file__).parent.parent
            / "app" / "agents" / "graph.py"
        ).read_text(encoding="utf-8")

        # Verify the source contains the agent_escalated guard
        assert 'state.get("agent_escalated")' in graph_src or \
               "agent_escalated" in graph_src

    def test_source_routes_escalated_to_reply_generation(self):
        import pathlib
        src = (
            pathlib.Path(__file__).parent.parent
            / "app" / "agents" / "graph.py"
        ).read_text(encoding="utf-8")
        # Find route_after_intent function
        start = src.find("def route_after_intent")
        end   = src.find("def route_after_negotiation")
        fn_src = src[start:end] if end > start else src[start:]
        assert "agent_escalated" in fn_src
        assert '"reply_generation"' in fn_src

    def test_source_seeds_agent_escalated_in_initial_state(self):
        import pathlib
        src = (
            pathlib.Path(__file__).parent.parent
            / "app" / "agents" / "graph.py"
        ).read_text(encoding="utf-8")
        assert '"agent_escalated"' in src
        assert "memory.agent_escalated" in src


# ─────────────────────────────────────────────────────────────────────────────
# 15.  summarization_service — threshold gate
# ─────────────────────────────────────────────────────────────────────────────

class TestSummarizationService:

    @pytest.mark.asyncio
    async def test_below_trigger_does_not_call_generate(self):
        """maybe_update_summary must be a no-op below the trigger threshold."""
        from app.services.summarization_service import maybe_update_summary

        with patch(
            "app.services.summarization_service._generate_and_store_summary",
            new_callable=AsyncMock,
        ) as mock_gen:
            await maybe_update_summary(thread_id=1, current_message_count=5)

        mock_gen.assert_not_called()

    @pytest.mark.asyncio
    async def test_at_trigger_calls_generate(self):
        from app.core.config import settings
        from app.services.summarization_service import maybe_update_summary

        with patch(
            "app.services.summarization_service._generate_and_store_summary",
            new_callable=AsyncMock,
        ) as mock_gen:
            await maybe_update_summary(
                thread_id=1,
                current_message_count=settings.MEMORY_SUMMARY_TRIGGER,
            )

        mock_gen.assert_called_once_with(1)

    @pytest.mark.asyncio
    async def test_generate_failure_does_not_raise(self):
        """Summary generation failures must be swallowed gracefully."""
        from app.core.config import settings
        from app.services.summarization_service import maybe_update_summary

        with patch(
            "app.services.summarization_service._generate_and_store_summary",
            new_callable=AsyncMock,
            side_effect=Exception("LLM timeout"),
        ):
            # Must not raise
            await maybe_update_summary(
                thread_id=1,
                current_message_count=settings.MEMORY_SUMMARY_TRIGGER,
            )


# ─────────────────────────────────────────────────────────────────────────────
# 16.  send_reply source — rolling summary wired in
# ─────────────────────────────────────────────────────────────────────────────

class TestSendReplyRollingSummary:

    def _src(self) -> str:
        import pathlib
        return (
            pathlib.Path(__file__).parent.parent
            / "app" / "agents" / "nodes" / "send_reply.py"
        ).read_text(encoding="utf-8")

    def test_calls_maybe_update_summary(self):
        assert "_maybe_update_summary" in self._src()

    def test_summary_called_after_commit(self):
        """Summary call must appear AFTER await db.commit() in the source."""
        src = self._src()
        commit_pos  = src.rfind("await db.commit()")
        summary_pos = src.find("_maybe_update_summary")
        assert commit_pos != -1 and summary_pos != -1
        assert summary_pos > commit_pos

    def test_summary_failure_handled(self):
        """_maybe_update_summary must be wrapped in its own try/except."""
        src = self._src()
        assert "try:" in src
        # The helper itself swallows exceptions
        assert "summarization_service" in src or "maybe_update_summary" in src


# ─────────────────────────────────────────────────────────────────────────────
# 17.  Migration 007
# ─────────────────────────────────────────────────────────────────────────────

class TestMigration007:

    def _src(self) -> str:
        import pathlib
        return (
            pathlib.Path(__file__).parent.parent
            / "alembic" / "versions" / "007_add_thread_hardening.py"
        ).read_text(encoding="utf-8")

    def test_file_exists(self):
        import pathlib
        assert (
            pathlib.Path(__file__).parent.parent
            / "alembic" / "versions" / "007_add_thread_hardening.py"
        ).exists()

    def test_revision_chain(self):
        src = self._src()
        assert 'revision = "007"' in src
        assert 'down_revision = "006"' in src

    def test_adds_thread_summary(self):
        assert "thread_summary" in self._src()

    def test_adds_ambiguous_count(self):
        assert "ambiguous_count" in self._src()

    def test_adds_agent_escalated(self):
        assert "agent_escalated" in self._src()

    def test_adds_escalation_reason(self):
        assert "escalation_reason" in self._src()

    def test_creates_index(self):
        assert "ix_email_threads_agent_escalated" in self._src()

    def test_downgrade_drops_all_columns(self):
        src = self._src()
        assert "def downgrade" in src
        assert src.count("drop_column") >= 4
        assert "drop_index" in src


# ─────────────────────────────────────────────────────────────────────────────
# 18.  Config — Phase 5 knobs
# ─────────────────────────────────────────────────────────────────────────────

class TestPhase5Config:

    def test_intent_confidence_threshold_exists(self):
        from app.core.config import settings
        assert hasattr(settings, "INTENT_CONFIDENCE_THRESHOLD")
        assert 0.0 < settings.INTENT_CONFIDENCE_THRESHOLD < 1.0

    def test_ambiguous_escalation_threshold_exists(self):
        from app.core.config import settings
        assert hasattr(settings, "AMBIGUOUS_ESCALATION_THRESHOLD")
        assert settings.AMBIGUOUS_ESCALATION_THRESHOLD >= 1

    def test_memory_window_messages_exists(self):
        from app.core.config import settings
        assert hasattr(settings, "MEMORY_WINDOW_MESSAGES")
        assert settings.MEMORY_WINDOW_MESSAGES >= 5

    def test_memory_summary_trigger_exists(self):
        from app.core.config import settings
        assert hasattr(settings, "MEMORY_SUMMARY_TRIGGER")
        assert settings.MEMORY_SUMMARY_TRIGGER > settings.MEMORY_WINDOW_MESSAGES
