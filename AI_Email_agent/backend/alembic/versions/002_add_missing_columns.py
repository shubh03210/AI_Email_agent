"""Add missing columns to email_threads and agent_configs

Revision ID: 002
Revises: 001
Create Date: 2026-05-20

Adds columns that exist in ORM models but were absent from the initial migration:
  - email_threads.follow_up_count
  - email_threads.last_outreach_at
  - agent_configs.follow_up_days
  - agent_configs.max_follow_ups

Uses ADD COLUMN IF NOT EXISTS so the migration is safe to run against a Supabase
database where these columns may have been added directly via the hosted SQL editor.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE email_threads
            ADD COLUMN IF NOT EXISTS follow_up_count  INTEGER                    NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS last_outreach_at TIMESTAMP WITH TIME ZONE   NULL
        """
    )
    op.execute(
        """
        ALTER TABLE agent_configs
            ADD COLUMN IF NOT EXISTS follow_up_days  INTEGER NOT NULL DEFAULT 1,
            ADD COLUMN IF NOT EXISTS max_follow_ups  INTEGER NOT NULL DEFAULT 2
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE email_threads  DROP COLUMN IF EXISTS follow_up_count")
    op.execute("ALTER TABLE email_threads  DROP COLUMN IF EXISTS last_outreach_at")
    op.execute("ALTER TABLE agent_configs  DROP COLUMN IF EXISTS follow_up_days")
    op.execute("ALTER TABLE agent_configs  DROP COLUMN IF EXISTS max_follow_ups")
