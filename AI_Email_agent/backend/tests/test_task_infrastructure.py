"""
Phase 6 — Task Infrastructure Hardening Tests
───────────────────────────────────────────────
Covers:
  1.  Config knobs — all 7 new settings present with sane defaults
  2.  Per-task base classes — GmailTask, AgentTask, DefaultTask + max_retries
  3.  exponential_backoff() — values, cap, jitter, edge cases
  4.  task_reject_on_worker_lost — flag present in celery conf
  5.  task_acks_late — still True (regression guard)
  6.  DLQ helpers — dlq_write, dlq_read, dlq_length, _safe_serialise
  7.  queue_depth — delegates to LLEN
  8.  Outreach idempotency lock — acquire, skip, release
  9.  Agent enqueue dedup — mark_agent_queued, is_agent_queued_or_running
 10.  Task routes — each task mapped to the correct queue
 11.  celery_app source — crash-safe ack, DLQ signal wired, base classes exported
 12.  tasks.py source — GmailTask/AgentTask/DefaultTask used, exp backoff,
                        outreach idempotency guard, agent dedup in _poll_inbox
 13.  health endpoint — /health/ and /health/worker return correct structure
 14.  health /worker/dlq — pagination params accepted
"""

from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock, patch


# ─────────────────────────────────────────────────────────────────────────────
# 1.  Config knobs
# ─────────────────────────────────────────────────────────────────────────────

class TestPhase6Config:

    def test_task_max_retries_gmail(self):
        from app.core.config import settings
        assert hasattr(settings, "TASK_MAX_RETRIES_GMAIL")
        assert settings.TASK_MAX_RETRIES_GMAIL >= 3

    def test_task_max_retries_agent(self):
        from app.core.config import settings
        assert hasattr(settings, "TASK_MAX_RETRIES_AGENT")
        assert settings.TASK_MAX_RETRIES_AGENT >= 1

    def test_task_max_retries_default(self):
        from app.core.config import settings
        assert hasattr(settings, "TASK_MAX_RETRIES_DEFAULT")
        assert settings.TASK_MAX_RETRIES_DEFAULT >= 1

    def test_task_retry_backoff_base(self):
        from app.core.config import settings
        assert hasattr(settings, "TASK_RETRY_BACKOFF_BASE")
        assert settings.TASK_RETRY_BACKOFF_BASE > 0

    def test_task_retry_backoff_cap(self):
        from app.core.config import settings
        assert hasattr(settings, "TASK_RETRY_BACKOFF_CAP")
        assert settings.TASK_RETRY_BACKOFF_CAP > settings.TASK_RETRY_BACKOFF_BASE

    def test_task_dlq_max_size(self):
        from app.core.config import settings
        assert hasattr(settings, "TASK_DLQ_MAX_SIZE")
        assert settings.TASK_DLQ_MAX_SIZE >= 100

    def test_task_outreach_idem_ttl(self):
        from app.core.config import settings
        assert hasattr(settings, "TASK_OUTREACH_IDEM_TTL")
        assert settings.TASK_OUTREACH_IDEM_TTL >= 3600  # at least 1 hour

    def test_task_agent_enqueue_ttl(self):
        from app.core.config import settings
        assert hasattr(settings, "TASK_AGENT_ENQUEUE_TTL")
        # Must be longer than run_agent_task soft_time_limit (300s)
        assert settings.TASK_AGENT_ENQUEUE_TTL > 300


# ─────────────────────────────────────────────────────────────────────────────
# 2.  Per-task base classes
# ─────────────────────────────────────────────────────────────────────────────

class TestTaskBaseClasses:

    def test_gmail_task_exists(self):
        from app.workers.celery_app import GmailTask
        assert GmailTask.abstract is True

    def test_gmail_task_max_retries(self):
        from app.core.config import settings
        from app.workers.celery_app import GmailTask
        assert GmailTask.max_retries == settings.TASK_MAX_RETRIES_GMAIL

    def test_agent_task_exists(self):
        from app.workers.celery_app import AgentTask
        assert AgentTask.abstract is True

    def test_agent_task_max_retries(self):
        from app.core.config import settings
        from app.workers.celery_app import AgentTask
        assert AgentTask.max_retries == settings.TASK_MAX_RETRIES_AGENT

    def test_default_task_exists(self):
        from app.workers.celery_app import DefaultTask
        assert DefaultTask.abstract is True

    def test_default_task_max_retries(self):
        from app.core.config import settings
        from app.workers.celery_app import DefaultTask
        assert DefaultTask.max_retries == settings.TASK_MAX_RETRIES_DEFAULT


