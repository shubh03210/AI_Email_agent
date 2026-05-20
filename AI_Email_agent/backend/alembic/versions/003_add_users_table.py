"""Add users table for JWT authentication

Revision ID: 003
Revises: 002
Create Date: 2026-05-20

Creates the `users` table that backs the JWT authentication system.
The application bootstraps a default admin user from ADMIN_USERNAME /
ADMIN_PASSWORD environment variables on first startup — no seed data
is embedded in this migration so that credential management stays
outside of version control.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id",              sa.Integer(),                  primary_key=True, autoincrement=True),
        sa.Column("username",        sa.String(64),                 nullable=False),
        sa.Column("hashed_password", sa.String(256),                nullable=False),
        sa.Column("role",            sa.String(32),                 nullable=False, server_default="operator"),
        sa.Column("is_active",       sa.Boolean(),                  nullable=False, server_default="true"),
        sa.Column("last_login_at",   sa.DateTime(timezone=True),    nullable=True),
        sa.Column("created_at",      sa.DateTime(timezone=True),    nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at",      sa.DateTime(timezone=True),    nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("username", name="uq_users_username"),
    )
    op.create_index("ix_users_id",          "users", ["id"])
    op.create_index("ix_users_username",    "users", ["username"])
    op.create_index("ix_users_role",        "users", ["role"])
    op.create_index("ix_users_is_active",   "users", ["is_active"])
    op.create_index("ix_users_role_active", "users", ["role", "is_active"])


def downgrade() -> None:
    op.drop_table("users")
