"""
Meetings API
─────────────
Endpoints for viewing and managing scheduled meetings.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import get_db
from app.core.logging import logger
from app.models.meeting import MeetingStatus
from app.repositories import meeting_repo
from app.schemas.meeting import MeetingListResponse, MeetingRead

router = APIRouter()


@router.get(
    "/",
    response_model=MeetingListResponse,
    summary="List meetings",
)
async def list_meetings(
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> MeetingListResponse:
    items, total = await meeting_repo.list_meetings(
        db, status=status_filter, page=page, page_size=page_size
    )
    return MeetingListResponse(
        items=[MeetingRead.model_validate(m) for m in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{meeting_id}",
    response_model=MeetingRead,
    summary="Get a meeting by ID",
)
async def get_meeting(
    meeting_id: int,
    db: AsyncSession = Depends(get_db),
) -> MeetingRead:
    meeting = await meeting_repo.get_by_id(db, meeting_id)
    if not meeting:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Meeting {meeting_id} not found.",
        )
    return MeetingRead.model_validate(meeting)


@router.post(
    "/{meeting_id}/cancel",
    response_model=MeetingRead,
    summary="Cancel a meeting",
)
async def cancel_meeting(
    meeting_id: int,
    db: AsyncSession = Depends(get_db),
) -> MeetingRead:
    """
    Cancel a meeting and (if possible) remove the Google Calendar event.
    """
    meeting = await meeting_repo.get_by_id(db, meeting_id)
    if not meeting:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Meeting {meeting_id} not found.",
        )
    if meeting.status == MeetingStatus.CANCELLED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Meeting is already cancelled.",
        )

    # Best-effort Google Calendar cancellation
    if meeting.google_event_id:
        try:
            from app.services.calendar_service import cancel_event
            cancel_event(meeting.google_event_id)
            logger.info(
                f"GCal event {meeting.google_event_id} cancelled | meeting={meeting_id}"
            )
        except Exception as exc:
            logger.warning(
                f"Failed to cancel GCal event {meeting.google_event_id}: {exc}"
            )

    meeting.status = MeetingStatus.CANCELLED.value
    db.add(meeting)
    logger.info(f"Meeting {meeting_id} cancelled.")
    return MeetingRead.model_validate(meeting)


@router.post(
    "/{meeting_id}/reschedule",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger rescheduling for a meeting",
)
async def reschedule_meeting(
    meeting_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Enqueue a Celery task to run the rescheduling agent flow for this meeting.
    The agent will find a new slot, cancel the old event, create a new one,
    and send a reply email to the prospect.
    """
    meeting = await meeting_repo.get_by_id(db, meeting_id)
    if not meeting:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Meeting {meeting_id} not found.",
        )
    if meeting.status == MeetingStatus.CANCELLED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot reschedule a cancelled meeting.",
        )

    try:
        from app.workers.tasks import run_agent_task
        task = run_agent_task.delay(thread_id=meeting.thread_id)
        logger.info(
            f"Reschedule task enqueued | meeting={meeting_id} "
            f"thread={meeting.thread_id} task={task.id}"
        )
        return {
            "task_id": task.id,
            "status": "queued",
            "message": f"Rescheduling queued for meeting {meeting_id}",
        }
    except Exception as exc:
        logger.error(f"Failed to enqueue reschedule task: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to enqueue reschedule task.",
        )