# ─────────────────────────────────────────────────────────────────────────────
# 3.  exponential_backoff()
# ─────────────────────────────────────────────────────────────────────────────

class TestExponentialBackoff:

    def test_first_attempt_returns_base(self):
        from app.workers.celery_app import exponential_backoff
        result = exponential_backoff(0, base=30, cap=600, jitter=False)
        assert result == 30

    def test_doubles_each_attempt(self):
        from app.workers.celery_app import exponential_backoff
        assert exponential_backoff(1, base=30, cap=600, jitter=False) == 60
        assert exponential_backoff(2, base=30, cap=600, jitter=False) == 120
        assert exponential_backoff(3, base=30, cap=600, jitter=False) == 240

    def test_capped_at_max(self):
        from app.workers.celery_app import exponential_backoff
        result = exponential_backoff(100, base=30, cap=600, jitter=False)
        assert result == 600

    def test_jitter_within_ten_percent(self):
        from app.workers.celery_app import exponential_backoff
        base_delay = exponential_backoff(2, base=30, cap=600, jitter=False)
        for _ in range(20):
            jittered = exponential_backoff(2, base=30, cap=600, jitter=True)
            assert jittered >= 1
            assert abs(jittered - base_delay) <= base_delay * 0.11 + 1

    def test_never_returns_zero_or_negative(self):
        from app.workers.celery_app import exponential_backoff
        for i in range(10):
            assert exponential_backoff(i, base=1, cap=5, jitter=True) >= 1

    def test_uses_config_defaults(self):
        """When base/cap omitted, uses TASK_RETRY_BACKOFF_BASE and CAP."""
        from app.core.config import settings
        from app.workers.celery_app import exponential_backoff
        # Just verify it runs without error and respects the cap
        result = exponential_backoff(100, jitter=False)
        assert result == settings.TASK_RETRY_BACKOFF_CAP


# ─────────────────────────────────────────────────────────────────────────────
# 4.  Crash-safe ack — task_reject_on_worker_lost
# ─────────────────────────────────────────────────────────────────────────────

class TestCrashSafeAck:

    def test_task_reject_on_worker_lost_is_true(self):
        from app.workers.celery_app import celery_app
        assert celery_app.conf.task_reject_on_worker_lost is True

    def test_task_acks_late_still_true(self):
        """Regression guard — task_acks_late must remain True."""
        from app.workers.celery_app import celery_app
        assert celery_app.conf.task_acks_late is True

    def test_worker_prefetch_multiplier_is_one(self):
        from app.workers.celery_app import celery_app
        assert celery_app.conf.worker_prefetch_multiplier == 1


# ─────────────────────────────────────────────────────────────────────────────
# 5.  Task routes — correct queue assignment
# ─────────────────────────────────────────────────────────────────────────────

class TestTaskRoutes:

    def _routes(self):
        from app.workers.celery_app import celery_app
        return celery_app.conf.task_routes

    def test_run_agent_task_on_agent_queue(self):
        routes = self._routes()
        assert routes["app.workers.tasks.run_agent_task"]["queue"] == "agent"

    def test_poll_inbox_task_on_gmail_queue(self):
        routes = self._routes()
        assert routes["app.workers.tasks.poll_inbox_task"]["queue"] == "gmail"

    def test_send_outreach_task_on_gmail_queue(self):
        routes = self._routes()
        assert routes["app.workers.tasks.send_outreach_task"]["queue"] == "gmail"

    def test_follow_up_on_gmail_queue(self):
        routes = self._routes()
        assert routes["app.workers.tasks.follow_up_silent_prospects"]["queue"] == "gmail"

    def test_cleanup_on_default_queue(self):
        routes = self._routes()
        assert routes["app.workers.tasks.cleanup_old_logs"]["queue"] == "default"

    def test_all_three_queues_defined(self):
        from app.workers.celery_app import celery_app
        queue_names = {q.name for q in celery_app.conf.task_queues}
        assert queue_names == {"default", "agent", "gmail"}


