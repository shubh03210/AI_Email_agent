"""Add error_message column to agent_runs

Revision ID: 004
Revises: 003
Create Date: 2026-05-20

Adds a nullable TEXT column `error_message` to `agent_runs` so that the
observability layer can record exception details for failed node runs.
The column is intentionally nullable — successful runs leave it NULL.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("error_message", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "error_message")
