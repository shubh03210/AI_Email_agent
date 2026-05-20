import enum
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.email_thread import EmailThread


class RunStatus(str, enum.Enum):
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    SKIPPED = "skipped"


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    thread_id: Mapped[int] = mapped_column(
        ForeignKey("email_threads.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    node_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    input_payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    output_payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=RunStatus.RUNNING.value,
        index=True,
    )
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    thread: Mapped["EmailThread"] = relationship(
        "EmailThread",
        back_populates="agent_runs",
    )

    def __repr__(self) -> str:
        return (
            f"<AgentRun id={self.id} node={self.node_name} "
            f"status={self.status} latency={self.latency_ms}ms>"
        )
