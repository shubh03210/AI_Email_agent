import enum
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.email_thread import EmailThread


class MeetingStatus(str, enum.Enum):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    RESCHEDULED = "rescheduled"
    COMPLETED = "completed"


class Meeting(Base):
    __tablename__ = "meetings"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    thread_id: Mapped[int] = mapped_column(
        ForeignKey("email_threads.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    google_event_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, unique=True
    )
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=MeetingStatus.PROPOSED.value,
        index=True,
    )
    reschedule_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )

    # ── Human escalation ──────────────────────────────────────────────────────
    # calendar_failure_count: incremented every time a calendar API operation
    # fails for this meeting (create, reschedule, or cancel).
    # needs_human_review: set True when calendar_failure_count reaches the
    # configured threshold (CALENDAR_MAX_FAILURES).  When True the agent nodes
    # skip automation and produce a human-escalation reply.
    calendar_failure_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    needs_human_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True
    )

    thread: Mapped["EmailThread"] = relationship(
        "EmailThread",
        back_populates="meeting",
    )

    def __repr__(self) -> str:
        return (
            f"<Meeting id={self.id} scheduled_at={self.scheduled_at} "
            f"status={self.status} reschedules={self.reschedule_count} "
            f"needs_review={self.needs_human_review}>"
        )
