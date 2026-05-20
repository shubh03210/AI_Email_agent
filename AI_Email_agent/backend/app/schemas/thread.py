from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    thread_id: int
    sender: str
    body: str
    intent: Optional[str]
    timestamp: datetime


class NegotiationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    thread_id: int
    max_budget: float
    current_offer: Optional[float]
    status: str
    updated_at: datetime


class ThreadRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    gmail_thread_id: str
    prospect_id: int
    subject: str
    status: str
    created_at: datetime
    updated_at: datetime
    follow_up_count: int = 0
    last_outreach_at: Optional[datetime] = None


class ThreadDetail(ThreadRead):
    """Thread with full message history, negotiation, and meeting state."""
    messages: List[MessageRead] = []
    negotiation: Optional[NegotiationRead] = None


class ThreadListResponse(BaseModel):
    items: List[ThreadRead]
    total: int
    page: int
    page_size: int


class MessageListResponse(BaseModel):
    items: List[MessageRead]
    total: int
