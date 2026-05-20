import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    OPERATOR = "operator"


class User(Base, TimestampMixin):
    """
    Authenticated users who can access the dashboard API.

    Roles
    ─────
    admin    – full read/write access to every endpoint
    operator – read access + all agent-operation endpoints
               (trigger outreach, run agent, reschedule meeting)
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True, index=True)
    username: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    hashed_password: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=UserRole.OPERATOR.value,
        index=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, index=True
    )
    last_login_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_users_role_active", "role", "is_active"),
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} username={self.username!r} role={self.role}>"
