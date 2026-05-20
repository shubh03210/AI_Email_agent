"""Add prospect.company column and performance indexes

Revision ID: 008
Revises: 007
Create Date: 2026-05-20

Phase 7 — Data Model Fixes

Changes in this migration:

1. prospects.company (new column)
   - Nullable String(255) so existing rows are unaffected.
   - Indexed for company-name search and filtering.

2. ix_agent_runs_thread_created  (new composite index)
   - Covers the paginated log listing query:
       SELECT ... FROM agent_runs
       WHERE thread_id = ?
       ORDER BY created_at
       LIMIT ? OFFSET ?
   - Mirrors the same optimisation applied to email_messages above.
"""

from alembic import op
import sqlalchemy as sa


revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. prospects.company ──────────────────────────────────────────────────
    op.add_column(
        "prospects",
        sa.Column("company", sa.String(255), nullable=True),
    )
    op.create_index(
        "ix_prospects_company",
        "prospects",
        ["company"],
    )

    # ── 2. agent_runs: composite (thread_id, created_at) ─────────────────────
    # NOTE: ix_email_messages_thread_timestamp and ix_email_messages_timestamp
    # were already created in migration 001 — do not re-create them here.
    op.create_index(
        "ix_agent_runs_thread_created",
        "agent_runs",
        ["thread_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_runs_thread_created", table_name="agent_runs")
    op.drop_index("ix_prospects_company", table_name="prospects")
    op.drop_column("prospects", "company")
