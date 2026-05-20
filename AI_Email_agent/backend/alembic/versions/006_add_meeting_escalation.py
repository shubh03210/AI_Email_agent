"""Add calendar_failure_count and needs_human_review to meetings

Revision ID: 006
Revises: 005
Create Date: 2026-05-20

Adds two columns that drive the human-escalation flow for calendar
operations:

  calendar_failure_count  — running tally of calendar API failures
                            for this meeting (create / reschedule / cancel).
                            Incremented on each failure, never reset.

  needs_human_review      — set to True when calendar_failure_count reaches
                            the configured CALENDAR_MAX_FAILURES threshold.
                            When True, scheduling/rescheduling nodes skip
                            automation and produce a human-escalation reply.

An index on needs_human_review allows easy querying for meetings that
require operator attention.
"""

from alembic import op
import sqlalchemy as sa


revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "meetings",
        sa.Column(
            "calendar_failure_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "meetings",
        sa.Column(
            "needs_human_review",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.create_index(
        "ix_meetings_needs_human_review",
        "meetings",
        ["needs_human_review"],
    )


def downgrade() -> None:
    op.drop_index("ix_meetings_needs_human_review", table_name="meetings")
    op.drop_column("meetings", "needs_human_review")
    op.drop_column("meetings", "calendar_failure_count")
