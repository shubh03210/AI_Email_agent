"""Add product-feature columns to agent_configs

Revision ID: 009
Revises: 008
Create Date: 2026-05-20

Phase 8 — Product Enhancements

New columns on agent_configs:

  follow_up_cadence          — JSON-encoded list of day offsets for multi-step
                               follow-up cadence (e.g. "[1, 3, 7]").  Replaces
                               the single follow_up_days integer with a
                               configurable per-step schedule.

  recruiter_name             — Display name of the recruiter persona (default
                               "Alex").  Replaces the hardcoded string in
                               LLM prompts and email signatures.

  recruiter_title            — Job title line for the recruiter persona (default
                               "HR Recruiter").

  recruiter_signature        — Free-text footer appended to every outbound email
                               body after LLM generation.  Supports multi-line
                               text, URL, phone number etc.

  meeting_confirmation_template — Jinja-style template string for meeting
                               confirmation emails.  Placeholders:
                               {prospect_name}, {meeting_date}, {meeting_time},
                               {timezone}, {recruiter_name}, {recruiter_title}.
"""

from alembic import op
import sqlalchemy as sa

_DEFAULT_CADENCE = "[1, 3, 7]"

_DEFAULT_CONFIRMATION_TEMPLATE = (
    "Hi {prospect_name},\n\n"
    "Just confirming our meeting on {meeting_date} at {meeting_time} ({timezone}).\n\n"
    "I'll send over a calendar invite shortly. Looking forward to connecting!\n\n"
    "Best regards,\n"
    "{recruiter_name}\n"
    "{recruiter_title}"
)

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_configs",
        sa.Column(
            "follow_up_cadence",
            sa.Text(),
            nullable=False,
            server_default=_DEFAULT_CADENCE,
        ),
    )
    op.add_column(
        "agent_configs",
        sa.Column(
            "recruiter_name",
            sa.String(128),
            nullable=False,
            server_default="Alex",
        ),
    )
    op.add_column(
        "agent_configs",
        sa.Column(
            "recruiter_title",
            sa.String(128),
            nullable=False,
            server_default="HR Recruiter",
        ),
    )
    op.add_column(
        "agent_configs",
        sa.Column(
            "recruiter_signature",
            sa.Text(),
            nullable=False,
            server_default="",
        ),
    )
    op.add_column(
        "agent_configs",
        sa.Column(
            "meeting_confirmation_template",
            sa.Text(),
            nullable=False,
            server_default=_DEFAULT_CONFIRMATION_TEMPLATE,
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_configs", "meeting_confirmation_template")
    op.drop_column("agent_configs", "recruiter_signature")
    op.drop_column("agent_configs", "recruiter_title")
    op.drop_column("agent_configs", "recruiter_name")
    op.drop_column("agent_configs", "follow_up_cadence")
