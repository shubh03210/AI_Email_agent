from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Default cadence: follow up on day 1, day 3, day 7 after last outreach
_DEFAULT_CADENCE = "[1, 3, 7]"

# Default meeting confirmation template — placeholders filled at send time
_DEFAULT_CONFIRMATION_TEMPLATE = (
    "Hi {prospect_name},\n\n"
    "Just confirming our meeting on {meeting_date} at {meeting_time} ({timezone}).\n\n"
    "I'll send over a calendar invite shortly. Looking forward to connecting!\n\n"
    "Best regards,\n"
    "{recruiter_name}\n"
    "{recruiter_title}"
)


class AgentConfig(Base):
    __tablename__ = "agent_configs"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    gig_description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    budget_ceiling: Mapped[float] = mapped_column(Float, nullable=False, default=5000.0)

    # ── Tone preset ───────────────────────────────────────────────────────────
    # Values: formal | friendly | startup | executive
    # (professional / casual retained for backward compatibility)
    tone: Mapped[str] = mapped_column(String(64), nullable=False, default="formal")

    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    working_hours_start: Mapped[int] = mapped_column(Integer, nullable=False, default=9)
    working_hours_end: Mapped[int] = mapped_column(Integer, nullable=False, default=18)

    # ── Follow-up cadence ─────────────────────────────────────────────────────
    # JSON-encoded list of day-offsets from last outreach for each step.
    # Example: "[1, 3, 7]" means follow-up 1 after 1 day, #2 after 3 days, #3 after 7.
    follow_up_cadence: Mapped[str] = mapped_column(
        Text, nullable=False, default=_DEFAULT_CADENCE
    )
    # follow_up_days kept for backward compat (used as fallback when cadence absent)
    follow_up_days: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    max_follow_ups: Mapped[int] = mapped_column(Integer, nullable=False, default=3)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)

    # ── Recruiter identity / signature ────────────────────────────────────────
    recruiter_name: Mapped[str] = mapped_column(String(128), nullable=False, default="Alex")
    recruiter_title: Mapped[str] = mapped_column(String(128), nullable=False, default="HR Recruiter")
    # Rich-text footer appended to every outbound email after LLM body
    recruiter_signature: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # ── Meeting confirmation template ─────────────────────────────────────────
    # Placeholders: {prospect_name}, {meeting_date}, {meeting_time},
    #               {timezone}, {recruiter_name}, {recruiter_title}
    meeting_confirmation_template: Mapped[str] = mapped_column(
        Text, nullable=False, default=_DEFAULT_CONFIRMATION_TEMPLATE
    )

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
