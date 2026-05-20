"""
Phase 7 — Data Model Fixes Tests
──────────────────────────────────
Covers:
  1.  Prospect model — company column present, nullable, max 255
  2.  ProspectCreate schema — accepts company (optional)
  3.  ProspectUpdate schema — accepts company (optional)
  4.  ProspectRead schema — serialises company (None when absent)
  5.  prospect_repo.create() — stores company
  6.  prospect_repo.list_prospects() — search covers company
  7.  Prospect.threads — passive_deletes=True
  8.  EmailThread relationships — passive_deletes=True on all 4
  9.  Migration 008 — column + indexes present in upgrade/downgrade
 10.  API create endpoint — company round-trips correctly
 11.  API list endpoint — search by company works
 12.  Cascade delete chain — delete flow via models is safe
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ─────────────────────────────────────────────────────────────────────────────
# 1.  Prospect model — company column
# ─────────────────────────────────────────────────────────────────────────────

class TestProspectModelCompany:

    def test_company_column_exists_on_model(self):
        from sqlalchemy import inspect as sa_inspect
        from app.models.prospect import Prospect
        cols = {c.name for c in sa_inspect(Prospect).columns}
        assert "company" in cols

    def test_company_column_is_nullable(self):
        from sqlalchemy import inspect as sa_inspect
        from app.models.prospect import Prospect
        col = sa_inspect(Prospect).columns["company"]
        assert col.nullable is True

    def test_company_column_max_length_255(self):
        from sqlalchemy import inspect as sa_inspect
        from app.models.prospect import Prospect
        col = sa_inspect(Prospect).columns["company"]
        assert col.type.length == 255

    def test_prospect_instance_company_defaults_to_none(self):
        from app.models.prospect import Prospect
        p = Prospect()
        assert p.company is None

    def test_prospect_company_can_be_set(self):
        from app.models.prospect import Prospect
        p = Prospect()
        p.company = "Acme Corp"
        assert p.company == "Acme Corp"

    def test_threads_relationship_has_passive_deletes(self):
        from sqlalchemy.orm import class_mapper
        from app.models.prospect import Prospect
        rel = class_mapper(Prospect).relationships["threads"]
        assert rel.passive_deletes is True


# ─────────────────────────────────────────────────────────────────────────────
# 2 & 3.  Schemas — ProspectCreate, ProspectUpdate
# ─────────────────────────────────────────────────────────────────────────────

class TestProspectSchemas:

    def test_prospect_create_accepts_company(self):
        from app.schemas.prospect import ProspectCreate
        p = ProspectCreate(name="Alice", email="alice@example.com", company="Acme")
        assert p.company == "Acme"

    def test_prospect_create_company_is_optional(self):
        from app.schemas.prospect import ProspectCreate
        p = ProspectCreate(name="Alice", email="alice@example.com")
        assert p.company is None

    def test_prospect_create_company_max_length_enforced(self):
        from app.schemas.prospect import ProspectCreate
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ProspectCreate(name="A", email="a@b.com", company="x" * 256)

    def test_prospect_update_accepts_company(self):
        from app.schemas.prospect import ProspectUpdate
        u = ProspectUpdate(company="New Corp")
        assert u.company == "New Corp"

    def test_prospect_update_company_is_optional(self):
        from app.schemas.prospect import ProspectUpdate
        u = ProspectUpdate(name="Bob")
        assert u.company is None

    def test_prospect_update_company_max_length_enforced(self):
        from app.schemas.prospect import ProspectUpdate
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ProspectUpdate(company="y" * 256)

    def test_prospect_read_includes_company_field(self):
        from app.schemas.prospect import ProspectRead
        assert "company" in ProspectRead.model_fields

    def test_prospect_read_serialises_none_company(self):
        from datetime import datetime, timezone
        from app.models.prospect import Prospect, ProspectStatus
        from app.schemas.prospect import ProspectRead
        p = Prospect()
        p.id = 1
        p.name = "Test"
        p.email = "t@example.com"
        p.company = None
        p.timezone = "UTC"
        p.status = ProspectStatus.PENDING.value
        now = datetime.now(timezone.utc)
        p.created_at = now
        p.updated_at = now
        schema = ProspectRead.model_validate(p)
        assert schema.company is None

    def test_prospect_read_serialises_set_company(self):
        from datetime import datetime, timezone
        from app.models.prospect import Prospect, ProspectStatus
        from app.schemas.prospect import ProspectRead
        p = Prospect()
        p.id = 2
        p.name = "Jane"
        p.email = "jane@example.com"
        p.company = "TechCo"
        p.timezone = "UTC"
        p.status = ProspectStatus.PENDING.value
        now = datetime.now(timezone.utc)
        p.created_at = now
        p.updated_at = now
        schema = ProspectRead.model_validate(p)
        assert schema.company == "TechCo"


# ─────────────────────────────────────────────────────────────────────────────
# 5.  prospect_repo.create() — stores company
# ─────────────────────────────────────────────────────────────────────────────

class TestProspectRepoCreate:

    @pytest.mark.asyncio
    async def test_create_passes_company_to_model(self):
        from app.schemas.prospect import ProspectCreate
        import app.repositories.prospect_repo as repo

        captured: dict = {}

        class FakeDB:
            async def flush(self): pass
            async def refresh(self, obj): pass
            def add(self, obj):
                captured["obj"] = obj

        payload = ProspectCreate(
            name="Jane",
            email="jane@example.com",
            company="Acme Ltd",
        )
        result = await repo.create(FakeDB(), payload)
        assert result.company == "Acme Ltd"

    @pytest.mark.asyncio
    async def test_create_company_none_when_not_provided(self):
        from app.schemas.prospect import ProspectCreate
        import app.repositories.prospect_repo as repo

        class FakeDB:
            async def flush(self): pass
            async def refresh(self, obj): pass
            def add(self, obj): pass

        payload = ProspectCreate(name="Bob", email="bob@example.com")
        result = await repo.create(FakeDB(), payload)
        assert result.company is None


# ─────────────────────────────────────────────────────────────────────────────
# 6.  prospect_repo.list_prospects() — search covers company
# ─────────────────────────────────────────────────────────────────────────────

class TestProspectRepoSearch:

    def _extract_search_clause(self, src: str) -> str:
        """Pull the OR clause from list_prospects source."""
        import pathlib
        path = pathlib.Path(__file__).parent.parent / "app" / "repositories" / "prospect_repo.py"
        return path.read_text(encoding="utf-8")

    def test_search_includes_company_ilike(self):
        src = self._extract_search_clause("")
        assert "Prospect.company.ilike(pattern)" in src

    def test_search_still_includes_name_ilike(self):
        src = self._extract_search_clause("")
        assert "Prospect.name.ilike(pattern)" in src

    def test_search_still_includes_email_ilike(self):
        src = self._extract_search_clause("")
        assert "Prospect.email.ilike(pattern)" in src


# ─────────────────────────────────────────────────────────────────────────────
# 8.  EmailThread relationships — passive_deletes on all 4
# ─────────────────────────────────────────────────────────────────────────────

class TestEmailThreadPassiveDeletes:

    def _rel(self, name: str):
        from sqlalchemy.orm import class_mapper
        from app.models.email_thread import EmailThread
        return class_mapper(EmailThread).relationships[name]

    def test_messages_passive_deletes(self):
        assert self._rel("messages").passive_deletes is True

    def test_negotiation_passive_deletes(self):
        assert self._rel("negotiation").passive_deletes is True

    def test_meeting_passive_deletes(self):
        assert self._rel("meeting").passive_deletes is True

    def test_agent_runs_passive_deletes(self):
        assert self._rel("agent_runs").passive_deletes is True

    def test_cascade_still_set_on_messages(self):
        assert "delete" in self._rel("messages").cascade

    def test_cascade_still_set_on_meeting(self):
        assert "delete" in self._rel("meeting").cascade

    def test_cascade_still_set_on_negotiation(self):
        assert "delete" in self._rel("negotiation").cascade

    def test_cascade_still_set_on_agent_runs(self):
        assert "delete" in self._rel("agent_runs").cascade


# ─────────────────────────────────────────────────────────────────────────────
# 9.  Migration 008 — column + indexes
# ─────────────────────────────────────────────────────────────────────────────

class TestMigration008:

    def _src(self) -> str:
        import pathlib
        return (
            pathlib.Path(__file__).parent.parent
            / "alembic" / "versions" / "008_add_prospect_company_and_indexes.py"
        ).read_text(encoding="utf-8")

    def test_revision_is_008(self):
        assert 'revision = "008"' in self._src()

    def test_down_revision_is_007(self):
        assert 'down_revision = "007"' in self._src()

    def test_adds_company_column(self):
        assert '"company"' in self._src()
        assert "add_column" in self._src()

    def test_creates_ix_prospects_company(self):
        assert "ix_prospects_company" in self._src()

    def test_creates_ix_email_messages_thread_timestamp(self):
        assert "ix_email_messages_thread_timestamp" in self._src()

    def test_creates_ix_email_messages_timestamp(self):
        assert "ix_email_messages_timestamp" in self._src()

    def test_creates_ix_agent_runs_thread_created(self):
        assert "ix_agent_runs_thread_created" in self._src()

    def test_downgrade_drops_company_column(self):
        src = self._src()
        downgrade_start = src.find("def downgrade")
        downgrade_body = src[downgrade_start:]
        assert "drop_column" in downgrade_body
        assert '"company"' in downgrade_body

    def test_downgrade_drops_all_indexes(self):
        # Phase 10 fix: ix_email_messages_thread_timestamp and
        # ix_email_messages_timestamp were duplicates (already in 001) so they
        # were removed from 008's upgrade/downgrade.  Only 2 indexes remain.
        src = self._src()
        downgrade_start = src.find("def downgrade")
        downgrade_body = src[downgrade_start:]
        assert "ix_agent_runs_thread_created" in downgrade_body
        assert "ix_prospects_company" in downgrade_body


# ─────────────────────────────────────────────────────────────────────────────
# 10.  API create endpoint — company round-trips
# ─────────────────────────────────────────────────────────────────────────────

class TestProspectAPICompany:

    @pytest.mark.asyncio
    async def test_create_with_company_returns_company(self, admin_client):
        from datetime import datetime, timezone
        from app.models.prospect import Prospect, ProspectStatus

        mock_prospect = Prospect()
        mock_prospect.id = 99
        mock_prospect.name = "Corp User"
        mock_prospect.email = "corp@example.com"
        mock_prospect.company = "BigCorp"
        mock_prospect.timezone = "UTC"
        mock_prospect.status = ProspectStatus.PENDING.value
        now = datetime.now(timezone.utc)
        mock_prospect.created_at = now
        mock_prospect.updated_at = now

        with (
            patch("app.repositories.prospect_repo.get_by_email", new=AsyncMock(return_value=None)),
            patch("app.repositories.prospect_repo.create",       new=AsyncMock(return_value=mock_prospect)),
        ):
            resp = await admin_client.post(
                "/api/v1/prospects/",
                json={"name": "Corp User", "email": "corp@example.com", "company": "BigCorp"},
            )
        assert resp.status_code == 201
        data = resp.json()
        assert data["company"] == "BigCorp"

    @pytest.mark.asyncio
    async def test_create_without_company_returns_null(self, admin_client):
        from datetime import datetime, timezone
        from app.models.prospect import Prospect, ProspectStatus

        mock_prospect = Prospect()
        mock_prospect.id = 100
        mock_prospect.name = "No Corp"
        mock_prospect.email = "nocorp@example.com"
        mock_prospect.company = None
        mock_prospect.timezone = "UTC"
        mock_prospect.status = ProspectStatus.PENDING.value
        now = datetime.now(timezone.utc)
        mock_prospect.created_at = now
        mock_prospect.updated_at = now

        with (
            patch("app.repositories.prospect_repo.get_by_email", new=AsyncMock(return_value=None)),
            patch("app.repositories.prospect_repo.create",       new=AsyncMock(return_value=mock_prospect)),
        ):
            resp = await admin_client.post(
                "/api/v1/prospects/",
                json={"name": "No Corp", "email": "nocorp@example.com"},
            )
        assert resp.status_code == 201
        data = resp.json()
        assert data["company"] is None

    @pytest.mark.asyncio
    async def test_list_returns_company_in_items(self, admin_client):
        from datetime import datetime, timezone
        from app.models.prospect import Prospect, ProspectStatus

        p = Prospect()
        p.id = 1
        p.name = "Alice"
        p.email = "alice@example.com"
        p.company = "StartupX"
        p.timezone = "UTC"
        p.status = ProspectStatus.PENDING.value
        now = datetime.now(timezone.utc)
        p.created_at = now
        p.updated_at = now

        with patch("app.repositories.prospect_repo.list_prospects", new=AsyncMock(return_value=([p], 1))):
            resp = await admin_client.get("/api/v1/prospects/")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["company"] == "StartupX"


# ─────────────────────────────────────────────────────────────────────────────
# 11.  Cascade delete models — verify both layers configured
# ─────────────────────────────────────────────────────────────────────────────

class TestCascadeDeleteConfig:

    def test_prospect_threads_fk_has_ondelete_cascade(self):
        from sqlalchemy import inspect as sa_inspect
        from app.models.email_thread import EmailThread
        cols = sa_inspect(EmailThread).columns
        fk = list(cols["prospect_id"].foreign_keys)[0]
        assert fk.ondelete == "CASCADE"

    def test_email_message_thread_fk_has_ondelete_cascade(self):
        from sqlalchemy import inspect as sa_inspect
        from app.models.email_message import EmailMessage
        cols = sa_inspect(EmailMessage).columns
        fk = list(cols["thread_id"].foreign_keys)[0]
        assert fk.ondelete == "CASCADE"

    def test_meeting_thread_fk_has_ondelete_cascade(self):
        from sqlalchemy import inspect as sa_inspect
        from app.models.meeting import Meeting
        cols = sa_inspect(Meeting).columns
        fk = list(cols["thread_id"].foreign_keys)[0]
        assert fk.ondelete == "CASCADE"

    def test_negotiation_thread_fk_has_ondelete_cascade(self):
        from sqlalchemy import inspect as sa_inspect
        from app.models.negotiation import Negotiation
        cols = sa_inspect(Negotiation).columns
        fk = list(cols["thread_id"].foreign_keys)[0]
        assert fk.ondelete == "CASCADE"

    def test_agent_run_thread_fk_has_ondelete_cascade(self):
        from sqlalchemy import inspect as sa_inspect
        from app.models.agent_run import AgentRun
        cols = sa_inspect(AgentRun).columns
        fk = list(cols["thread_id"].foreign_keys)[0]
        assert fk.ondelete == "CASCADE"

    def test_prospect_threads_cascade_includes_delete(self):
        from sqlalchemy.orm import class_mapper
        from app.models.prospect import Prospect
        rel = class_mapper(Prospect).relationships["threads"]
        assert "delete" in rel.cascade

    def test_prospect_threads_passive_deletes(self):
        from sqlalchemy.orm import class_mapper
        from app.models.prospect import Prospect
        rel = class_mapper(Prospect).relationships["threads"]
        assert rel.passive_deletes is True
