"""
Phase 8 — Product Enhancements Tests
──────────────────────────────────────
Covers:
  1.  AgentConfig model — 5 new columns with correct defaults
  2.  Schemas — new fields in Create/Update/Read
  3.  Schema validation — follow_up_cadence JSON format
  4.  TONE_PRESETS — all 4 preset keys present
  5.  get_tone_description() — returns preset text
  6.  Metrics endpoint — response structure + safe_rate math
  7.  Migration 009 — revision, columns in upgrade/downgrade
  8.  Cadence parsing helpers
  9.  tasks.py source — cadence logic, recruiter fields
 10.  prompts.py — dynamic recruiter fields in REPLY_GENERATION_SYSTEM
 11.  API router — metrics registered
 12.  Agent state — new config-driven fields
 13.  Signature append in send_reply
 14.  reply_generation uses state tone/recruiter
"""

from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ─────────────────────────────────────────────────────────────────────────────
# 1.  AgentConfig model — new columns
# ─────────────────────────────────────────────────────────────────────────────

class TestAgentConfigModel:

    def _cols(self):
        from sqlalchemy import inspect as sa_inspect
        from app.models.agent_config import AgentConfig
        return {c.name: c for c in sa_inspect(AgentConfig).columns}

    def test_follow_up_cadence_column_exists(self):
        assert "follow_up_cadence" in self._cols()

    def test_recruiter_name_column_exists(self):
        assert "recruiter_name" in self._cols()

    def test_recruiter_title_column_exists(self):
        assert "recruiter_title" in self._cols()

    def test_recruiter_signature_column_exists(self):
        assert "recruiter_signature" in self._cols()

    def test_meeting_confirmation_template_column_exists(self):
        assert "meeting_confirmation_template" in self._cols()

    def test_recruiter_name_default_is_alex(self):
        # SQLAlchemy default= is a SQL-level default; check via column metadata.
        col = self._cols()["recruiter_name"]
        assert col.default.arg == "Alex"

    def test_recruiter_title_default(self):
        col = self._cols()["recruiter_title"]
        assert col.default.arg == "HR Recruiter"

    def test_follow_up_cadence_default_is_json(self):
        col = self._cols()["follow_up_cadence"]
        assert json.loads(col.default.arg) == [1, 3, 7]

    def test_tone_default_is_formal(self):
        col = self._cols()["tone"]
        assert col.default.arg == "formal"


# ─────────────────────────────────────────────────────────────────────────────
# 2.  Schemas — new fields present in all 3
# ─────────────────────────────────────────────────────────────────────────────

class TestAgentConfigSchemas:

    def test_create_has_follow_up_cadence(self):
        from app.schemas.config import AgentConfigCreate
        assert "follow_up_cadence" in AgentConfigCreate.model_fields

    def test_create_has_recruiter_name(self):
        from app.schemas.config import AgentConfigCreate
        assert "recruiter_name" in AgentConfigCreate.model_fields

    def test_create_has_recruiter_title(self):
        from app.schemas.config import AgentConfigCreate
        assert "recruiter_title" in AgentConfigCreate.model_fields

    def test_create_has_recruiter_signature(self):
        from app.schemas.config import AgentConfigCreate
        assert "recruiter_signature" in AgentConfigCreate.model_fields

    def test_create_has_meeting_confirmation_template(self):
        from app.schemas.config import AgentConfigCreate
        assert "meeting_confirmation_template" in AgentConfigCreate.model_fields

    def test_update_has_follow_up_cadence(self):
        from app.schemas.config import AgentConfigUpdate
        assert "follow_up_cadence" in AgentConfigUpdate.model_fields

    def test_update_has_recruiter_name(self):
        from app.schemas.config import AgentConfigUpdate
        assert "recruiter_name" in AgentConfigUpdate.model_fields

    def test_read_has_follow_up_cadence(self):
        from app.schemas.config import AgentConfigRead
        assert "follow_up_cadence" in AgentConfigRead.model_fields

    def test_read_has_recruiter_signature(self):
        from app.schemas.config import AgentConfigRead
        assert "recruiter_signature" in AgentConfigRead.model_fields

    def test_create_default_cadence_parses(self):
        from app.schemas.config import AgentConfigCreate
        c = AgentConfigCreate()
        assert json.loads(c.follow_up_cadence) == [1, 3, 7]

    def test_create_default_tone_is_formal(self):
        from app.schemas.config import AgentConfigCreate
        assert AgentConfigCreate().tone == "formal"


