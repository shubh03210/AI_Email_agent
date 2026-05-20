"""
Role-Based Access Control (RBAC) Tests
────────────────────────────────────────
Tests that every protected endpoint enforces the correct role.

Strategy:
  - admin_client   fixture → get_current_user overridden to return admin User
  - operator_client fixture → get_current_user overridden to return operator User
  - unauth_client  fixture → no auth override (genuine 401 scenarios)
  - Repository calls that would hit the DB are patched per test
  - We test HTTP status codes, not business logic (that is tested in e2e tests)

Role matrix being verified:
  Route                           admin  operator
  ─────────────────────────────────────────────
  GET  /prospects/                 200    200
  POST /prospects/                 201    403
  PUT  /prospects/{id}             200*   403
  DELETE /prospects/{id}           204*   403
  POST /prospects/{id}/outreach    202    202
  GET  /threads/                   200    200
  POST /threads/{id}/run           202    202
  GET  /meetings/                  200    200
  POST /meetings/{id}/cancel       200*   403
  POST /meetings/{id}/reschedule   202    202
  GET  /config/                    200    200
  PUT  /config/                    200*   403
  POST /config/                    201*   403
  GET  /agent/poll/{task_id}       200    200
  GET  /logs/                      200    200
  ─────────────────────────────────────────────
  * may return 404/500 due to missing DB data, but will NOT be 403

  No token at all  → 401 on all protected routes
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient


# ── Unauthenticated (401) ─────────────────────────────────────────────────────

class TestUnauthenticated:
    """Every protected endpoint must return 401 with no Authorization header."""

    @pytest.mark.parametrize("method,path", [
        ("GET",    "/api/v1/prospects/"),
        ("POST",   "/api/v1/prospects/"),
        ("GET",    "/api/v1/threads/"),
        ("GET",    "/api/v1/meetings/"),
        ("GET",    "/api/v1/config/"),
        ("PUT",    "/api/v1/config/"),
        ("GET",    "/api/v1/logs/"),
        ("POST",   "/api/v1/agent/start"),
    ])
    async def test_protected_route_without_token_returns_401(
        self, unauth_client: AsyncClient, method: str, path: str
    ):
        # Remove the get_db override so auth is the only override cleared
        from app.api.v1.deps import get_db
        from app.main import app

        # unauth_client has get_db overridden but NOT get_current_user
        resp = await unauth_client.request(method, path)
        assert resp.status_code == 401, (
            f"{method} {path} should be 401 without token, got {resp.status_code}"
        )

    async def test_invalid_bearer_token_returns_401(self, unauth_client: AsyncClient):
        resp = await unauth_client.get(
            "/api/v1/prospects/",
            headers={"Authorization": "Bearer thisisnotavalidtoken"},
        )
        assert resp.status_code == 401

    async def test_expired_token_returns_401(self, unauth_client: AsyncClient):
        from datetime import timedelta
        from app.core.security import create_access_token
        expired = create_access_token(
            subject="ghost",
            role="admin",
            expires_delta=timedelta(seconds=-1),
        )
        resp = await unauth_client.get(
            "/api/v1/prospects/",
            headers={"Authorization": f"Bearer {expired}"},
        )
        assert resp.status_code == 401


# ── Prospects RBAC ────────────────────────────────────────────────────────────

class TestProspectsRBAC:

    async def test_operator_can_list_prospects(self, operator_client: AsyncClient):
        mock_result = ([], 0)
        with patch("app.repositories.prospect_repo.list_prospects", new=AsyncMock(return_value=mock_result)):
            resp = await operator_client.get("/api/v1/prospects/")
        assert resp.status_code == 200

    async def test_admin_can_list_prospects(self, admin_client: AsyncClient):
        mock_result = ([], 0)
        with patch("app.repositories.prospect_repo.list_prospects", new=AsyncMock(return_value=mock_result)):
            resp = await admin_client.get("/api/v1/prospects/")
        assert resp.status_code == 200

    async def test_operator_cannot_create_prospect(self, operator_client: AsyncClient):
        resp = await operator_client.post(
            "/api/v1/prospects/",
            json={"name": "Test", "email": "test@example.com"},
        )
        assert resp.status_code == 403

    async def test_admin_can_create_prospect(self, admin_client: AsyncClient):
        from datetime import datetime, timezone
        from app.models.prospect import Prospect, ProspectStatus
        mock_prospect = Prospect()
        mock_prospect.id = 1
        mock_prospect.name = "Test"
        mock_prospect.email = "test@example.com"
        mock_prospect.timezone = "UTC"
        mock_prospect.status = ProspectStatus.PENDING.value
        mock_prospect.created_at = datetime.now(timezone.utc)
        mock_prospect.updated_at = datetime.now(timezone.utc)

        with (
            patch("app.repositories.prospect_repo.get_by_email", new=AsyncMock(return_value=None)),
            patch("app.repositories.prospect_repo.create",       new=AsyncMock(return_value=mock_prospect)),
        ):
            resp = await admin_client.post(
                "/api/v1/prospects/",
                json={"name": "Test", "email": "test@example.com"},
            )
        assert resp.status_code == 201

    async def test_operator_cannot_update_prospect(self, operator_client: AsyncClient):
        resp = await operator_client.put(
            "/api/v1/prospects/1",
            json={"name": "Updated"},
        )
        assert resp.status_code == 403

    async def test_operator_cannot_delete_prospect(self, operator_client: AsyncClient):
        resp = await operator_client.delete("/api/v1/prospects/1")
        assert resp.status_code == 403

    async def test_operator_can_trigger_outreach(self, operator_client: AsyncClient):
        from app.models.prospect import Prospect, ProspectStatus
        mock_p = Prospect()
        mock_p.id = 5
        mock_p.email = "p@example.com"
        mock_p.status = ProspectStatus.PENDING.value

        with (
            patch("app.repositories.prospect_repo.get_by_id", new=AsyncMock(return_value=mock_p)),
            patch("app.workers.tasks.send_outreach_task") as mock_task,
        ):
            mock_task.delay.return_value = MagicMock(id="task-abc")
            resp = await operator_client.post("/api/v1/prospects/5/outreach")
        assert resp.status_code in (200, 202, 500)
        assert resp.status_code != 403

    async def test_admin_can_trigger_outreach(self, admin_client: AsyncClient):
        from app.models.prospect import Prospect, ProspectStatus
        mock_p = Prospect()
        mock_p.id = 5
        mock_p.email = "p@example.com"
        mock_p.status = ProspectStatus.PENDING.value

        with (
            patch("app.repositories.prospect_repo.get_by_id", new=AsyncMock(return_value=mock_p)),
            patch("app.workers.tasks.send_outreach_task") as mock_task,
        ):
            mock_task.delay.return_value = MagicMock(id="task-abc")
            resp = await admin_client.post("/api/v1/prospects/5/outreach")
        assert resp.status_code != 403


# ── Threads RBAC ──────────────────────────────────────────────────────────────

class TestThreadsRBAC:

    async def test_operator_can_list_threads(self, operator_client: AsyncClient):
        with patch("app.repositories.thread_repo.list_threads", new=AsyncMock(return_value=([], 0))):
            resp = await operator_client.get("/api/v1/threads/")
        assert resp.status_code == 200

    async def test_operator_can_run_agent_on_thread(self, operator_client: AsyncClient):
        from app.models.email_thread import EmailThread
        mock_thread = MagicMock(spec=EmailThread)
        mock_thread.id = 1

        with (
            patch("app.repositories.thread_repo.get_by_id", new=AsyncMock(return_value=mock_thread)),
            patch("app.workers.tasks.run_agent_task") as mock_task,
        ):
            mock_task.delay.return_value = MagicMock(id="task-xyz")
            resp = await operator_client.post("/api/v1/threads/1/run")
        assert resp.status_code not in (401, 403)

    async def test_admin_can_run_agent_on_thread(self, admin_client: AsyncClient):
        from app.models.email_thread import EmailThread
        mock_thread = MagicMock(spec=EmailThread)
        mock_thread.id = 1

        with (
            patch("app.repositories.thread_repo.get_by_id", new=AsyncMock(return_value=mock_thread)),
            patch("app.workers.tasks.run_agent_task") as mock_task,
        ):
            mock_task.delay.return_value = MagicMock(id="task-xyz")
            resp = await admin_client.post("/api/v1/threads/1/run")
        assert resp.status_code not in (401, 403)


# ── Meetings RBAC ─────────────────────────────────────────────────────────────

class TestMeetingsRBAC:

    async def test_operator_can_list_meetings(self, operator_client: AsyncClient):
        with patch("app.repositories.meeting_repo.list_meetings", new=AsyncMock(return_value=([], 0))):
            resp = await operator_client.get("/api/v1/meetings/")
        assert resp.status_code == 200

    async def test_operator_cannot_cancel_meeting(self, operator_client: AsyncClient):
        resp = await operator_client.post("/api/v1/meetings/1/cancel")
        assert resp.status_code == 403

    async def test_admin_can_cancel_meeting(self, admin_client: AsyncClient):
        from app.models.meeting import Meeting, MeetingStatus
        mock_meeting = MagicMock(spec=Meeting)
        mock_meeting.id = 1
        mock_meeting.status = MeetingStatus.CONFIRMED.value
        mock_meeting.google_event_id = None

        with patch("app.repositories.meeting_repo.get_by_id", new=AsyncMock(return_value=mock_meeting)):
            resp = await admin_client.post("/api/v1/meetings/1/cancel")
        assert resp.status_code not in (401, 403)

    async def test_operator_can_reschedule_meeting(self, operator_client: AsyncClient):
        from app.models.meeting import Meeting, MeetingStatus
        mock_meeting = MagicMock(spec=Meeting)
        mock_meeting.id = 2
        mock_meeting.thread_id = 10
        mock_meeting.status = MeetingStatus.CONFIRMED.value

        with (
            patch("app.repositories.meeting_repo.get_by_id", new=AsyncMock(return_value=mock_meeting)),
            patch("app.workers.tasks.run_agent_task") as mock_task,
        ):
            mock_task.delay.return_value = MagicMock(id="task-resched")
            resp = await operator_client.post("/api/v1/meetings/2/reschedule")
        assert resp.status_code != 403

    async def test_admin_can_reschedule_meeting(self, admin_client: AsyncClient):
        from app.models.meeting import Meeting, MeetingStatus
        mock_meeting = MagicMock(spec=Meeting)
        mock_meeting.id = 2
        mock_meeting.thread_id = 10
        mock_meeting.status = MeetingStatus.CONFIRMED.value

        with (
            patch("app.repositories.meeting_repo.get_by_id", new=AsyncMock(return_value=mock_meeting)),
            patch("app.workers.tasks.run_agent_task") as mock_task,
        ):
            mock_task.delay.return_value = MagicMock(id="task-resched")
            resp = await admin_client.post("/api/v1/meetings/2/reschedule")
        assert resp.status_code != 403


# ── Config RBAC ───────────────────────────────────────────────────────────────

class TestConfigRBAC:

    def _mock_config(self, **overrides):
        from datetime import datetime, timezone as dt_tz
        from app.models.agent_config import AgentConfig
        c = AgentConfig()
        c.id = 1
        c.gig_description = "Test"
        c.budget_ceiling = 5000.0
        c.tone = "formal"
        c.timezone = "UTC"
        c.working_hours_start = 9
        c.working_hours_end = 18
        c.follow_up_days = 3
        c.max_follow_ups = 3
        c.is_active = True
        c.follow_up_cadence = "[1, 3, 7]"
        c.recruiter_name = "Alex"
        c.recruiter_title = "HR Recruiter"
        c.recruiter_signature = ""
        c.meeting_confirmation_template = "Hi {prospect_name}, meeting on {meeting_date}."
        now = datetime.now(dt_tz.utc)
        c.created_at = now
        c.updated_at = now
        for k, v in overrides.items():
            setattr(c, k, v)
        return c

    async def test_operator_can_read_config(self, operator_client: AsyncClient):
        mock_cfg = self._mock_config()
        with patch("app.repositories.config_repo.get_or_create_default", new=AsyncMock(return_value=mock_cfg)):
            resp = await operator_client.get("/api/v1/config/")
        assert resp.status_code == 200

    async def test_admin_can_read_config(self, admin_client: AsyncClient):
        mock_cfg = self._mock_config()
        with patch("app.repositories.config_repo.get_or_create_default", new=AsyncMock(return_value=mock_cfg)):
            resp = await admin_client.get("/api/v1/config/")
        assert resp.status_code == 200

    async def test_operator_cannot_update_config(self, operator_client: AsyncClient):
        resp = await operator_client.put(
            "/api/v1/config/",
            json={"tone": "casual"},
        )
        assert resp.status_code == 403

    async def test_operator_cannot_create_config(self, operator_client: AsyncClient):
        resp = await operator_client.post(
            "/api/v1/config/",
            json={"gig_description": "Test", "budget_ceiling": 5000},
        )
        assert resp.status_code == 403

    async def test_admin_can_update_config(self, admin_client: AsyncClient):
        mock_cfg = self._mock_config(tone="casual")
        with (
            patch("app.repositories.config_repo.get_active",  new=AsyncMock(return_value=mock_cfg)),
            patch("app.repositories.config_repo.update",      new=AsyncMock(return_value=mock_cfg)),
        ):
            resp = await admin_client.put("/api/v1/config/", json={"tone": "casual"})
        assert resp.status_code not in (401, 403)


# ── Logs RBAC ─────────────────────────────────────────────────────────────────

class TestLogsRBAC:

    async def test_operator_can_list_logs(self, operator_client: AsyncClient):
        with patch("app.repositories.agent_run_repo.list_runs", new=AsyncMock(return_value=([], 0))):
            resp = await operator_client.get("/api/v1/logs/")
        assert resp.status_code == 200

    async def test_admin_can_list_logs(self, admin_client: AsyncClient):
        with patch("app.repositories.agent_run_repo.list_runs", new=AsyncMock(return_value=([], 0))):
            resp = await admin_client.get("/api/v1/logs/")
        assert resp.status_code == 200


# ── Token introspection (role embedded in JWT) ────────────────────────────────

class TestTokenRoleClaims:
    """Verify that the JWT issued for admin/operator carries the correct role claim."""

    def test_admin_token_has_admin_role(self):
        from app.core.security import create_access_token, decode_access_token
        token = create_access_token(subject="admin_user", role="admin")
        payload = decode_access_token(token)
        assert payload["role"] == "admin"
        assert payload["sub"] == "admin_user"

    def test_operator_token_has_operator_role(self):
        from app.core.security import create_access_token, decode_access_token
        token = create_access_token(subject="op_user", role="operator")
        payload = decode_access_token(token)
        assert payload["role"] == "operator"

    def test_admin_role_differs_from_operator_role(self):
        from app.core.security import create_access_token, decode_access_token
        admin_token = create_access_token(subject="u", role="admin")
        op_token = create_access_token(subject="u", role="operator")
        assert decode_access_token(admin_token)["role"] != decode_access_token(op_token)["role"]
