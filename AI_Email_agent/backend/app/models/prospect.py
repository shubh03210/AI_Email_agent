import enum
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.email_thread import EmailThread


class ProspectStatus(str, enum.Enum):
    PENDING = "pending"
    CONTACTED = "contacted"
    INTERESTED = "interested"
    NEGOTIATING = "negotiating"
    SCHEDULED = "scheduled"
    DECLINED = "declined"
    CLOSED = "closed"


class Prospect(Base, TimestampMixin):
    __tablename__ = "prospects"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    company: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=ProspectStatus.PENDING.value,
        index=True,
    )

    # passive_deletes=True: rely on DB-level ON DELETE CASCADE rather than
    # loading all child EmailThread rows into memory before deletion.
    threads: Mapped[List["EmailThread"]] = relationship(
        "EmailThread",
        back_populates="prospect",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )

    __table_args__ = (
        Index("ix_prospects_status_created", "status", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<Prospect id={self.id} email={self.email} status={self.status}>"
