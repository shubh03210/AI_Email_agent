"""
Meeting Repository
───────────────────
Async CRUD for the Meeting model.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.meeting import Meeting


async def get_by_id(db: AsyncSession, meeting_id: int) -> Optional[Meeting]:
    result = await db.execute(
        select(Meeting).where(Meeting.id == meeting_id)
    )
    return result.scalar_one_or_none()


async def get_by_thread(db: AsyncSession, thread_id: int) -> Optional[Meeting]:
    result = await db.execute(
        select(Meeting).where(Meeting.thread_id == thread_id)
    )
    return result.scalar_one_or_none()


async def list_meetings(
    db: AsyncSession,
    *,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> Tuple[List[Meeting], int]:
    query = select(Meeting)
    if status:
        query = query.where(Meeting.status == status)

    count_q = select(func.count()).select_from(query.subquery())
    total: int = (await db.execute(count_q)).scalar_one()

    offset = (page - 1) * page_size
    query = (
        query
        .order_by(Meeting.scheduled_at.desc().nulls_last())
        .offset(offset)
        .limit(page_size)
    )
    items = list((await db.execute(query)).scalars().all())
    return items, total
