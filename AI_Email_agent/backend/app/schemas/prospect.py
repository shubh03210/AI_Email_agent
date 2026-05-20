from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, EmailStr, ConfigDict, Field


# ── Request schemas ───────────────────────────────────────────────────────────

class ProspectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    email: EmailStr
    company: Optional[str] = Field(None, max_length=255)
    timezone: str = Field(default="UTC", max_length=64)


class ProspectUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    email: Optional[EmailStr] = None
    company: Optional[str] = Field(None, max_length=255)
    timezone: Optional[str] = Field(None, max_length=64)
    status: Optional[str] = Field(None, max_length=32)


# ── Response schemas ──────────────────────────────────────────────────────────

class ProspectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: str
    company: Optional[str]
    timezone: str
    status: str
    created_at: datetime
    updated_at: datetime


class ProspectListResponse(BaseModel):
    items: List[ProspectRead]
    total: int
    page: int
    page_size: int
