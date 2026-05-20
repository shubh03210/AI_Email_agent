import enum
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.email_thread import EmailThread


class MessageIntent(str, enum.Enum):
    INTERESTED = "interested"
    CURIOUS = "curious"
    NEGOTIATING = "negotiating"
    DECLINED = "declined"
    AMBIGUOUS = "ambiguous"
    RESCHEDULE = "reschedule"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class EmailMessage(Base):
    __tablename__ = "email_messages"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    thread_id: Mapped[int] = mapped_column(
        ForeignKey("email_threads.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sender: Mapped[str] = mapped_column(String(320), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Dedicated column for the Gmail API message ID (e.g. "17f9e2f3d8c4a1b2").
    # Nullable because agent-sent outbound messages may be set asynchronously
    # after the Gmail send completes.  A partial unique index (WHERE NOT NULL)
    # prevents duplicate ingest of the same Gmail message.
    gmail_message_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, index=True
    )
    raw_payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    intent: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    thread: Mapped["EmailThread"] = relationship(
        "EmailThread",
        back_populates="messages",
    )

    __table_args__ = (
        Index("ix_email_messages_thread_timestamp", "thread_id", "timestamp"),
        # Partial unique index: only one row per Gmail message ID when non-NULL.
        # This prevents duplicate ingest even if the application-level check
        # is bypassed (e.g. race between two Celery workers).
        Index(
            "uix_email_messages_gmail_id",
            "gmail_message_id",
            unique=True,
            postgresql_where=("gmail_message_id IS NOT NULL"),
        ),
    )

    def __repr__(self) -> str:
        return f"<EmailMessage id={self.id} sender={self.sender} intent={self.intent}>"
