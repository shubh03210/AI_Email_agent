"""Add gmail_message_id to email_messages for indexed idempotency

Revision ID: 005
Revises: 004
Create Date: 2026-05-20

Adds a dedicated, indexed `gmail_message_id` column to `email_messages`.

Previously, duplicate-message detection relied on a JSONB-field scan
(`raw_payload->>'id' = ?`) which cannot use a B-tree index and requires
knowing the `thread_id` in advance.

With this column:
  - Inbound messages store their Gmail message ID at ingest time.
  - Outbound messages store the Gmail message ID returned by the send API.
  - A partial unique index (WHERE gmail_message_id IS NOT NULL) acts as a
    database-level guard against duplicate rows even under race conditions.
"""

from alembic import op
import sqlalchemy as sa


revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add the column (nullable — existing rows get NULL)
    op.add_column(
        "email_messages",
        sa.Column("gmail_message_id", sa.String(255), nullable=True),
    )

    # Regular index for fast lookups by message ID
    op.create_index(
        "ix_email_messages_gmail_message_id",
        "email_messages",
        ["gmail_message_id"],
    )

    # Partial unique index — prevents duplicate rows for the same Gmail message
    # across all threads (no thread_id scope needed).
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uix_email_messages_gmail_id
        ON email_messages (gmail_message_id)
        WHERE gmail_message_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uix_email_messages_gmail_id")
    op.drop_index("ix_email_messages_gmail_message_id", table_name="email_messages")
    op.drop_column("email_messages", "gmail_message_id")
