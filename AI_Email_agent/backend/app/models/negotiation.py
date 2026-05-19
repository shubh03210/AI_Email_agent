import enum
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, Float, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.email_thread import EmailThread


class NegotiationStatus(str, enum.Enum):
    ACTIVE = "active"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    WALKAWAY = "walkaway"
    PENDING = "pending"


class Negotiation(Base):
    __tablename__ = "negotiations"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    thread_id: Mapped[int] = mapped_column(
        ForeignKey("email_threads.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    max_budget: Mapped[float] = mapped_column(Float, nullable=False)
    current_offer: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=NegotiationStatus.PENDING.value,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    thread: Mapped["EmailThread"] = relationship(
        "EmailThread",
        back_populates="negotiation",
    )

    def __repr__(self) -> str:
        return (
            f"<Negotiation id={self.id} offer={self.current_offer} "
            f"max={self.max_budget} status={self.status}>"
        )
