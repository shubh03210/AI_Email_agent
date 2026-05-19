from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class MeetingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    thread_id: int
    google_event_id: Optional[str]
    scheduled_at: Optional[datetime]
    status: str
    reschedule_count: int


class MeetingListResponse(BaseModel):
    items: List[MeetingRead]
    total: int
    page: int
    page_size: int