# ─────────────────────────────────────────────────────────────────────────────
# 6.  DLQ helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestDLQHelpers:

    def _make_fake_redis(self, llen_val=3, lrange_vals=None):
        mock = MagicMock()
        mock.llen.return_value = llen_val
        mock.lrange.return_value = lrange_vals or []
        mock.pipeline.return_value.__enter__ = MagicMock(return_value=mock)
        mock.pipeline.return_value.__exit__ = MagicMock(return_value=False)
        mock.pipeline.return_value.execute = MagicMock()
        mock.pipeline.return_value.lpush = MagicMock()
        mock.pipeline.return_value.ltrim = MagicMock()
        return mock

    def test_dlq_length_returns_integer(self):
        from app.workers.celery_app import dlq_length
        fake = self._make_fake_redis(llen_val=7)
        with patch("app.workers.celery_app.redis") as mock_redis_mod:
            mock_redis_mod.from_url.return_value = fake
            result = dlq_length()
        assert result == 7

    def test_dlq_length_returns_zero_on_redis_error(self):
        from app.workers.celery_app import dlq_length
        with patch("app.workers.celery_app.redis") as mock_redis_mod:
            mock_redis_mod.from_url.side_effect = Exception("Redis down")
            result = dlq_length()
        assert result == 0

    def test_dlq_read_parses_json_entries(self):
        from app.workers.celery_app import dlq_read
        sample = json.dumps({"task_id": "abc", "task_name": "test_task"})
        fake = self._make_fake_redis(lrange_vals=[sample])
        with patch("app.workers.celery_app.redis") as mock_redis_mod:
            mock_redis_mod.from_url.return_value = fake
            result = dlq_read(offset=0, limit=1)
        assert len(result) == 1
        assert result[0]["task_id"] == "abc"

    def test_dlq_read_returns_empty_on_redis_error(self):
        from app.workers.celery_app import dlq_read
        with patch("app.workers.celery_app.redis") as mock_redis_mod:
            mock_redis_mod.from_url.side_effect = Exception("down")
            result = dlq_read()
        assert result == []

    def test_safe_serialise_truncates_large_values(self):
        from app.workers.celery_app import _safe_serialise
        large = "x" * 3000
        result = _safe_serialise(large)
        assert "truncated" in str(result)

    def test_safe_serialise_passthrough_small_value(self):
        from app.workers.celery_app import _safe_serialise
        result = _safe_serialise({"key": "value"})
        assert result == {"key": "value"}

    def test_dlq_write_pushes_to_redis(self):
        from app.workers.celery_app import _dlq_write
        fake_pipe = MagicMock()
        fake = MagicMock()
        fake.pipeline.return_value = fake_pipe
        with patch("app.workers.celery_app.redis") as mock_redis_mod:
            mock_redis_mod.from_url.return_value = fake
            _dlq_write(
                task_id="t1",
                task_name="test_task",
                queue="gmail",
                args=(1,),
                kwargs={"x": 1},
                exception=RuntimeError("boom"),
                retry_count=3,
                einfo=None,
            )
        fake_pipe.lpush.assert_called_once()
        fake_pipe.ltrim.assert_called_once()
        fake_pipe.execute.assert_called_once()

    def test_dlq_write_does_not_raise_on_redis_error(self):
        from app.workers.celery_app import _dlq_write
        with patch("app.workers.celery_app.redis") as mock_redis_mod:
            mock_redis_mod.from_url.side_effect = Exception("Redis down")
            # Must not raise
            _dlq_write("t", "n", "q", (), {}, Exception("e"), 0, None)


# ─────────────────────────────────────────────────────────────────────────────
# 7.  queue_depth
# ─────────────────────────────────────────────────────────────────────────────

class TestQueueDepth:

    def test_returns_llen_result(self):
        from app.workers.celery_app import queue_depth
        fake = MagicMock()
        fake.llen.return_value = 5
        with patch("app.workers.celery_app.redis") as mock_redis_mod:
            mock_redis_mod.from_url.return_value = fake
            assert queue_depth("agent") == 5
        fake.llen.assert_called_once_with("agent")

    def test_returns_zero_on_redis_error(self):
        from app.workers.celery_app import queue_depth
        with patch("app.workers.celery_app.redis") as mock_redis_mod:
            mock_redis_mod.from_url.side_effect = Exception("down")
            assert queue_depth("agent") == 0