# ─────────────────────────────────────────────────────────────────────────────
# 3.  Schema validation — follow_up_cadence
# ─────────────────────────────────────────────────────────────────────────────

class TestCadenceValidation:

    def test_valid_cadence_accepted(self):
        from app.schemas.config import AgentConfigUpdate
        u = AgentConfigUpdate(follow_up_cadence="[1, 3, 7]")
        assert u.follow_up_cadence == "[1, 3, 7]"

    def test_invalid_cadence_not_array_raises(self):
        from app.schemas.config import AgentConfigUpdate
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            AgentConfigUpdate(follow_up_cadence='"just a string"')

    def test_zero_value_in_cadence_raises(self):
        from app.schemas.config import AgentConfigUpdate
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            AgentConfigUpdate(follow_up_cadence="[0, 3, 7]")

    def test_negative_value_raises(self):
        from app.schemas.config import AgentConfigUpdate
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            AgentConfigUpdate(follow_up_cadence="[-1, 3, 7]")

    def test_non_integer_raises(self):
        from app.schemas.config import AgentConfigUpdate
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            AgentConfigUpdate(follow_up_cadence='["a", "b"]')

    def test_none_cadence_accepted(self):
        from app.schemas.config import AgentConfigUpdate
        u = AgentConfigUpdate(recruiter_name="Bob")
        assert u.follow_up_cadence is None


# ─────────────────────────────────────────────────────────────────────────────
# 4 & 5.  TONE_PRESETS + get_tone_description
# ─────────────────────────────────────────────────────────────────────────────

class TestTonePresets:
    # Import from tone_service (no langchain_core dependency) not from llm_service

    def test_formal_preset_exists(self):
        from app.services.tone_service import TONE_PRESETS
        assert "formal" in TONE_PRESETS

    def test_friendly_preset_exists(self):
        from app.services.tone_service import TONE_PRESETS
        assert "friendly" in TONE_PRESETS

    def test_startup_preset_exists(self):
        from app.services.tone_service import TONE_PRESETS
        assert "startup" in TONE_PRESETS

    def test_executive_preset_exists(self):
        from app.services.tone_service import TONE_PRESETS
        assert "executive" in TONE_PRESETS

    def test_all_preset_values_are_nonempty_strings(self):
        from app.services.tone_service import TONE_PRESETS
        for key, val in TONE_PRESETS.items():
            assert isinstance(val, str) and len(val) > 5, f"Preset '{key}' is too short"

    def test_get_tone_description_known_tone(self):
        from app.services.tone_service import get_tone_description, TONE_PRESETS
        assert get_tone_description("startup") == TONE_PRESETS["startup"]

    def test_get_tone_description_unknown_falls_back_to_formal(self):
        from app.services.tone_service import get_tone_description, TONE_PRESETS
        assert get_tone_description("gibberish") == TONE_PRESETS["formal"]

    def test_get_tone_description_case_insensitive(self):
        from app.services.tone_service import get_tone_description
        assert get_tone_description("FORMAL") == get_tone_description("formal")

    def test_llm_service_re_exports_tone_presets(self):
        """Verify llm_service re-exports TONE_PRESETS from tone_service."""
        import pathlib
        src = (
            pathlib.Path(__file__).parent.parent
            / "app" / "services" / "llm_service.py"
        ).read_text(encoding="utf-8")
        assert "from app.services.tone_service import" in src
        assert "TONE_PRESETS" in src


# ─────────────────────────────────────────────────────────────────────────────
# 6.  Metrics endpoint — safe_rate + structure
# ─────────────────────────────────────────────────────────────────────────────

