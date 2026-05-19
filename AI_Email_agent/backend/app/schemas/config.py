from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class AgentConfigRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    gig_description: str
    budget_ceiling: float
    tone: str
    timezone: str
    working_hours_start: int
    working_hours_end: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class AgentConfigUpdate(BaseModel):
    gig_description: Optional[str] = None
    budget_ceiling: Optional[float] = Field(None, gt=0)
    tone: Optional[str] = Field(None, max_length=64)
    timezone: Optional[str] = Field(None, max_length=64)
    working_hours_start: Optional[int] = Field(None, ge=0, le=23)
    working_hours_end: Optional[int] = Field(None, ge=1, le=24)
    is_active: Optional[bool] = None


class AgentConfigCreate(BaseModel):
    gig_description: str = ""
    budget_ceiling: float = Field(default=5000.0, gt=0)
    tone: str = Field(default="professional", max_length=64)
    timezone: str = Field(default="UTC", max_length=64)
    working_hours_start: int = Field(default=9, ge=0, le=23)
    working_hours_end: int = Field(default=18, ge=1, le=24)
    is_active: bool = True