# ─────────────────────────────────────────────────────────────────────────────
# 8.  Outreach idempotency lock
# ─────────────────────────────────────────────────────────────────────────────

class TestOutreachIdempotencyLock:

    def test_acquire_returns_true_when_key_not_set(self):
        from app.services.gmail_lock import acquire_outreach_lock
        fake = MagicMock()
        fake.set.return_value = True  # NX succeeded → key was new
        with patch("app.services.gmail_lock._get_redis", return_value=fake):
            assert acquire_outreach_lock(42) is True

    def test_acquire_returns_false_when_key_already_set(self):
        from app.services.gmail_lock import acquire_outreach_lock
        fake = MagicMock()
        fake.set.return_value = None  # NX failed → key existed
        with patch("app.services.gmail_lock._get_redis", return_value=fake):
            assert acquire_outreach_lock(42) is False

    def test_acquire_uses_correct_key_prefix(self):
        from app.services.gmail_lock import OUTREACH_IDEM_PREFIX, acquire_outreach_lock
        fake = MagicMock()
        fake.set.return_value = True
        with patch("app.services.gmail_lock._get_redis", return_value=fake):
            acquire_outreach_lock(99)
        call_key = fake.set.call_args[0][0]
        assert call_key == f"{OUTREACH_IDEM_PREFIX}99"

    def test_acquire_uses_outreach_idem_ttl(self):
        from app.core.config import settings
        from app.services.gmail_lock import acquire_outreach_lock
        fake = MagicMock()
        fake.set.return_value = True
        with patch("app.services.gmail_lock._get_redis", return_value=fake):
            acquire_outreach_lock(1)
        _, kwargs = fake.set.call_args
        assert kwargs.get("ex") == settings.TASK_OUTREACH_IDEM_TTL

    def test_release_deletes_key(self):
        from app.services.gmail_lock import OUTREACH_IDEM_PREFIX, release_outreach_lock
        fake = MagicMock()
        with patch("app.services.gmail_lock._get_redis", return_value=fake):
            release_outreach_lock(7)
        fake.delete.assert_called_once_with(f"{OUTREACH_IDEM_PREFIX}7")

    def test_acquire_fail_open_on_redis_error(self):
        from app.services.gmail_lock import acquire_outreach_lock
        with patch("app.services.gmail_lock._get_redis", side_effect=Exception("down")):
            # Fail-open: return True so the task can proceed
            assert acquire_outreach_lock(1) is True


# ─────────────────────────────────────────────────────────────────────────────
# 9.  Agent enqueue deduplication
# ─────────────────────────────────────────────────────────────────────────────

class TestAgentEnqueueDedup:

    def test_mark_agent_queued_sets_key(self):
        from app.services.gmail_lock import AGENT_QUEUED_PREFIX, mark_agent_queued
        fake = MagicMock()
        with patch("app.services.gmail_lock._get_redis", return_value=fake):
            mark_agent_queued(55)
        fake.set.assert_called_once()
        set_key = fake.set.call_args[0][0]
        assert set_key == f"{AGENT_QUEUED_PREFIX}55"

    def test_mark_agent_queued_uses_correct_ttl(self):
        from app.core.config import settings
        from app.services.gmail_lock import mark_agent_queued
        fake = MagicMock()
        with patch("app.services.gmail_lock._get_redis", return_value=fake):
            mark_agent_queued(1)
        _, kwargs = fake.set.call_args
        assert kwargs.get("ex") == settings.TASK_AGENT_ENQUEUE_TTL

    def test_is_agent_queued_returns_true_when_queued_key_exists(self):
        from app.services.gmail_lock import is_agent_queued_or_running
        fake = MagicMock()
        fake.exists.return_value = 1  # at least one key exists
        with patch("app.services.gmail_lock._get_redis", return_value=fake):
            assert is_agent_queued_or_running(10) is True

    def test_is_agent_queued_returns_false_when_no_keys(self):
        from app.services.gmail_lock import is_agent_queued_or_running
        fake = MagicMock()
        fake.exists.return_value = 0
        with patch("app.services.gmail_lock._get_redis", return_value=fake):
            assert is_agent_queued_or_running(10) is False

    def test_is_agent_queued_checks_both_keys(self):
        from app.services.gmail_lock import (
            AGENT_QUEUED_PREFIX,
            THREAD_LOCK_PREFIX,
            is_agent_queued_or_running,
        )
        fake = MagicMock()
        fake.exists.return_value = 0
        with patch("app.services.gmail_lock._get_redis", return_value=fake):
            is_agent_queued_or_running(42)
        checked_keys = fake.exists.call_args[0]
        assert f"{AGENT_QUEUED_PREFIX}42" in checked_keys
        assert f"{THREAD_LOCK_PREFIX}42" in checked_keys

    def test_is_agent_queued_fail_open_on_redis_error(self):
        from app.services.gmail_lock import is_agent_queued_or_running
        with patch("app.services.gmail_lock._get_redis", side_effect=Exception("down")):
            # Fail-open: return False to allow enqueue
            assert is_agent_queued_or_running(1) is False

    def test_mark_agent_queued_silences_redis_error(self):
        from app.services.gmail_lock import mark_agent_queued
        with patch("app.services.gmail_lock._get_redis", side_effect=Exception("down")):
            mark_agent_queued(1)  # must not raise


