"""
Phase 2 — Agent Observability Tests
─────────────────────────────────────
Tests for:
  1. with_observability() wrapper — happy path, error path, DB-fail resilience
  2. _input_snapshot / _output_snapshot helpers
  3. send_reply node NameError bug fix (new_thread_status)
  4. /logs API endpoint (list + get)
  5. error_message field propagation
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ─────────────────────────────────────────────────────────────────────────────
# 1.  with_observability() wrapper unit tests
# ─────────────────────────────────────────────────────────────────────────────

class TestWithObservability:
    """Verify the observability wrapper records AgentRun rows correctly."""

    def _make_state(self, **extra):
        state = {
            "thread_id": 42,
            "thread_status": "waiting",
            "intent": "curious",
            "messages": [{"id": 1}],
        }
        state.update(extra)
        return state

    def _make_fake_run(self):
        run = MagicMock()
        run.id = 99
        run.node_name = "classify_intent"
        run.latency_ms = 150
        run.error_message = None
        return run

    def _patch_obs(self):
        """Return a context manager that patches all observability DB helpers."""
        import app.agents.observability as obs_mod
        return (
            patch.object(obs_mod, "AsyncSessionLocal"),
            patch.object(obs_mod, "start_agent_run", new_callable=AsyncMock),
            patch.object(obs_mod, "finish_agent_run", new_callable=AsyncMock),
        )

    @pytest.mark.asyncio
    async def test_success_path_calls_start_and_finish(self):
        """On a successful node execution, start_agent_run + finish_agent_run
        are both called and the result state is returned unchanged."""
        import time
        from app.agents.observability import with_observability

        async def _node(state):
            return {**state, "intent": "interested", "intent_confidence": 0.9}

        fake_run = self._make_fake_run()
        p_session, p_start, p_finish = self._patch_obs()

        with p_session as MockSession, p_start as mock_start, p_finish as mock_finish:
            mock_start.return_value = (fake_run, time.perf_counter())
            MockSession.return_value = AsyncMock()

            wrapped = with_observability(_node, "classify_intent")
            result = await wrapped(self._make_state())

        assert result["intent"] == "interested"
        mock_start.assert_called_once()
        mock_finish.assert_called_once()

        call_args = mock_finish.call_args
        status_arg = call_args.args[3] if len(call_args.args) >= 4 else call_args.kwargs.get("status")
        assert status_arg == "success"

    @pytest.mark.asyncio
    async def test_node_error_state_recorded_as_error(self):
        """When the node sets error_node == node_name in state, status is ERROR."""
        import time
        from app.agents.observability import with_observability

        async def _node(state):
            return {**state, "error": "LLM timeout", "error_node": "classify_intent"}

        fake_run = self._make_fake_run()
        p_session, p_start, p_finish = self._patch_obs()

        with p_session as MockSession, p_start as mock_start, p_finish as mock_finish:
            mock_start.return_value = (fake_run, time.perf_counter())
            MockSession.return_value = AsyncMock()

            wrapped = with_observability(_node, "classify_intent")
            result = await wrapped(self._make_state())

        assert result["error"] == "LLM timeout"

        call_args = mock_finish.call_args
        status_arg = call_args.args[3] if len(call_args.args) >= 4 else call_args.kwargs.get("status")
        assert status_arg == "error"

    @pytest.mark.asyncio
    async def test_observability_db_failure_does_not_crash_node(self):
        """If start_agent_run raises, the node still runs and returns its result."""
        import app.agents.observability as obs_mod
        from app.agents.observability import with_observability

        async def _node(state):
            return {**state, "intent": "negotiating"}

        with (
            patch.object(obs_mod, "AsyncSessionLocal"),
            patch.object(obs_mod, "start_agent_run", side_effect=RuntimeError("DB down")),
        ):
            wrapped = with_observability(_node, "classify_intent")
            result = await wrapped(self._make_state())

        assert result["intent"] == "negotiating"

    @pytest.mark.asyncio
    async def test_node_exception_converted_to_safe_error_state(self):
        """Phase 5 — failure-safe execution: unhandled node exceptions must NOT
        be re-raised.  Instead, the wrapper returns a safe error state dict so
        the LangGraph graph can continue to reply_generation and send a
        graceful fallback reply.
        """
        import time
        from app.agents.observability import with_observability

        async def _node(state):
            raise ValueError("unexpected crash")

        fake_run = self._make_fake_run()
        p_session, p_start, p_finish = self._patch_obs()

        with p_session as MockSession, p_start as mock_start, p_finish as mock_finish:
            mock_start.return_value = (fake_run, time.perf_counter())
            MockSession.return_value = AsyncMock()

            wrapped = with_observability(_node, "classify_intent")
            # Must NOT raise — must return a safe state dict
            result = await wrapped(self._make_state())

        assert isinstance(result, dict)
        assert result.get("error_node") == "classify_intent"
        assert "unexpected crash" in str(result.get("error", ""))
        assert result.get("reply_instruction") is not None

        # finish was still called with ERROR status (audit trail preserved)
        call_args = mock_finish.call_args
        if call_args:
            status_arg = call_args.args[3] if len(call_args.args) >= 4 else call_args.kwargs.get("status")
            assert status_arg == "error"

    @pytest.mark.asyncio
    async def test_finish_db_failure_does_not_crash_node(self):
        """If finish_agent_run raises, the node's result still propagates."""
        import time
        import app.agents.observability as obs_mod
        from app.agents.observability import with_observability

        async def _node(state):
            return {**state, "intent": "interested"}

        fake_run = self._make_fake_run()

        with (
            patch.object(obs_mod, "AsyncSessionLocal"),
            patch.object(obs_mod, "start_agent_run", new_callable=AsyncMock) as mock_start,
            patch.object(obs_mod, "finish_agent_run", side_effect=RuntimeError("DB gone")),
        ):
            mock_start.return_value = (fake_run, time.perf_counter())

            wrapped = with_observability(_node, "classify_intent")
            result = await wrapped(self._make_state())

        assert result["intent"] == "interested"

    def test_wrapper_preserves_function_name(self):
        """The wrapped function keeps the original function's __name__."""
        from app.agents.observability import with_observability

        async def classify_intent(state):  # noqa: F811
            return state

        wrapped = with_observability(classify_intent, "classify_intent")
        assert wrapped.__name__ == "classify_intent"


