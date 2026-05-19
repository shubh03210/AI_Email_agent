"""
Prospect Repository
────────────────────
Async CRUD operations for the Prospect model.
All methods accept an AsyncSession injected via FastAPI dependency.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.prospect import Prospect
from app.schemas.prospect import ProspectCreate, ProspectUpdate


async def get_by_id(db: AsyncSession, prospect_id: int) -> Optional[Prospect]:
    result = await db.execute(
        select(Prospect).where(Prospect.id == prospect_id)
    )
    return result.scalar_one_or_none()


async def get_by_email(db: AsyncSession, email: str) -> Optional[Prospect]:
    result = await db.execute(
        select(Prospect).where(Prospect.email == email)
    )
    return result.scalar_one_or_none()


async def list_prospects(
    db: AsyncSession,
    *,
    status: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> Tuple[List[Prospect], int]:
    """
    Return a paginated list of prospects with optional filters.

    Args:
        status:    Filter by status string (exact match).
        search:    Case-insensitive search on name or email.
        page:      1-based page number.
        page_size: Number of items per page.

    Returns:
        (items, total_count)
    """
    query = select(Prospect)

    if status:
        query = query.where(Prospect.status == status)
    if search:
        pattern = f"%{search}%"
        query = query.where(
            or_(
                Prospect.name.ilike(pattern),
                Prospect.email.ilike(pattern),
            )
        )

    # Total count
    count_q = select(func.count()).select_from(query.subquery())
    total: int = (await db.execute(count_q)).scalar_one()

    # Paginated results
    offset = (page - 1) * page_size
    query = (
        query
        .order_by(Prospect.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    items = list((await db.execute(query)).scalars().all())
    return items, total


async def create(db: AsyncSession, payload: ProspectCreate) -> Prospect:
    prospect = Prospect(
        name=payload.name,
        email=payload.email,
        timezone=payload.timezone,
    )
    db.add(prospect)
    await db.flush()
    await db.refresh(prospect)
    return prospect


async def update(
    db: AsyncSession,
    prospect: Prospect,
    payload: ProspectUpdate,
) -> Prospect:
    data = payload.model_dump(exclude_none=True)
    for field, value in data.items():
        setattr(prospect, field, value)
    db.add(prospect)
    await db.flush()
    await db.refresh(prospect)
    return prospect


async def delete(db: AsyncSession, prospect: Prospect) -> None:
    await db.delete(prospect)
    await db.flush()
