import enum
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
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
    follow_up_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    last_outreach_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    # ── Agent hardening (Phase 5) ─────────────────────────────────────────
    # thread_summary: LLM-generated rolling summary of messages that fall
    #   outside the memory window.  Prepended to the windowed context so the
    #   LLM always sees a compact history, even for long-running threads.
    thread_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ambiguous_count: consecutive runs where classify_intent returned
    #   "ambiguous" (reset to 0 when any non-ambiguous intent is detected).
    #   Drives the repeated-ambiguity escalation threshold.
    ambiguous_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # agent_escalated: True when the agent cannot handle this thread
    #   autonomously (confidence too low, repeated ambiguity, API failures).
    #   When True, the agent produces a human-handoff reply and stops
    #   making routing decisions.
    agent_escalated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True
    )

    # escalation_reason: human-readable reason for the escalation decision,
    #   stored for operator review.
    escalation_reason: Mapped[Optional[str]] = mapped_column(
        String(512), nullable=True
    )

    prospect: Mapped["Prospect"] = relationship(
        "Prospect",
        back_populates="threads",
    )
    # passive_deletes=True on all child relationships: DB ON DELETE CASCADE
    # handles the actual deletions, so SQLAlchemy does not load child rows
    # into memory when a thread (or its parent prospect) is deleted.
    messages: Mapped[List["EmailMessage"]] = relationship(
        "EmailMessage",
        back_populates="thread",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="EmailMessage.timestamp",
        lazy="selectin",
    )
    negotiation: Mapped[Optional["Negotiation"]] = relationship(
        "Negotiation",
        back_populates="thread",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
        lazy="selectin",
    )
    meeting: Mapped[Optional["Meeting"]] = relationship(
        "Meeting",
        back_populates="thread",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
        lazy="selectin",
    )
    agent_runs: Mapped[List["AgentRun"]] = relationship(
        "AgentRun",
        back_populates="thread",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="AgentRun.created_at",
        lazy="selectin",
    )

    __table_args__ = (
        Index("ix_email_threads_prospect_status", "prospect_id", "status"),
    )

    def __repr__(self) -> str:
        return f"<EmailThread id={self.id} gmail={self.gmail_thread_id} status={self.status}>"
