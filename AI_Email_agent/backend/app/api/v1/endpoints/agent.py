from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.core.logging import logger

router = APIRouter()


class AgentStartRequest(BaseModel):
    prospect_id: int
    thread_id: Optional[int] = None


class AgentStartResponse(BaseModel):
    task_id: str
    status: str
    message: str


class AgentPollResponse(BaseModel):
    task_id: str
    status: str
    result: Optional[dict] = None


@router.post(
    "/start",
    response_model=AgentStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start the agent for a given prospect / thread",
)
async def start_agent(payload: AgentStartRequest) -> AgentStartResponse:
    """
    Enqueues an agent run.
    If thread_id is supplied the agent runs against that thread directly.
    Otherwise it enqueues an outreach task for the prospect.
    """
    try:
        if payload.thread_id:
            from app.workers.tasks import run_agent_task
            task = run_agent_task.delay(thread_id=payload.thread_id)
            msg = f"Agent run queued for thread {payload.thread_id}"
        else:
            from app.workers.tasks import send_outreach_task
            task = send_outreach_task.delay(prospect_id=payload.prospect_id)
            msg = f"Outreach queued for prospect {payload.prospect_id}"

        logger.info(f"Task enqueued: {task.id}")
        return AgentStartResponse(task_id=task.id, status="queued", message=msg)

    except Exception as exc:
        logger.error(f"Failed to enqueue agent task: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to enqueue agent task.",
        )


@router.get(
    "/poll/{task_id}",
    response_model=AgentPollResponse,
    summary="Poll agent task status",
)
async def poll_agent(task_id: str) -> AgentPollResponse:
    """Returns the current status and result of a running or completed agent task."""
    try:
        from app.workers.celery_app import celery_app
        from celery.result import AsyncResult

        result: AsyncResult = celery_app.AsyncResult(task_id)
        return AgentPollResponse(
            task_id=task_id,
            status=result.status,
            result=result.result if result.ready() else None,
        )
    except Exception as exc:
        logger.error(f"Failed to poll task {task_id}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch task status.",
        )