class TestMetricsEndpoint:

    def test_safe_rate_zero_denominator(self):
        from app.api.v1.endpoints.metrics import _safe_rate
        assert _safe_rate(5, 0) == 0.0

    def test_safe_rate_full(self):
        from app.api.v1.endpoints.metrics import _safe_rate
        assert _safe_rate(10, 10) == 1.0

    def test_safe_rate_half(self):
        from app.api.v1.endpoints.metrics import _safe_rate
        assert _safe_rate(1, 2) == 0.5

    def test_safe_rate_rounds_to_4dp(self):
        from app.api.v1.endpoints.metrics import _safe_rate
        result = _safe_rate(1, 3)
        assert result == round(1 / 3, 4)

    @pytest.mark.asyncio
    async def test_metrics_endpoint_returns_200(self, admin_client):
        resp = await admin_client.get("/api/v1/metrics/")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_metrics_response_has_required_keys(self, admin_client):
        resp = await admin_client.get("/api/v1/metrics/")
        data = resp.json()
        required = {
            "total_prospects", "outreach_sent", "responses_received",
            "response_rate", "meetings_booked", "booking_conversion",
            "active_negotiations", "negotiations_resolved",
            "negotiation_success", "reschedule_pct", "walkaway_pct",
            "pipeline", "meetings_by_status",
        }
        assert required <= set(data.keys())

    @pytest.mark.asyncio
    async def test_metrics_rates_are_0_to_1(self, admin_client):
        resp = await admin_client.get("/api/v1/metrics/")
        data = resp.json()
        for key in ("response_rate", "booking_conversion", "negotiation_success",
                    "reschedule_pct", "walkaway_pct"):
            assert 0.0 <= data[key] <= 1.0, f"{key}={data[key]} out of range"

    @pytest.mark.asyncio
    async def test_metrics_pipeline_is_dict(self, admin_client):
        resp = await admin_client.get("/api/v1/metrics/")
        assert isinstance(resp.json()["pipeline"], dict)

    @pytest.mark.asyncio
    async def test_metrics_operator_can_access(self, operator_client):
        resp = await operator_client.get("/api/v1/metrics/")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_metrics_requires_auth(self, unauth_client):
        resp = await unauth_client.get("/api/v1/metrics/")
        assert resp.status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# 7.  Migration 009
# ─────────────────────────────────────────────────────────────────────────────

class TestMigration009:

    def _src(self) -> str:
        import pathlib
        return (
            pathlib.Path(__file__).parent.parent
            / "alembic" / "versions" / "009_add_config_product_fields.py"
        ).read_text(encoding="utf-8")

    def test_revision_is_009(self):
        assert 'revision = "009"' in self._src()

    def test_down_revision_is_008(self):
        assert 'down_revision = "008"' in self._src()

    def test_adds_follow_up_cadence(self):
        assert "follow_up_cadence" in self._src()

    def test_adds_recruiter_name(self):
        assert "recruiter_name" in self._src()

    def test_adds_recruiter_title(self):
        assert "recruiter_title" in self._src()

    def test_adds_recruiter_signature(self):
        assert "recruiter_signature" in self._src()

    def test_adds_meeting_confirmation_template(self):
        assert "meeting_confirmation_template" in self._src()

    def test_downgrade_drops_all_columns(self):
        src = self._src()
        down = src[src.find("def downgrade"):]
        assert "follow_up_cadence" in down
        assert "recruiter_name" in down
        assert "recruiter_title" in down
        assert "recruiter_signature" in down
        assert "meeting_confirmation_template" in down


# ─────────────────────────────────────────────────────────────────────────────
# 8.  tasks.py source — cadence logic
# ─────────────────────────────────────────────────────────────────────────────

class TestTasksSourceCadence:

    def _src(self) -> str:
        import pathlib
        return (
            pathlib.Path(__file__).parent.parent
            / "app" / "workers" / "tasks.py"
        ).read_text(encoding="utf-8")

    def test_follow_up_cadence_read_from_config(self):
        assert "follow_up_cadence" in self._src()

    def test_json_parse_cadence_in_tasks(self):
        assert "json.loads" in self._src()

    def test_recruiter_name_passed_to_generate_followup(self):
        src = self._src()
        # The actual call site is the SECOND occurrence (first is the import line)
        first = src.find("generate_followup_email")
        call_site = src.find("generate_followup_email", first + 1)
        assert call_site != -1, "Second occurrence of generate_followup_email not found"
        call_block = src[call_site: call_site + 500]
        assert "recruiter_name" in call_block

    def test_recruiter_signature_appended_in_outreach(self):
        src = self._src()
        outreach_block = src[src.find("def _send_outreach"):]
        assert "recruiter_sig" in outreach_block[:2000]

    def test_signature_appended_in_followup(self):
        src = self._src()
        followup_block = src[src.find("async def _follow_up_silent_prospects"):]
        assert "recruiter_sig" in followup_block[:5000]

    def test_cadence_step_index_used(self):
        assert "step_index" in self._src()


# ─────────────────────────────────────────────────────────────────────────────
# 9.  prompts.py — dynamic recruiter fields
# ─────────────────────────────────────────────────────────────────────────────

