"""Add counter_round and last_prospect_offer to negotiations

Revision ID: 010
Revises: 009
Create Date: 2026-05-20

Phase 10 — Production Audit Fixes

Changes:
  1. negotiations.counter_round (Integer, NOT NULL, default 0)
     Persists the negotiation round counter across agent runs so the
     walkaway-after-N-rounds rule can accumulate across emails.

  2. negotiations.last_prospect_offer (Float, nullable)
     Stores the most recent dollar amount the prospect offered, enabling
     Rule 3: "prospect is not moving → walkaway".
"""

from alembic import op
import sqlalchemy as sa


revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "negotiations",
        sa.Column("counter_round", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "negotiations",
        sa.Column("last_prospect_offer", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("negotiations", "last_prospect_offer")
    op.drop_column("negotiations", "counter_round")
