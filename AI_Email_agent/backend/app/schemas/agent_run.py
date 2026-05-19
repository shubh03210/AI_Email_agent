from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict


class AgentRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    thread_id: int
    node_name: str
    status: str
    latency_ms: Optional[int]
    input_payload: Optional[Any]
    output_payload: Optional[Any]
    created_at: datetime


class AgentRunListResponse(BaseModel):
    items: List[AgentRunRead]
    total: int
    page: int
    page_size: int
