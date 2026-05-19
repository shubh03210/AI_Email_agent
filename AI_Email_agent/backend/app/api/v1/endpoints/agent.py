from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.core.logging import logger

router = APIRouter()


class AgentStartRequest(BaseModel):
    prospect_id: int
    thread_id: str | None = None


class AgentStartResponse(BaseModel):
    task_id: str
    status: str
    message: str


class AgentPollResponse(BaseModel):
    task_id: str
    status: str
    result: dict | None = None


@router.post(
    "/start",
    response_model=AgentStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start the agent for a given prospect",
)
async def start_agent(payload: AgentStartRequest):
    """
    Enqueues an agent run for the specified prospect.
    Returns a Celery task ID that can be used to poll status.
    """
    try:
        from app.workers.tasks import run_agent_task

        task = run_agent_task.delay(
            prospect_id=payload.prospect_id,
            thread_id=payload.thread_id,
        )
        logger.info(f"Agent task enqueued: {task.id} for prospect {payload.prospect_id}")
        return AgentStartResponse(
            task_id=task.id,
            status="queued",
            message=f"Agent started for prospect {payload.prospect_id}",
        )
    except Exception as exc:
        logger.error(f"Failed to start agent: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to enqueue agent task.",
        )


@router.get(
    "/poll/{task_id}",
    response_model=AgentPollResponse,
    summary="Poll agent task status",
)
async def poll_agent(task_id: str):
    """
    Returns the current status and result of a running or completed agent task.
    """
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