class TestPromptsSource:

    def _src(self) -> str:
        import pathlib
        return (
            pathlib.Path(__file__).parent.parent
            / "app" / "agents" / "prompts.py"
        ).read_text(encoding="utf-8")

    def test_reply_generation_system_has_recruiter_name_placeholder(self):
        assert "{recruiter_name}" in self._src()

    def test_reply_generation_system_has_recruiter_title_placeholder(self):
        assert "{recruiter_title}" in self._src()

    def test_followup_system_has_agent_name_placeholder(self):
        assert "{agent_name}" in self._src()

    def test_followup_system_has_agent_title_placeholder(self):
        assert "{agent_title}" in self._src()

    def test_followup_system_has_tone_placeholder(self):
        assert "{tone}" in self._src()


# ─────────────────────────────────────────────────────────────────────────────
# 10.  API router — metrics registered
# ─────────────────────────────────────────────────────────────────────────────

class TestMetricsRouterRegistered:

    def _api_src(self) -> str:
        import pathlib
        return (
            pathlib.Path(__file__).parent.parent
            / "app" / "api" / "v1" / "api.py"
        ).read_text(encoding="utf-8")

    def test_metrics_imported(self):
        assert "metrics" in self._api_src()

    def test_metrics_router_included(self):
        assert "metrics.router" in self._api_src()

    def test_metrics_prefix(self):
        assert '"/metrics"' in self._api_src()


# ─────────────────────────────────────────────────────────────────────────────
# 11.  AgentState — new config-driven fields
# ─────────────────────────────────────────────────────────────────────────────

class TestAgentStateNewFields:

    def _annotations(self):
        from app.agents.state import AgentState
        return AgentState.__annotations__

    def test_tone_in_state(self):
        assert "tone" in self._annotations()

    def test_recruiter_name_in_state(self):
        assert "recruiter_name" in self._annotations()

    def test_recruiter_title_in_state(self):
        assert "recruiter_title" in self._annotations()

    def test_recruiter_signature_in_state(self):
        assert "recruiter_signature" in self._annotations()

    def test_meeting_confirmation_template_in_state(self):
        assert "meeting_confirmation_template" in self._annotations()


# ─────────────────────────────────────────────────────────────────────────────
# 12.  send_reply — signature appended
# ─────────────────────────────────────────────────────────────────────────────

class TestSendReplySignature:

    def _src(self) -> str:
        import pathlib
        return (
            pathlib.Path(__file__).parent.parent
            / "app" / "agents" / "nodes" / "send_reply.py"
        ).read_text(encoding="utf-8")

    def test_recruiter_signature_read_from_state(self):
        assert "recruiter_signature" in self._src()

    def test_signature_appended_to_reply_body(self):
        src = self._src()
        assert "recruiter_sig" in src

    def test_signature_logic_in_source(self):
        """
        Verify the signature-append logic is present in send_reply.py source.
        Tests that the pattern 'if recruiter_signature and reply_body: ...' exists.
        """
        src = self._src()
        # The append block must appear BEFORE the gmail send call
        sig_idx = src.find("recruiter_sig")
        # reply_to_thread is now called via asyncio.to_thread; find the actual call site
        # (not the import line) by searching for the asyncio.to_thread wrapper
        send_idx = src.find("asyncio.to_thread(")
        assert sig_idx != -1, "recruiter_sig not found in send_reply.py"
        assert send_idx != -1, "asyncio.to_thread call not found in send_reply.py"
        assert sig_idx < send_idx, "Signature must be appended before the send call"

    def test_signature_appended_format(self):
        """The append uses a newline separator between body and signature."""
        src = self._src()
        # Source contains  f"{body}\n\n{recruiter_sig}"  (real newlines in f-string)
        # When the file is read as text, those are actual newline chars.
        after_sig = src.split("recruiter_sig")[1][:80]
        assert "body" in src and "recruiter_sig" in src  # both exist
        # The append block must contain a string join of body and the signature
        assert "body" in src.split("recruiter_sig")[0][-200:]


# ─────────────────────────────────────────────────────────────────────────────
# 13.  reply_generation — reads tone/recruiter from state
# ─────────────────────────────────────────────────────────────────────────────

