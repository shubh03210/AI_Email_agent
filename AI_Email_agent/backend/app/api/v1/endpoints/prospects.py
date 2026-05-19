"""
Prospects API
──────────────
CRUD endpoints for managing outreach prospects.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import get_db
from app.core.logging import logger
from app.repositories import prospect_repo
from app.schemas.prospect import (
    ProspectCreate,
    ProspectListResponse,
    ProspectRead,
    ProspectUpdate,
)

router = APIRouter()


@router.get(
    "/",
    response_model=ProspectListResponse,
    summary="List prospects",
)
async def list_prospects(
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by status"),
    search: Optional[str] = Query(None, description="Search by name or email"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> ProspectListResponse:
    items, total = await prospect_repo.list_prospects(
        db, status=status_filter, search=search, page=page, page_size=page_size
    )
    return ProspectListResponse(
        items=[ProspectRead.model_validate(p) for p in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/",
    response_model=ProspectRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a prospect",
)
async def create_prospect(
    payload: ProspectCreate,
    db: AsyncSession = Depends(get_db),
) -> ProspectRead:
    existing = await prospect_repo.get_by_email(db, payload.email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Prospect with email '{payload.email}' already exists.",
        )
    prospect = await prospect_repo.create(db, payload)
    logger.info(f"Prospect created | id={prospect.id} email={prospect.email}")
    return ProspectRead.model_validate(prospect)


@router.get(
    "/{prospect_id}",
    response_model=ProspectRead,
    summary="Get a prospect by ID",
)
async def get_prospect(
    prospect_id: int,
    db: AsyncSession = Depends(get_db),
) -> ProspectRead:
    prospect = await prospect_repo.get_by_id(db, prospect_id)
    if not prospect:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Prospect {prospect_id} not found.",
        )
    return ProspectRead.model_validate(prospect)


@router.put(
    "/{prospect_id}",
    response_model=ProspectRead,
    summary="Update a prospect",
)
async def update_prospect(
    prospect_id: int,
    payload: ProspectUpdate,
    db: AsyncSession = Depends(get_db),
) -> ProspectRead:
    prospect = await prospect_repo.get_by_id(db, prospect_id)
    if not prospect:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Prospect {prospect_id} not found.",
        )
    # If email is being changed, check uniqueness
    if payload.email and payload.email != prospect.email:
        existing = await prospect_repo.get_by_email(db, payload.email)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Email '{payload.email}' is already used by another prospect.",
            )
    prospect = await prospect_repo.update(db, prospect, payload)
    logger.info(f"Prospect updated | id={prospect.id}")
    return ProspectRead.model_validate(prospect)


@router.delete(
    "/{prospect_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a prospect",
)
async def delete_prospect(
    prospect_id: int,
    db: AsyncSession = Depends(get_db),
) -> Response:
    prospect = await prospect_repo.get_by_id(db, prospect_id)
    if not prospect:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Prospect {prospect_id} not found.",
        )
    await prospect_repo.delete(db, prospect)
    logger.info(f"Prospect deleted | id={prospect_id}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{prospect_id}/outreach",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger cold outreach for a prospect",
)
async def trigger_outreach(
    prospect_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Enqueue a Celery task to generate and send the first outreach email
    to this prospect. Returns a task ID for polling.
    """
    prospect = await prospect_repo.get_by_id(db, prospect_id)
    if not prospect:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Prospect {prospect_id} not found.",
        )
    # Try Celery first; fall back to direct async execution when Redis is unavailable
    try:
        from app.workers.tasks import send_outreach_task
        task = send_outreach_task.delay(prospect_id=prospect_id)
        logger.info(f"Outreach task enqueued | prospect_id={prospect_id} task={task.id}")
        return {
            "task_id": task.id,
            "status": "queued",
            "message": f"Outreach queued for prospect {prospect_id}",
        }
    except Exception as celery_exc:
        logger.warning(
            f"Celery unavailable ({celery_exc}) — running outreach inline for prospect {prospect_id}"
        )
        try:
            import asyncio
            from app.workers.tasks import _send_outreach
            result = asyncio.run(_send_outreach(prospect_id))
            logger.info(f"Inline outreach done | prospect={prospect_id} result={result}")
            return {
                "task_id": "inline",
                "status": "sent",
                "message": f"Outreach sent directly (no Celery). Subject: {result.get('subject', '')}",
            }
        except Exception as inline_exc:
            logger.error(f"Inline outreach also failed: {inline_exc}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Outreach failed: {inline_exc}",
            )
