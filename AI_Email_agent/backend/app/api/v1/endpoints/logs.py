"""
Agent Run Logs API
───────────────────
Paginated, filterable log viewer for agent execution records.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import get_db
from app.repositories import agent_run_repo
from app.schemas.agent_run import AgentRunListResponse, AgentRunRead

router = APIRouter()


@router.get(
    "/",
    response_model=AgentRunListResponse,
    summary="List agent execution logs",
)
async def list_logs(
    thread_id: Optional[int] = Query(None, description="Filter by thread DB ID"),
    node_name: Optional[str] = Query(None, description="Filter by node name"),
    run_status: Optional[str] = Query(None, alias="status", description="Filter by status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> AgentRunListResponse:
    runs, total = await agent_run_repo.list_runs(
        db,
        thread_id=thread_id,
        node_name=node_name,
        status=run_status,
        page=page,
        page_size=page_size,
    )
    return AgentRunListResponse(
        items=[AgentRunRead.model_validate(r) for r in runs],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{log_id}",
    response_model=AgentRunRead,
    summary="Get a specific agent run log with full payloads",
)
async def get_log(
    log_id: int,
    db: AsyncSession = Depends(get_db),
) -> AgentRunRead:
    run = await agent_run_repo.get_by_id(db, log_id)
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Log {log_id} not found.",
        )
    return AgentRunRead.model_validate(run)