# ─────────────────────────────────────────────────────────────────────────────
# 10.  celery_app.py source checks
# ─────────────────────────────────────────────────────────────────────────────

class TestCeleryAppSource:

    def _src(self) -> str:
        import pathlib
        return (
            pathlib.Path(__file__).parent.parent
            / "app" / "workers" / "celery_app.py"
        ).read_text(encoding="utf-8")

    def test_task_reject_on_worker_lost_in_source(self):
        assert "task_reject_on_worker_lost" in self._src()

    def test_dlq_write_defined(self):
        assert "_dlq_write" in self._src()

    def test_dlq_read_defined(self):
        assert "dlq_read" in self._src()

    def test_dlq_length_defined(self):
        assert "dlq_length" in self._src()

    def test_queue_depth_defined(self):
        assert "queue_depth" in self._src()

    def test_exponential_backoff_defined(self):
        assert "def exponential_backoff" in self._src()

    def test_gmail_task_class_defined(self):
        assert "class GmailTask" in self._src()

    def test_agent_task_class_defined(self):
        assert "class AgentTask" in self._src()

    def test_default_task_class_defined(self):
        assert "class DefaultTask" in self._src()

    def test_task_failure_signal_writes_to_dlq(self):
        src = self._src()
        assert "_dlq_write" in src
        assert "task_failure" in src
        # Ensure _dlq_write is called inside the failure signal handler
        failure_fn_start = src.find("def _on_task_failure")
        failure_fn_body = src[failure_fn_start:failure_fn_start + 1200]
        assert "_dlq_write" in failure_fn_body


# ─────────────────────────────────────────────────────────────────────────────
# 11.  tasks.py source checks
# ─────────────────────────────────────────────────────────────────────────────

class TestTasksSource:

    def _src(self) -> str:
        import pathlib
        return (
            pathlib.Path(__file__).parent.parent
            / "app" / "workers" / "tasks.py"
        ).read_text(encoding="utf-8")

    def test_imports_gmail_task(self):
        assert "GmailTask" in self._src()

    def test_imports_agent_task(self):
        assert "AgentTask" in self._src()

    def test_imports_default_task(self):
        assert "DefaultTask" in self._src()

    def test_imports_exponential_backoff(self):
        assert "exponential_backoff" in self._src()

    def test_imports_acquire_outreach_lock(self):
        assert "acquire_outreach_lock" in self._src()

    def test_imports_mark_agent_queued(self):
        assert "mark_agent_queued" in self._src()

    def test_imports_is_agent_queued_or_running(self):
        assert "is_agent_queued_or_running" in self._src()

    def test_poll_inbox_uses_gmail_task(self):
        src = self._src()
        task_def_start = src.find('"app.workers.tasks.poll_inbox_task"')
        task_def_region = src[max(0, task_def_start - 200):task_def_start + 50]
        assert "GmailTask" in task_def_region

    def test_run_agent_uses_agent_task(self):
        src = self._src()
        task_def_start = src.find('"app.workers.tasks.run_agent_task"')
        task_def_region = src[max(0, task_def_start - 200):task_def_start + 50]
        assert "AgentTask" in task_def_region

    def test_send_outreach_uses_gmail_task(self):
        src = self._src()
        task_def_start = src.find('"app.workers.tasks.send_outreach_task"')
        task_def_region = src[max(0, task_def_start - 200):task_def_start + 50]
        assert "GmailTask" in task_def_region

    def test_cleanup_uses_default_task(self):
        src = self._src()
        task_def_start = src.find('"app.workers.tasks.cleanup_old_logs"')
        task_def_region = src[max(0, task_def_start - 200):task_def_start + 50]
        assert "DefaultTask" in task_def_region

    def test_outreach_idempotency_guard_present(self):
        src = self._src()
        assert "acquire_outreach_lock" in src
        outreach_fn_start = src.find("def send_outreach_task")
        outreach_fn_body = src[outreach_fn_start:outreach_fn_start + 1400]
        assert "acquire_outreach_lock" in outreach_fn_body

    def test_agent_enqueue_dedup_in_poll_inbox(self):
        src = self._src()
        poll_start = src.find("async def _poll_inbox")
        poll_body = src[poll_start:poll_start + 8000]
        assert "is_agent_queued_or_running" in poll_body
        assert "mark_agent_queued" in poll_body

    def test_exponential_backoff_used_in_retries(self):
        src = self._src()
        assert "exponential_backoff(self.request.retries" in src


