from typing import List, Optional
from fastapi import APIRouter, Query, status
from pydantic import BaseModel
from datetime import datetime

router = APIRouter()


class AgentRunLog(BaseModel):
    id: int
    thread_id: str
    node_name: str
    status: str
    latency_ms: Optional[int]
    created_at: datetime

    model_config = {"from_attributes": True}


@router.get(
    "/",
    response_model=List[AgentRunLog],
    summary="List agent execution logs",
)
async def list_logs(
    thread_id: Optional[str] = Query(None, description="Filter by thread ID"),
    node_name: Optional[str] = Query(None, description="Filter by node name"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """
    Returns paginated agent run logs.
    Filterable by thread_id and node_name.
    Full implementation wired in Phase 2 after repositories are built.
    """
    return []


@router.get(
    "/{log_id}",
    response_model=AgentRunLog,
    summary="Get a specific agent run log",
)
async def get_log(log_id: int):
    """
    Returns the full input/output payload of a specific agent run.
    """
    from fastapi import HTTPException
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Log not found")