# ─────────────────────────────────────────────────────────────────────────────
# 2.  Snapshot helpers unit tests
# ─────────────────────────────────────────────────────────────────────────────

class TestSnapshotHelpers:

    def _base_state(self):
        return {
            "thread_id": 7,
            "thread_status": "waiting",
            "intent": "curious",
            "messages": [{"id": 1}, {"id": 2}],
            "conversation_text": "This is a very long text " * 100,
            "available_slots": [{"start": "2026-06-01T09:00"}, {"start": "2026-06-01T10:00"}],
        }

    def test_input_snapshot_excludes_large_fields(self):
        from app.agents.observability import _input_snapshot
        snap = _input_snapshot(self._base_state(), "classify_intent")
        assert "conversation_text" not in snap
        assert "messages" not in snap
        assert "available_slots" not in snap
        assert snap["thread_id"] == 7
        assert snap["message_count"] == 2

    def test_input_snapshot_negotiation(self):
        from app.agents.observability import _input_snapshot
        state = {**self._base_state(), "max_budget": 5000.0, "current_offer": 4000.0, "counter_round": 1}
        snap = _input_snapshot(state, "negotiation")
        assert snap["max_budget"] == 5000.0
        assert snap["current_offer"] == 4000.0
        assert snap["counter_round"] == 1

    def test_output_snapshot_truncates_long_strings(self):
        from app.agents.observability import _output_snapshot
        long_reasoning = "X" * 600
        before = self._base_state()
        after = {**before, "intent": "interested", "intent_reasoning": long_reasoning}
        snap = _output_snapshot(before, after, "classify_intent")
        assert snap["intent"] == "interested"
        assert len(snap["intent_reasoning"]) <= 504  # 500 + "…"
        assert snap["intent_reasoning"].endswith("…")

    def test_output_snapshot_reply_generation_includes_preview(self):
        from app.agents.observability import _output_snapshot
        body = "A" * 300
        before = self._base_state()
        after = {**before, "reply_subject": "Re: Follow up", "reply_body": body}
        snap = _output_snapshot(before, after, "reply_generation")
        assert "reply_body_preview" in snap
        assert len(snap["reply_body_preview"]) <= 204  # 200 + "…"

    def test_output_snapshot_send_reply(self):
        from app.agents.observability import _output_snapshot
        before = {**self._base_state(), "reply_body": "Short body"}
        after = {**before, "reply_sent": True}
        snap = _output_snapshot(before, after, "send_reply")
        assert snap["reply_sent"] is True
        assert snap["reply_body_preview"] == "Short body"


