"""
Thread Repository
──────────────────
Async read operations for EmailThread and EmailMessage.
Threads are created by the agent (memory_service), not the API.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.email_message import EmailMessage
from app.models.email_thread import EmailThread


async def get_by_id(db: AsyncSession, thread_id: int) -> Optional[EmailThread]:
    """Fetch a thread with messages, negotiation, and meeting eagerly loaded."""
    result = await db.execute(
        select(EmailThread)
        .where(EmailThread.id == thread_id)
        .options(
            selectinload(EmailThread.messages),
            selectinload(EmailThread.negotiation),
            selectinload(EmailThread.meeting),
        )
    )
    return result.scalar_one_or_none()


async def get_by_gmail_id(
    db: AsyncSession, gmail_thread_id: str
) -> Optional[EmailThread]:
    result = await db.execute(
        select(EmailThread).where(EmailThread.gmail_thread_id == gmail_thread_id)
    )
    return result.scalar_one_or_none()


async def list_threads(
    db: AsyncSession,
    *,
    prospect_id: Optional[int] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> Tuple[List[EmailThread], int]:
    """
    Return paginated email threads with optional filters.

    Returns:
        (items, total_count)
    """
    query = select(EmailThread)

    if prospect_id is not None:
        query = query.where(EmailThread.prospect_id == prospect_id)
    if status:
        query = query.where(EmailThread.status == status)

    count_q = select(func.count()).select_from(query.subquery())
    total: int = (await db.execute(count_q)).scalar_one()

    offset = (page - 1) * page_size
    query = (
        query
        .order_by(EmailThread.updated_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    items = list((await db.execute(query)).scalars().all())
    return items, total


async def list_messages(
    db: AsyncSession,
    thread_id: int,
    *,
    page: int = 1,
    page_size: int = 50,
) -> Tuple[List[EmailMessage], int]:
    """Return paginated messages for a thread, chronological order."""
    query = select(EmailMessage).where(EmailMessage.thread_id == thread_id)

    count_q = select(func.count()).select_from(query.subquery())
    total: int = (await db.execute(count_q)).scalar_one()

    offset = (page - 1) * page_size
    query = (
        query
        .order_by(EmailMessage.timestamp.asc())
        .offset(offset)
        .limit(page_size)
    )
    items = list((await db.execute(query)).scalars().all())
    return items, total
