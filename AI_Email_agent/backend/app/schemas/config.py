from __future__ import annotations

import json
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ── Tone preset options ───────────────────────────────────────────────────────
TONE_OPTIONS = ["formal", "friendly", "startup", "executive", "professional", "casual"]

_DEFAULT_CADENCE = "[1, 3, 7]"

_DEFAULT_CONFIRMATION_TEMPLATE = (
    "Hi {prospect_name},\n\n"
    "Just confirming our meeting on {meeting_date} at {meeting_time} ({timezone}).\n\n"
    "I'll send over a calendar invite shortly. Looking forward to connecting!\n\n"
    "Best regards,\n"
    "{recruiter_name}\n"
    "{recruiter_title}"
)


class AgentConfigRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    gig_description: str
    budget_ceiling: float
    tone: str
    timezone: str
    working_hours_start: int
    working_hours_end: int
    follow_up_days: int
    max_follow_ups: int
    is_active: bool
    # Phase 8 fields
    follow_up_cadence: str
    recruiter_name: str
    recruiter_title: str
    recruiter_signature: str
    meeting_confirmation_template: str
    created_at: datetime
    updated_at: datetime

    @property
    def follow_up_cadence_list(self) -> List[int]:
        """Parsed cadence as a list of day-offset integers."""
        try:
            return json.loads(self.follow_up_cadence)
        except Exception:
            return [self.follow_up_days]


class AgentConfigUpdate(BaseModel):
    gig_description: Optional[str] = None
    budget_ceiling: Optional[float] = Field(None, gt=0)
    tone: Optional[str] = Field(None, max_length=64)
    timezone: Optional[str] = Field(None, max_length=64)
    working_hours_start: Optional[int] = Field(None, ge=0, le=23)
    working_hours_end: Optional[int] = Field(None, ge=1, le=24)
    follow_up_days: Optional[int] = Field(None, ge=1, le=30)
    max_follow_ups: Optional[int] = Field(None, ge=0, le=10)
    is_active: Optional[bool] = None
    # Phase 8 fields
    follow_up_cadence: Optional[str] = None
    recruiter_name: Optional[str] = Field(None, max_length=128)
    recruiter_title: Optional[str] = Field(None, max_length=128)
    recruiter_signature: Optional[str] = None
    meeting_confirmation_template: Optional[str] = None

    @field_validator("follow_up_cadence")
    @classmethod
    def validate_cadence(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        try:
            parsed = json.loads(v)
            if not isinstance(parsed, list) or not all(isinstance(d, int) and d > 0 for d in parsed):
                raise ValueError
        except Exception:
            raise ValueError(
                "follow_up_cadence must be a JSON array of positive integers, e.g. '[1, 3, 7]'"
            )
        return v


class AgentConfigCreate(BaseModel):
    gig_description: str = ""
    budget_ceiling: float = Field(default=5000.0, gt=0)
    tone: str = Field(default="formal", max_length=64)
    timezone: str = Field(default="UTC", max_length=64)
    working_hours_start: int = Field(default=9, ge=0, le=23)
    working_hours_end: int = Field(default=18, ge=1, le=24)
    follow_up_days: int = Field(default=3, ge=1, le=30)
    max_follow_ups: int = Field(default=3, ge=0, le=10)
    is_active: bool = True
    # Phase 8 fields
    follow_up_cadence: str = _DEFAULT_CADENCE
    recruiter_name: str = Field(default="Alex", max_length=128)
    recruiter_title: str = Field(default="HR Recruiter", max_length=128)
    recruiter_signature: str = ""
    meeting_confirmation_template: str = _DEFAULT_CONFIRMATION_TEMPLATE