# ─────────────────────────────────────────────────────────────────────────────
# 3.  send_reply NameError fix — new_thread_status is referenced correctly
# ─────────────────────────────────────────────────────────────────────────────

class TestSendReplyBugFix:
    """Verify the send_reply node no longer references the undefined 'new_status'."""

    def _read_send_reply_source(self) -> str:
        """Read send_reply.py source directly to avoid triggering node __init__.py
        which would pull in langchain_core / google-auth packages not present in
        the unit-test venv."""
        import pathlib
        src_path = pathlib.Path(__file__).parent.parent / "app" / "agents" / "nodes" / "send_reply.py"
        return src_path.read_text(encoding="utf-8")

    def test_no_name_error_in_send_reply_source(self):
        """The source code must not contain the raw undefined 'new_status' string."""
        source = self._read_send_reply_source()
        # 'new_thread_status' should appear and the buggy bare 'new_status' should NOT
        # appear outside of the log message that now correctly uses new_thread_status.
        assert "new_thread_status" in source
        # The old bare variable must not appear as a standalone name
        lines_with_new_status = [
            ln for ln in source.splitlines()
            if "new_status" in ln and "new_thread_status" not in ln
        ]
        assert lines_with_new_status == [], (
            f"Found legacy 'new_status' references (without 'new_thread_status'): "
            f"{lines_with_new_status}"
        )

    def test_resolve_thread_status_logic_in_source(self):
        """The source must contain the CLOSED and WAITING thread status logic."""
        source = self._read_send_reply_source()
        assert "ThreadStatus.CLOSED.value" in source
        assert "ThreadStatus.WAITING.value" in source
        assert "_resolve_thread_status" in source


# ─────────────────────────────────────────────────────────────────────────────
# 4.  /logs API endpoint tests
# ─────────────────────────────────────────────────────────────────────────────

class TestLogsApi:
    """Integration tests for GET /api/v1/logs/ and GET /api/v1/logs/{id}."""

    @pytest.mark.asyncio
    async def test_list_logs_returns_paginated_response(self, admin_client):
        """GET /logs/ returns 200 with items / total / page / page_size."""
        from unittest.mock import patch, AsyncMock
        from app.models.agent_run import AgentRun, RunStatus
        from datetime import datetime, timezone

        run = AgentRun()
        run.id = 1
        run.thread_id = 10
        run.node_name = "classify_intent"
        run.status = RunStatus.SUCCESS.value
        run.latency_ms = 120
        run.error_message = None
        run.input_payload = {"thread_id": 10}
        run.output_payload = {"intent": "interested"}
        run.created_at = datetime.now(timezone.utc)

        with patch(
            "app.repositories.agent_run_repo.list_runs",
            new_callable=AsyncMock,
            return_value=([run], 1),
        ):
            resp = await admin_client.get("/api/v1/logs/")

        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["page"] == 1
        assert len(body["items"]) == 1
        item = body["items"][0]
        assert item["node_name"] == "classify_intent"
        assert item["status"] == "success"
        assert item["latency_ms"] == 120

    @pytest.mark.asyncio
    async def test_list_logs_supports_node_filter(self, admin_client):
        """GET /logs/?node_name=negotiation passes the filter to the repo."""
        from unittest.mock import patch, AsyncMock

        with patch(
            "app.repositories.agent_run_repo.list_runs",
            new_callable=AsyncMock,
            return_value=([], 0),
        ) as mock_list:
            resp = await admin_client.get("/api/v1/logs/?node_name=negotiation")

        assert resp.status_code == 200
        call_kwargs = mock_list.call_args.kwargs
        assert call_kwargs.get("node_name") == "negotiation"

    @pytest.mark.asyncio
    async def test_list_logs_supports_status_filter(self, admin_client):
        """GET /logs/?status=error passes the status filter to the repo."""
        from unittest.mock import patch, AsyncMock

        with patch(
            "app.repositories.agent_run_repo.list_runs",
            new_callable=AsyncMock,
            return_value=([], 0),
        ) as mock_list:
            resp = await admin_client.get("/api/v1/logs/?status=error")

        assert resp.status_code == 200
        call_kwargs = mock_list.call_args.kwargs
        assert call_kwargs.get("status") == "error"

    @pytest.mark.asyncio
    async def test_get_log_by_id_found(self, admin_client):
        """GET /logs/{id} returns 200 with the run detail."""
        from unittest.mock import patch, AsyncMock
        from app.models.agent_run import AgentRun, RunStatus
        from datetime import datetime, timezone

        run = AgentRun()
        run.id = 5
        run.thread_id = 3
        run.node_name = "send_reply"
        run.status = RunStatus.ERROR.value
        run.latency_ms = 800
        run.error_message = "Gmail API 429"
        run.input_payload = {"thread_id": 3}
        run.output_payload = {"reply_sent": False}
        run.created_at = datetime.now(timezone.utc)

        with patch(
            "app.repositories.agent_run_repo.get_by_id",
            new_callable=AsyncMock,
            return_value=run,
        ):
            resp = await admin_client.get("/api/v1/logs/5")

        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == 5
        assert body["status"] == "error"
        assert body["error_message"] == "Gmail API 429"

    @pytest.mark.asyncio
    async def test_get_log_by_id_not_found(self, admin_client):
        """GET /logs/{id} returns 404 for an unknown ID."""
        from unittest.mock import patch, AsyncMock

        with patch(
            "app.repositories.agent_run_repo.get_by_id",
            new_callable=AsyncMock,
            return_value=None,
        ):
            resp = await admin_client.get("/api/v1/logs/9999")

        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_logs_endpoint_requires_auth(self, unauth_client):
        """GET /logs/ returns 401 without a valid token."""
        resp = await unauth_client.get("/api/v1/logs/")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_logs_accessible_to_operator(self, operator_client):
        """GET /logs/ is accessible to operator users (read-only)."""
        from unittest.mock import patch, AsyncMock

        with patch(
            "app.repositories.agent_run_repo.list_runs",
            new_callable=AsyncMock,
            return_value=([], 0),
        ):
            resp = await operator_client.get("/api/v1/logs/")

        assert resp.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# 5.  AgentRun model + schema include error_message