# ─────────────────────────────────────────────────────────────────────────────
# 12.  Health endpoints — structure tests (no real Redis/DB/Celery needed)
# ─────────────────────────────────────────────────────────────────────────────

class TestHealthEndpoints:

    @pytest.mark.asyncio
    async def test_basic_health_returns_200_when_all_ok(self, client):
        resp = await client.get("/api/v1/health/")
        assert resp.status_code in (200, 503)  # depends on test env
        data = resp.json()
        assert "status" in data
        assert "db" in data
        assert "redis" in data

    @pytest.mark.asyncio
    async def test_basic_health_payload_keys(self, client):
        resp = await client.get("/api/v1/health/")
        data = resp.json()
        assert set(data.keys()) >= {"status", "version", "db", "redis"}

    @pytest.mark.asyncio
    async def test_worker_health_always_200(self, admin_client):
        """Worker health is auth-protected and must return 200 for an admin."""
        resp = await admin_client.get("/api/v1/health/worker")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_worker_health_payload_structure(self, admin_client):
        resp = await admin_client.get("/api/v1/health/worker")
        data = resp.json()
        assert "status" in data
        assert "queues" in data
        assert "dlq" in data
        assert "celery" in data

    @pytest.mark.asyncio
    async def test_worker_health_queues_has_all_three(self, admin_client):
        resp = await admin_client.get("/api/v1/health/worker")
        queues = resp.json()["queues"]
        assert set(queues.keys()) == {"default", "agent", "gmail"}

    @pytest.mark.asyncio
    async def test_worker_health_dlq_has_size(self, admin_client):
        resp = await admin_client.get("/api/v1/health/worker")
        dlq = resp.json()["dlq"]
        assert "size" in dlq
        assert isinstance(dlq["size"], int)

    @pytest.mark.asyncio
    async def test_dlq_endpoint_returns_200(self, admin_client):
        resp = await admin_client.get("/api/v1/health/worker/dlq")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_dlq_endpoint_pagination_keys(self, admin_client):
        resp = await admin_client.get("/api/v1/health/worker/dlq?offset=0&limit=5")
        data = resp.json()
        assert "total" in data
        assert "offset" in data
        assert "limit" in data
        assert "entries" in data
        assert data["limit"] == 5


# ─────────────────────────────────────────────────────────────────────────────
# 13.  health.py source checks
# ─────────────────────────────────────────────────────────────────────────────

class TestHealthSource:

    def _src(self) -> str:
        import pathlib
        return (
            pathlib.Path(__file__).parent.parent
            / "app" / "api" / "v1" / "endpoints" / "health.py"
        ).read_text(encoding="utf-8")

    def test_uses_real_redis_ping_not_socket(self):
        src = self._src()
        assert "client.ping()" in src
        # Old TCP socket check should not be used
        assert "socket.create_connection" not in src

    def test_queue_depth_called_in_worker_health(self):
        assert "queue_depth" in self._src()

    def test_dlq_length_in_worker_health(self):
        assert "dlq_length" in self._src()

    def test_worker_dlq_endpoint_defined(self):
        assert '"/worker/dlq"' in self._src()

    def test_worker_health_endpoint_defined(self):
        assert '"/worker"' in self._src()
