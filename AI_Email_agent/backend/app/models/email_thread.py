import enum
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.prospect import Prospect
    from app.models.email_message import EmailMessage
    from app.models.negotiation import Negotiation
    from app.models.meeting import Meeting
    from app.models.agent_run import AgentRun


class ThreadStatus(str, enum.Enum):
    PENDING = "pending"
    ACTIVE = "active"
    WAITING = "waiting"
    CLOSED = "closed"


class EmailThread(Base, TimestampMixin):
    __tablename__ = "email_threads"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    gmail_thread_id: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True, index=True
    )
    prospect_id: Mapped[int] = mapped_column(
        ForeignKey("prospects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    subject: Mapped[str] = mapped_column(String(998), nullable=False, default="(No Subject)")
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=ThreadStatus.PENDING.value,
        index=True,
    )

    prospect: Mapped["Prospect"] = relationship(
        "Prospect",
        back_populates="threads",
    )
    messages: Mapped[List["EmailMessage"]] = relationship(
        "EmailMessage",
        back_populates="thread",
        cascade="all, delete-orphan",
        order_by="EmailMessage.timestamp",
        lazy="selectin",
    )
    negotiation: Mapped[Optional["Negotiation"]] = relationship(
        "Negotiation",
        back_populates="thread",
        cascade="all, delete-orphan",
        uselist=False,
        lazy="selectin",
    )
    meeting: Mapped[Optional["Meeting"]] = relationship(
        "Meeting",
        back_populates="thread",
        cascade="all, delete-orphan",
        uselist=False,
        lazy="selectin",
    )
    agent_runs: Mapped[List["AgentRun"]] = relationship(
        "AgentRun",
        back_populates="thread",
        cascade="all, delete-orphan",
        order_by="AgentRun.created_at",
        lazy="selectin",
    )

    __table_args__ = (
        Index("ix_email_threads_prospect_status", "prospect_id", "status"),
    )

    def __repr__(self) -> str:
        return f"<EmailThread id={self.id} gmail={self.gmail_thread_id} status={self.status}>"
