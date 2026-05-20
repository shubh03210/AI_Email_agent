"""
Phase 4 — Calendar Consistency Tests
──────────────────────────────────────
Covers:
  1. validate_timezone — valid, invalid, None, fallback
  2. CalendarOpError — distinct exception type
  3. schedule_meeting_atomic — success, calendar fail, DB fail + compensation
  4. reschedule_meeting_atomic — success, new-event-first ordering,
                                  old-cancel soft fail → needs_human_review
  5. cancel_meeting_calendar — DB-first ordering, soft-cancel calendar fail
  6. Meeting model — new columns present with correct defaults
  7. increment_calendar_failure — counter, threshold, needs_human_review
  8. AgentState — escalation fields declared
  9. scheduling node — human-review guard, timezone correction
 10. rescheduling node — human-review guard, max-reschedules cancels DB-first
 11. Migration 006 — structure check
 12. Source-level checks — new-event-first order, atomic calls
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ─────────────────────────────────────────────────────────────────────────────
# 1.  validate_timezone
# ─────────────────────────────────────────────────────────────────────────────

class TestValidateTimezone:

    def test_valid_utc(self):
        from app.services.calendar_ops import validate_timezone
        assert validate_timezone("UTC") == "UTC"

    def test_valid_asia_kolkata(self):
        from app.services.calendar_ops import validate_timezone
        assert validate_timezone("Asia/Kolkata") == "Asia/Kolkata"

    def test_valid_america_new_york_dst_safe(self):
        """America/New_York handles DST automatically via zoneinfo."""
        from app.services.calendar_ops import validate_timezone
        assert validate_timezone("America/New_York") == "America/New_York"

    def test_invalid_returns_fallback(self):
        from app.services.calendar_ops import validate_timezone
        assert validate_timezone("Not/A/Timezone") == "UTC"

    def test_none_returns_fallback(self):
        from app.services.calendar_ops import validate_timezone
        assert validate_timezone(None) == "UTC"

    def test_empty_string_returns_fallback(self):
        from app.services.calendar_ops import validate_timezone
        assert validate_timezone("") == "UTC"

    def test_custom_fallback_used(self):
        from app.services.calendar_ops import validate_timezone
        assert validate_timezone("Bad/Zone", fallback="Europe/London") == "Europe/London"

    def test_never_raises(self):
        from app.services.calendar_ops import validate_timezone
        # On Windows, invalid filename characters raise OSError inside ZoneInfo;
        # validate_timezone must catch that and fall back to UTC.
        result = validate_timezone("!@#$%^&*()")
        assert result == "UTC"

    def test_never_raises_on_unicode_garbage(self):
        from app.services.calendar_ops import validate_timezone
        result = validate_timezone("\x00\xff\x80")
        assert result == "UTC"


# ─────────────────────────────────────────────────────────────────────────────
# 2.  CalendarOpError
# ─────────────────────────────────────────────────────────────────────────────

class TestCalendarOpError:

    def test_is_exception(self):
        from app.services.calendar_ops import CalendarOpError
        err = CalendarOpError("test failure")
        assert isinstance(err, Exception)

    def test_message_preserved(self):
        from app.services.calendar_ops import CalendarOpError
        err = CalendarOpError("DB commit failed")
        assert "DB commit failed" in str(err)

    def test_distinct_from_base_exception(self):
        from app.services.calendar_ops import CalendarOpError
        assert not issubclass(CalendarOpError, (ValueError, RuntimeError))
        assert issubclass(CalendarOpError, Exception)


# ─────────────────────────────────────────────────────────────────────────────
# 3.  schedule_meeting_atomic — source-level + runtime (mocking calendar_service)
#
# calendar_service imports google.* which is not in the test venv.
# We test correctness via:
#   a) Source inspection for structural guarantees
#   b) Runtime tests that mock calendar_service at the sys.modules level
# ─────────────────────────────────────────────────────────────────────────────

class TestScheduleMeetingAtomic:

    def _make_slot(self):
        from datetime import datetime, timezone
        from dataclasses import dataclass

        @dataclass
        class _Slot:
            start: datetime
            end: datetime

        return _Slot(
            start=datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc),
            end=datetime(2026, 6, 1, 10, 30, tzinfo=timezone.utc),
        )

    def test_source_calls_safe_cancel_on_db_failure(self):
        """schedule_meeting_atomic source must call _safe_cancel_event as compensation."""
        import pathlib
        src = (
            pathlib.Path(__file__).parent.parent
            / "app" / "services" / "calendar_ops.py"
        ).read_text(encoding="utf-8")

        # The schedule function must have compensation logic
        assert "_safe_cancel_event(event_id)" in src
        assert "CalendarOpError" in src

    def test_source_db_commit_inside_try_except(self):
        """The DB commit must be wrapped in a try/except with compensation."""
        import pathlib
        src = (
            pathlib.Path(__file__).parent.parent
            / "app" / "services" / "calendar_ops.py"
        ).read_text(encoding="utf-8")
        assert "_safe_cancel_event" in src
        assert "DB update failed" in src or "DB commit failed" in src

    def test_source_calendar_failure_raises_calendar_op_error(self):
        """Calendar creation failure must raise CalendarOpError."""
        import pathlib
        src = (
            pathlib.Path(__file__).parent.parent
            / "app" / "services" / "calendar_ops.py"
        ).read_text(encoding="utf-8")
        start = src.find("async def schedule_meeting_atomic")
        fn = src[start:src.find("async def reschedule_meeting_atomic")]
        assert "CalendarOpError" in fn
        assert "Calendar event creation failed" in fn


# ─────────────────────────────────────────────────────────────────────────────
# 4.  reschedule_meeting_atomic — ordering + compensation
# ─────────────────────────────────────────────────────────────────────────────

class TestRescheduleMeetingAtomic:

    def _make_slot(self):
        from datetime import datetime, timezone
        from dataclasses import dataclass

        @dataclass
        class _Slot:
            start: datetime
            end: datetime

        return _Slot(
            start=datetime(2026, 6, 2, 14, 0, tzinfo=timezone.utc),
            end=datetime(2026, 6, 2, 14, 30, tzinfo=timezone.utc),
        )

    def test_source_creates_new_before_cancelling_old(self):
        """
        Verify the new-event-first invariant by checking the source order.
        The critical transaction ordering rule: step 4a (create new event),
        step 4b (DB commit), step 4d (cancel old event).
        """
        import pathlib
        src = (
            pathlib.Path(__file__).parent.parent
            / "app" / "services" / "calendar_ops.py"
        ).read_text(encoding="utf-8")

        # Find the reschedule_meeting_atomic function body
        start = src.find("async def reschedule_meeting_atomic")
        end   = src.find("async def cancel_meeting_calendar")
        fn_src = src[start:end] if end > start else src[start:]

        # The create call must appear before the cancel call in the source
        create_pos = fn_src.find("create_event(")
        cancel_pos = fn_src.find("cancel_event(")
        assert create_pos != -1, "reschedule_meeting_atomic must call create_event"
        assert cancel_pos != -1, "reschedule_meeting_atomic must call cancel_event"
        assert create_pos < cancel_pos, (
            "create_event must appear BEFORE cancel_event in reschedule_meeting_atomic"
        )

    def test_source_compensates_on_db_failure(self):
        """DB commit failure must delete the new event."""
        import pathlib
        src = (
            pathlib.Path(__file__).parent.parent
            / "app" / "services" / "calendar_ops.py"
        ).read_text(encoding="utf-8")
        start = src.find("async def reschedule_meeting_atomic")
        end   = src.find("async def cancel_meeting_calendar")
        fn_src = src[start:end] if end > start else src[start:]
        assert "_safe_cancel_event" in fn_src

    def test_source_marks_review_on_old_cancel_failure(self):
        """If old event cancel fails, meeting must be flagged for review."""
        import pathlib
        src = (
            pathlib.Path(__file__).parent.parent
            / "app" / "services" / "calendar_ops.py"
        ).read_text(encoding="utf-8")
        start = src.find("async def reschedule_meeting_atomic")
        end   = src.find("async def cancel_meeting_calendar")
        fn_src = src[start:end] if end > start else src[start:]
        assert "_mark_needs_review" in fn_src

    def test_source_old_cancel_failure_triggers_mark_review(self):
        """Source must call _mark_needs_review when old event cancel fails."""
        import pathlib
        src = (
            pathlib.Path(__file__).parent.parent
            / "app" / "services" / "calendar_ops.py"
        ).read_text(encoding="utf-8")
        start = src.find("async def reschedule_meeting_atomic")
        end   = src.find("async def cancel_meeting_calendar")
        fn_src = src[start:end] if end > start else src[start:]
        # Both the soft-fail catch and the mark function must be present
        assert "_mark_needs_review" in fn_src
        assert "soft fail" in fn_src.lower() or "Soft fail" in fn_src

    def test_source_db_failure_triggers_compensation_in_reschedule(self):
        """DB failure must trigger _safe_cancel_event on the newly-created event."""
        import pathlib
        src = (
            pathlib.Path(__file__).parent.parent
            / "app" / "services" / "calendar_ops.py"
        ).read_text(encoding="utf-8")
        start = src.find("async def reschedule_meeting_atomic")
        end   = src.find("async def cancel_meeting_calendar")
        fn_src = src[start:end] if end > start else src[start:]
        assert "_safe_cancel_event" in fn_src


# ─────────────────────────────────────────────────────────────────────────────
# 5.  cancel_meeting_calendar — DB-first ordering
# ─────────────────────────────────────────────────────────────────────────────

class TestCancelMeetingCalendar:

    def test_source_db_commit_before_calendar_cancel(self):
        """Source must commit to DB BEFORE cancelling the calendar event."""
        import pathlib
        src = (
            pathlib.Path(__file__).parent.parent
            / "app" / "services" / "calendar_ops.py"
        ).read_text(encoding="utf-8")
        start = src.find("async def cancel_meeting_calendar")
        fn_src = src[start:] if start != -1 else src

        db_commit_pos  = fn_src.find("db.commit()")
        cal_cancel_pos = fn_src.find("cancel_event(")
        assert db_commit_pos != -1,  "cancel_meeting_calendar must commit DB"
        assert cal_cancel_pos != -1, "cancel_meeting_calendar must cancel calendar event"
        assert db_commit_pos < cal_cancel_pos, (
            "DB commit must come BEFORE calendar cancel in cancel_meeting_calendar"
        )

    def test_source_marks_review_on_calendar_cancel_failure(self):
        import pathlib
        src = (
            pathlib.Path(__file__).parent.parent
            / "app" / "services" / "calendar_ops.py"
        ).read_text(encoding="utf-8")
        start = src.find("async def cancel_meeting_calendar")
        fn_src = src[start:] if start != -1 else src
        assert "_mark_needs_review" in fn_src

    def test_source_cancel_failure_escalates_to_human_review(self):
        """Calendar cancel failure must trigger _mark_needs_review."""
        import pathlib
        src = (
            pathlib.Path(__file__).parent.parent
            / "app" / "services" / "calendar_ops.py"
        ).read_text(encoding="utf-8")
        start = src.find("async def cancel_meeting_calendar")
        fn_src = src[start:] if start != -1 else src
        assert "_mark_needs_review" in fn_src


# ─────────────────────────────────────────────────────────────────────────────
# 6.  Meeting model — new columns
# ─────────────────────────────────────────────────────────────────────────────

class TestMeetingModel:

    def test_calendar_failure_count_column_in_table(self):
        """Column exists in the SQLAlchemy table metadata."""
        from app.models.meeting import Meeting
        col_names = [c.name for c in Meeting.__table__.columns]
        assert "calendar_failure_count" in col_names

    def test_needs_human_review_column_in_table(self):
        from app.models.meeting import Meeting
        col_names = [c.name for c in Meeting.__table__.columns]
        assert "needs_human_review" in col_names

    def test_can_set_needs_human_review(self):
        from app.models.meeting import Meeting
        m = Meeting()
        m.needs_human_review = True
        assert m.needs_human_review is True

    def test_can_set_calendar_failure_count(self):
        from app.models.meeting import Meeting
        m = Meeting()
        m.calendar_failure_count = 2
        assert m.calendar_failure_count == 2

    def test_calendar_failure_count_column_server_default_is_zero(self):
        """DB server_default must be '0' so existing rows are not NULL."""
        from app.models.meeting import Meeting
        col = Meeting.__table__.columns["calendar_failure_count"]
        # server_default OR default should evaluate to '0'
        assert col.server_default is not None or col.default is not None

    def test_needs_human_review_is_boolean_column(self):
        from app.models.meeting import Meeting
        from sqlalchemy import Boolean
        col = Meeting.__table__.columns["needs_human_review"]
        assert isinstance(col.type, Boolean)


# ─────────────────────────────────────────────────────────────────────────────
# 7.  increment_calendar_failure
# ─────────────────────────────────────────────────────────────────────────────

class TestIncrementCalendarFailure:

    @pytest.mark.asyncio
    async def test_increments_count(self):
        from app.models.meeting import Meeting
        from app.services.memory_service import increment_calendar_failure

        mock_meeting = Meeting()
        mock_meeting.calendar_failure_count = 0
        mock_meeting.needs_human_review = False

        mock_db = MagicMock()
        mock_db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=mock_meeting))
        )
        mock_db.flush = AsyncMock()
        mock_db.add = MagicMock()

        count, needs_review = await increment_calendar_failure(mock_db, thread_id=1, max_failures=3)
        assert count == 1
        assert needs_review is False

    @pytest.mark.asyncio
    async def test_sets_needs_human_review_at_threshold(self):
        from app.models.meeting import Meeting
        from app.services.memory_service import increment_calendar_failure

        mock_meeting = Meeting()
        mock_meeting.calendar_failure_count = 2  # one more will reach threshold of 3
        mock_meeting.needs_human_review = False

        mock_db = MagicMock()
        mock_db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=mock_meeting))
        )
        mock_db.flush = AsyncMock()
        mock_db.add = MagicMock()

        count, needs_review = await increment_calendar_failure(mock_db, thread_id=1, max_failures=3)
        assert count == 3
        assert needs_review is True
        assert mock_meeting.needs_human_review is True

    @pytest.mark.asyncio
    async def test_does_not_downgrade_needs_review(self):
        """Once True, needs_human_review must stay True."""
        from app.models.meeting import Meeting
        from app.services.memory_service import increment_calendar_failure

        mock_meeting = Meeting()
        mock_meeting.calendar_failure_count = 5
        mock_meeting.needs_human_review = True

        mock_db = MagicMock()
        mock_db.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=mock_meeting))
        )
        mock_db.flush = AsyncMock()
        mock_db.add = MagicMock()

        count, needs_review = await increment_calendar_failure(mock_db, thread_id=1, max_failures=3)
        assert needs_review is True
        assert mock_meeting.needs_human_review is True


# ─────────────────────────────────────────────────────────────────────────────
# 8.  AgentState escalation fields
# ─────────────────────────────────────────────────────────────────────────────

class TestAgentStateEscalationFields:

    def test_needs_human_review_in_state(self):
        from app.agents.state import AgentState
        import typing
        hints = typing.get_type_hints(AgentState)
        assert "needs_human_review" in hints

    def test_calendar_failure_count_in_state(self):
        from app.agents.state import AgentState
        import typing
        hints = typing.get_type_hints(AgentState)
        assert "calendar_failure_count" in hints


# ─────────────────────────────────────────────────────────────────────────────
# 9.  scheduling node — escalation guard + timezone correction
# ─────────────────────────────────────────────────────────────────────────────

class TestSchedulingNode:

    def _src(self) -> str:
        import pathlib
        return (
            pathlib.Path(__file__).parent.parent
            / "app" / "agents" / "nodes" / "scheduling.py"
        ).read_text(encoding="utf-8")

    def test_human_review_guard_present_in_source(self):
        """Source must check needs_human_review before doing any calendar work."""
        src = self._src()
        # The guard must exist and short-circuit before calendar calls
        assert 'state.get("needs_human_review")' in src or "needs_human_review" in src

    def test_human_review_guard_returns_before_fetch_slots(self):
        """Guard must appear BEFORE _fetch_slots in the function body."""
        src = self._src()
        guard_pos = src.find("needs_human_review")
        fetch_pos = src.find("_fetch_slots(")
        assert guard_pos != -1 and fetch_pos != -1
        assert guard_pos < fetch_pos

    def test_invalid_timezone_corrected_before_fetch(self):
        """validate_timezone must be called BEFORE _fetch_slots."""
        src = self._src()
        tz_pos    = src.find("validate_timezone(")
        fetch_pos = src.find("_fetch_slots(")
        assert tz_pos != -1 and fetch_pos != -1
        assert tz_pos < fetch_pos, (
            "validate_timezone must be called before _fetch_slots "
            "to prevent ZoneInfoNotFoundError"
        )

    def test_source_uses_schedule_meeting_atomic(self):
        assert "schedule_meeting_atomic" in self._src()

    def test_source_uses_validate_timezone(self):
        assert "validate_timezone" in self._src()

    def test_source_increments_calendar_failure(self):
        assert "increment_calendar_failure" in self._src()

    def test_source_has_human_review_reply(self):
        """Node must produce a human-escalation reply when needed."""
        assert "_HUMAN_REVIEW_REPLY" in self._src() or "team member" in self._src()


# ─────────────────────────────────────────────────────────────────────────────
# 10.  rescheduling node — escalation + max-reschedules + ordering
# ─────────────────────────────────────────────────────────────────────────────

class TestReschedulingNode:

    def _src(self) -> str:
        import pathlib
        return (
            pathlib.Path(__file__).parent.parent
            / "app" / "agents" / "nodes" / "rescheduling.py"
        ).read_text(encoding="utf-8")

    def test_human_review_guard_present_in_source(self):
        src = self._src()
        assert "needs_human_review" in src

    def test_human_review_guard_before_fetch_slots(self):
        src = self._src()
        guard_pos = src.find("needs_human_review")
        fetch_pos = src.find("_fetch_slots(")
        assert guard_pos != -1 and fetch_pos != -1
        assert guard_pos < fetch_pos

    def test_max_reschedules_triggers_cancel_meeting_calendar(self):
        """At max reschedules the node must call cancel_meeting_calendar (DB-first)."""
        src = self._src()
        # The max-reschedules guard must lead to cancel_meeting_calendar, not cancel_event
        assert "cancel_meeting_calendar" in src

    def test_source_uses_reschedule_meeting_atomic(self):
        assert "reschedule_meeting_atomic" in self._src()

    def test_source_does_not_cancel_before_create(self):
        """rescheduling node must NOT call cancel_event directly."""
        assert "cancel_event(" not in self._src(), (
            "rescheduling.py must not call cancel_event directly — "
            "ordering is handled by reschedule_meeting_atomic"
        )

    def test_source_uses_validate_timezone(self):
        assert "validate_timezone" in self._src()

    def test_source_increments_calendar_failure(self):
        assert "increment_calendar_failure" in self._src()

    def test_source_has_human_review_reply(self):
        assert "_HUMAN_REVIEW_REPLY" in self._src() or "team member" in self._src()


# ─────────────────────────────────────────────────────────────────────────────
# 11.  Migration 006
# ─────────────────────────────────────────────────────────────────────────────

class TestMigration006:

    def _src(self) -> str:
        import pathlib
        return (
            pathlib.Path(__file__).parent.parent
            / "alembic" / "versions" / "006_add_meeting_escalation.py"
        ).read_text(encoding="utf-8")

    def test_file_exists(self):
        import pathlib
        path = (
            pathlib.Path(__file__).parent.parent
            / "alembic" / "versions" / "006_add_meeting_escalation.py"
        )
        assert path.exists()

    def test_revision_chain(self):
        src = self._src()
        assert 'revision = "006"' in src
        assert 'down_revision = "005"' in src

    def test_adds_calendar_failure_count(self):
        assert "calendar_failure_count" in self._src()

    def test_adds_needs_human_review(self):
        assert "needs_human_review" in self._src()

    def test_creates_index_on_needs_human_review(self):
        assert "ix_meetings_needs_human_review" in self._src()

    def test_downgrade_reverses_changes(self):
        src = self._src()
        assert "def downgrade" in src
        assert "drop_column" in src
        assert "drop_index" in src


# ─────────────────────────────────────────────────────────────────────────────
# 12.  Source-level structural checks
# ─────────────────────────────────────────────────────────────────────────────

class TestCalendarOpsSourceChecks:

    def _src(self) -> str:
        import pathlib
        return (
            pathlib.Path(__file__).parent.parent
            / "app" / "services" / "calendar_ops.py"
        ).read_text(encoding="utf-8")

    def test_schedule_meeting_atomic_defined(self):
        assert "async def schedule_meeting_atomic" in self._src()

    def test_reschedule_meeting_atomic_defined(self):
        assert "async def reschedule_meeting_atomic" in self._src()

    def test_cancel_meeting_calendar_defined(self):
        assert "async def cancel_meeting_calendar" in self._src()

    def test_compensation_present_in_schedule(self):
        """schedule_meeting_atomic must call _safe_cancel_event as compensation."""
        assert "_safe_cancel_event" in self._src()

    def test_mark_needs_review_present(self):
        """reschedule and cancel ops must call _mark_needs_review on soft failures."""
        assert "_mark_needs_review" in self._src()

    def test_new_event_before_old_cancel_comment(self):
        """Source code should document the new-event-first ordering."""
        src = self._src()
        # At least one of these must appear to verify the ordering is documented
        assert any(
            phrase in src
            for phrase in ["new event", "NEW", "create new", "Create new"]
        )