# ─────────────────────────────────────────────────────────────────────────────

class TestAgentRunSchema:

    def test_agent_run_model_has_error_message(self):
        from app.models.agent_run import AgentRun
        run = AgentRun()
        run.error_message = "test error"
        assert run.error_message == "test error"

    def test_agent_run_read_schema_includes_error_message(self):
        from app.schemas.agent_run import AgentRunRead
        from datetime import datetime, timezone

        r = AgentRunRead(
            id=1,
            thread_id=1,
            node_name="classify_intent",
            status="error",
            latency_ms=None,
            error_message="boom",
            input_payload=None,
            output_payload=None,
            created_at=datetime.now(timezone.utc),
        )
        assert r.error_message == "boom"

    def test_agent_run_read_schema_error_message_defaults_none(self):
        from app.schemas.agent_run import AgentRunRead
        from datetime import datetime, timezone

        r = AgentRunRead(
            id=2,
            thread_id=1,
            node_name="send_reply",
            status="success",
            latency_ms=100,
            input_payload=None,
            output_payload=None,
            created_at=datetime.now(timezone.utc),
        )
        assert r.error_message is None


# ─────────────────────────────────────────────────────────────────────────────
# 6.  graph.py wires observability for all 6 nodes
# ─────────────────────────────────────────────────────────────────────────────

class TestGraphObservabilityWiring:

    def test_all_nodes_are_in_graph_source(self):
        """Verify graph.py wires all 6 nodes through with_observability() by
        reading the source file — avoids importing langgraph/langchain which
        are production deps not installed in the unit-test venv."""
        import pathlib
        src = (
            pathlib.Path(__file__).parent.parent
            / "app" / "agents" / "graph.py"
        ).read_text(encoding="utf-8")

        assert "with_observability" in src, "graph.py must import with_observability"
        expected_nodes = [
            "classify_intent",
            "negotiation",
            "scheduling",
            "rescheduling",
            "reply_generation",
            "send_reply",
        ]
        for node in expected_nodes:
            assert f'with_observability({node}' in src or f"with_observability({node}" in src, (
                f"Node '{node}' is not wrapped with with_observability() in graph.py"
            )

    def test_observability_import_in_graph_source(self):
        """graph.py must import from app.agents.observability."""
        import pathlib
        src = (
            pathlib.Path(__file__).parent.parent
            / "app" / "agents" / "graph.py"
        ).read_text(encoding="utf-8")
        assert "from app.agents.observability import with_observability" in src
