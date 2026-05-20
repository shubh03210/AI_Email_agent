"""
Threads API
────────────
Endpoints for viewing email threads, their messages, and triggering agent runs.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import get_db, require_operator_or_admin
from app.core.logging import logger
from app.repositories import thread_repo, agent_run_repo
from app.schemas.thread import (
    ThreadDetail,
    ThreadListResponse,
    ThreadRead,
    MessageListResponse,
    MessageRead,
)
from app.schemas.agent_run import AgentRunListResponse, AgentRunRead

router = APIRouter()


@router.get(
    "/",
    response_model=ThreadListResponse,
    summary="List email threads",
)
async def list_threads(
    prospect_id: Optional[int] = Query(None, description="Filter by prospect ID"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> ThreadListResponse:
    items, total = await thread_repo.list_threads(
        db,
        prospect_id=prospect_id,
        status=status_filter,
        page=page,
        page_size=page_size,
    )
    return ThreadListResponse(
        items=[ThreadRead.model_validate(t) for t in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{thread_id}",
    response_model=ThreadDetail,
    summary="Get a thread with full message history",
)
async def get_thread(
    thread_id: int,
    db: AsyncSession = Depends(get_db),
) -> ThreadDetail:
    thread = await thread_repo.get_by_id(db, thread_id)
    if not thread:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {thread_id} not found.",
        )
    return ThreadDetail.model_validate(thread)


@router.get(
    "/{thread_id}/messages",
    response_model=MessageListResponse,
    summary="Get paginated messages for a thread",
)
async def list_messages(
    thread_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> MessageListResponse:
    thread = await thread_repo.get_by_id(db, thread_id)
    if not thread:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {thread_id} not found.",
        )
    messages, total = await thread_repo.list_messages(
        db, thread_id, page=page, page_size=page_size
    )
    return MessageListResponse(
        items=[MessageRead.model_validate(m) for m in messages],
        total=total,
    )


@router.post(
    "/{thread_id}/run",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger an agent run for a thread",
)
async def run_agent_for_thread(
    thread_id: int,
    db: AsyncSession = Depends(get_db),
    _: object = Depends(require_operator_or_admin),
) -> dict:
    """
    Enqueue a Celery task to run the AI agent on this thread.
    Returns a task ID that can be polled via /agent/poll/{task_id}.
    """
    thread = await thread_repo.get_by_id(db, thread_id)
    if not thread:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {thread_id} not found.",
        )
    # Try Celery first; fall back to direct async execution when Redis is unavailable
    try:
        from app.workers.tasks import run_agent_task
        task = run_agent_task.delay(thread_id=thread_id)
        logger.info(f"Agent run enqueued | thread_id={thread_id} task={task.id}")
        return {
            "task_id": task.id,
            "status": "queued",
            "message": f"Agent run queued for thread {thread_id}",
        }
    except Exception as celery_exc:
        logger.warning(
            f"Celery unavailable ({celery_exc}) — running agent inline for thread {thread_id}"
        )
        try:
            from app.agents.graph import run_agent
            final_state = await run_agent(thread_id)
            logger.info(
                f"Inline agent run done | thread={thread_id} "
                f"intent={final_state.get('intent')} reply_sent={final_state.get('reply_sent')}"
            )
            return {
                "task_id": "inline",
                "status": "completed",
                "message": (
                    f"Agent ran directly (no Celery). "
                    f"Intent: {final_state.get('intent')} | "
                    f"Reply sent: {final_state.get('reply_sent')}"
                ),
            }
        except Exception as inline_exc:
            logger.exception(f"Inline agent run failed: {inline_exc}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An internal error occurred. Please try again.",
            )


@router.get(
    "/{thread_id}/logs",
    response_model=AgentRunListResponse,
    summary="Get agent run logs for a thread",
)
async def get_thread_logs(
    thread_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> AgentRunListResponse:
    thread = await thread_repo.get_by_id(db, thread_id)
    if not thread:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thread {thread_id} not found.",
        )
    runs, total = await agent_run_repo.list_runs(
        db, thread_id=thread_id, page=page, page_size=page_size
    )
    return AgentRunListResponse(
        items=[AgentRunRead.model_validate(r) for r in runs],
        total=total,
        page=page,
        page_size=page_size,
    )
