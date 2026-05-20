from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AgentConfig(Base):
    __tablename__ = "agent_configs"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    gig_description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    budget_ceiling: Mapped[float] = mapped_column(Float, nullable=False, default=5000.0)
    tone: Mapped[str] = mapped_column(String(64), nullable=False, default="professional")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    working_hours_start: Mapped[int] = mapped_column(Integer, nullable=False, default=9)
    working_hours_end: Mapped[int] = mapped_column(Integer, nullable=False, default=18)
    follow_up_days: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    max_follow_ups: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<AgentConfig id={self.id} tone={self.tone} "
            f"budget_ceiling={self.budget_ceiling} active={self.is_active}>"
        )