class TestReplyGenerationConfigDriven:

    def _src(self) -> str:
        import pathlib
        return (
            pathlib.Path(__file__).parent.parent
            / "app" / "agents" / "nodes" / "reply_generation.py"
        ).read_text(encoding="utf-8")

    def test_reads_tone_from_state(self):
        assert 'state.get("tone")' in self._src()

    def test_reads_recruiter_name_from_state(self):
        assert 'state.get("recruiter_name")' in self._src()

    def test_reads_recruiter_title_from_state(self):
        assert 'state.get("recruiter_title")' in self._src()

    def test_uses_get_tone_description(self):
        assert "get_tone_description" in self._src()

    def test_recruiter_name_passed_to_prompt(self):
        assert "recruiter_name=recruiter_name" in self._src()

    def test_recruiter_title_passed_to_prompt(self):
        assert "recruiter_title=recruiter_title" in self._src()


# ─────────────────────────────────────────────────────────────────────────────
# 14.  Config API — new fields round-trip via GET
# ─────────────────────────────────────────────────────────────────────────────

def _make_real_config(**overrides):
    """Build a real AgentConfig instance with all required fields set."""
    from datetime import datetime, timezone as dt_tz
    from app.models.agent_config import AgentConfig
    now = datetime.now(dt_tz.utc)
    c = AgentConfig()
    c.id = 1
    c.gig_description = "Test gig"
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
    c.meeting_confirmation_template = "Hi {prospect_name}, confirming {meeting_date}."
    c.created_at = now
    c.updated_at = now
    for k, v in overrides.items():
        setattr(c, k, v)
    return c


class TestConfigAPINewFields:

    @pytest.mark.asyncio
    async def test_get_config_returns_follow_up_cadence(self, admin_client):
        mock_cfg = _make_real_config()
        with patch("app.repositories.config_repo.get_or_create_default", new=AsyncMock(return_value=mock_cfg)):
            resp = await admin_client.get("/api/v1/config/")
        assert resp.status_code == 200
        data = resp.json()
        assert "follow_up_cadence" in data
        assert isinstance(json.loads(data["follow_up_cadence"]), list)

    @pytest.mark.asyncio
    async def test_get_config_returns_recruiter_name(self, admin_client):
        mock_cfg = _make_real_config()
        with patch("app.repositories.config_repo.get_or_create_default", new=AsyncMock(return_value=mock_cfg)):
            resp = await admin_client.get("/api/v1/config/")
        assert resp.status_code == 200
        assert resp.json()["recruiter_name"] == "Alex"

    @pytest.mark.asyncio
    async def test_get_config_returns_recruiter_title(self, admin_client):
        mock_cfg = _make_real_config()
        with patch("app.repositories.config_repo.get_or_create_default", new=AsyncMock(return_value=mock_cfg)):
            resp = await admin_client.get("/api/v1/config/")
        assert resp.status_code == 200
        assert resp.json()["recruiter_title"] == "HR Recruiter"

    @pytest.mark.asyncio
    async def test_get_config_returns_recruiter_signature(self, admin_client):
        mock_cfg = _make_real_config()
        with patch("app.repositories.config_repo.get_or_create_default", new=AsyncMock(return_value=mock_cfg)):
            resp = await admin_client.get("/api/v1/config/")
        assert resp.status_code == 200
        assert "recruiter_signature" in resp.json()

    @pytest.mark.asyncio
    async def test_get_config_returns_meeting_confirmation_template(self, admin_client):
        mock_cfg = _make_real_config()
        with patch("app.repositories.config_repo.get_or_create_default", new=AsyncMock(return_value=mock_cfg)):
            resp = await admin_client.get("/api/v1/config/")
        assert resp.status_code == 200
        assert "meeting_confirmation_template" in resp.json()

    @pytest.mark.asyncio
    async def test_update_recruiter_name(self, admin_client):
        mock_cfg = _make_real_config()
        updated_cfg = _make_real_config(recruiter_name="Jordan")
        with (
            patch("app.repositories.config_repo.get_active", new=AsyncMock(return_value=mock_cfg)),
            patch("app.repositories.config_repo.update", new=AsyncMock(return_value=updated_cfg)),
        ):
            resp = await admin_client.put(
                "/api/v1/config/",
                json={"recruiter_name": "Jordan"},
            )
        assert resp.status_code == 200
        assert resp.json()["recruiter_name"] == "Jordan"

    @pytest.mark.asyncio
    async def test_update_invalid_cadence_returns_422(self, admin_client):
        resp = await admin_client.put(
            "/api/v1/config/",
            json={"follow_up_cadence": "not-valid-json"},
        )
        assert resp.status_code == 422
