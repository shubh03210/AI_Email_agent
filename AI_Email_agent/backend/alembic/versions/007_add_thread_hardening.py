"""Add agent-hardening columns to email_threads

Revision ID: 007
Revises: 006
Create Date: 2026-05-20

Phase 5 — LangGraph Hardening

Adds four columns to email_threads that support:

  thread_summary     — Rolling LLM-generated summary of messages outside the
                       memory window.  Used by build_windowed_context() so the
                       LLM always receives compact, relevant context.

  ambiguous_count    — Consecutive classify_intent runs that returned
                       "ambiguous".  Reset to 0 when any non-ambiguous intent
                       is detected.  Drives the repeated-ambiguity escalation.

  agent_escalated    — Set True when the agent can no longer handle a thread
                       autonomously (low confidence, repeated ambiguity, API
                       failures).  When True: agent produces a human-handoff
                       reply and stops making routing decisions.

  escalation_reason  — Short human-readable string explaining why escalation
                       was triggered, stored for operator review.

An index on agent_escalated allows operators to query all escalated threads
efficiently.
"""

from alembic import op
import sqlalchemy as sa


revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "email_threads",
        sa.Column("thread_summary", sa.Text(), nullable=True),
    )
    op.add_column(
        "email_threads",
        sa.Column(
            "ambiguous_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "email_threads",
        sa.Column(
            "agent_escalated",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "email_threads",
        sa.Column("escalation_reason", sa.String(512), nullable=True),
    )
    op.create_index(
        "ix_email_threads_agent_escalated",
        "email_threads",
        ["agent_escalated"],
    )


def downgrade() -> None:
    op.drop_index("ix_email_threads_agent_escalated", table_name="email_threads")
    op.drop_column("email_threads", "escalation_reason")
    op.drop_column("email_threads", "agent_escalated")
    op.drop_column("email_threads", "ambiguous_count")
    op.drop_column("email_threads", "thread_summary")
