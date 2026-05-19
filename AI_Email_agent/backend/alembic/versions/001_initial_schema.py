"""Initial schema

Revision ID: 001
Revises:
Create Date: 2026-05-19

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:

    # ── prospects ────────────────────────────────────────────────────────────
    op.create_table(
        "prospects",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("email", name="uq_prospects_email"),
    )
    op.create_index("ix_prospects_id",              "prospects", ["id"])
    op.create_index("ix_prospects_email",            "prospects", ["email"])
    op.create_index("ix_prospects_status",           "prospects", ["status"])
    op.create_index("ix_prospects_status_created",   "prospects", ["status", "created_at"])

    # ── email_threads ────────────────────────────────────────────────────────
    op.create_table(
        "email_threads",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("gmail_thread_id", sa.String(255), nullable=False),
        sa.Column("prospect_id", sa.Integer(), sa.ForeignKey("prospects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subject", sa.String(998), nullable=False, server_default="(No Subject)"),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("gmail_thread_id", name="uq_email_threads_gmail_thread_id"),
    )
    op.create_index("ix_email_threads_id",               "email_threads", ["id"])
    op.create_index("ix_email_threads_gmail_thread_id",  "email_threads", ["gmail_thread_id"])
    op.create_index("ix_email_threads_prospect_id",      "email_threads", ["prospect_id"])
    op.create_index("ix_email_threads_status",           "email_threads", ["status"])
    op.create_index("ix_email_threads_prospect_status",  "email_threads", ["prospect_id", "status"])

    # ── email_messages ───────────────────────────────────────────────────────
    op.create_table(
        "email_messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("thread_id", sa.Integer(), sa.ForeignKey("email_threads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sender", sa.String(320), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("raw_payload", JSONB(), nullable=True),
        sa.Column("intent", sa.String(32), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_email_messages_id",               "email_messages", ["id"])
    op.create_index("ix_email_messages_thread_id",        "email_messages", ["thread_id"])
    op.create_index("ix_email_messages_intent",           "email_messages", ["intent"])
    op.create_index("ix_email_messages_timestamp",        "email_messages", ["timestamp"])
    op.create_index("ix_email_messages_thread_timestamp", "email_messages", ["thread_id", "timestamp"])

    # ── negotiations ─────────────────────────────────────────────────────────
    op.create_table(
        "negotiations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("thread_id", sa.Integer(), sa.ForeignKey("email_threads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("max_budget", sa.Float(), nullable=False),
        sa.Column("current_offer", sa.Float(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("thread_id", name="uq_negotiations_thread_id"),
    )
    op.create_index("ix_negotiations_id",        "negotiations", ["id"])
    op.create_index("ix_negotiations_thread_id", "negotiations", ["thread_id"])
    op.create_index("ix_negotiations_status",    "negotiations", ["status"])

    # ── meetings ─────────────────────────────────────────────────────────────
    op.create_table(
        "meetings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("thread_id", sa.Integer(), sa.ForeignKey("email_threads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("google_event_id", sa.String(255), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="proposed"),
        sa.Column("reschedule_count", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("thread_id",        name="uq_meetings_thread_id"),
        sa.UniqueConstraint("google_event_id",  name="uq_meetings_google_event_id"),
    )
    op.create_index("ix_meetings_id",            "meetings", ["id"])
    op.create_index("ix_meetings_thread_id",     "meetings", ["thread_id"])
    op.create_index("ix_meetings_status",        "meetings", ["status"])
    op.create_index("ix_meetings_scheduled_at",  "meetings", ["scheduled_at"])

    # ── agent_configs ────────────────────────────────────────────────────────
    op.create_table(
        "agent_configs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("gig_description", sa.Text(), nullable=False, server_default=""),
        sa.Column("budget_ceiling", sa.Float(), nullable=False, server_default="5000.0"),
        sa.Column("tone", sa.String(64), nullable=False, server_default="professional"),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
        sa.Column("working_hours_start", sa.Integer(), nullable=False, server_default="9"),
        sa.Column("working_hours_end", sa.Integer(), nullable=False, server_default="18"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_agent_configs_id",        "agent_configs", ["id"])
    op.create_index("ix_agent_configs_is_active", "agent_configs", ["is_active"])

    # ── agent_runs ───────────────────────────────────────────────────────────
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("thread_id", sa.Integer(), sa.ForeignKey("email_threads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("node_name", sa.String(128), nullable=False),
        sa.Column("input_payload", JSONB(), nullable=True),
        sa.Column("output_payload", JSONB(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_agent_runs_id",         "agent_runs", ["id"])
    op.create_index("ix_agent_runs_thread_id",  "agent_runs", ["thread_id"])
    op.create_index("ix_agent_runs_node_name",  "agent_runs", ["node_name"])
    op.create_index("ix_agent_runs_status",     "agent_runs", ["status"])
    op.create_index("ix_agent_runs_created_at", "agent_runs", ["created_at"])


def downgrade() -> None:
    op.drop_table("agent_runs")
    op.drop_table("agent_configs")
    op.drop_table("meetings")
    op.drop_table("negotiations")
    op.drop_table("email_messages")
    op.drop_table("email_threads")
    op.drop_table("prospects")
