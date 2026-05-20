"""
User Repository
────────────────
All DB interactions for the User model.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.schemas.user import UserCreate


async def get_by_username(db: AsyncSession, username: str) -> Optional[User]:
    result = await db.execute(
        select(User).where(User.username == username, User.is_active.is_(True))
    )
    return result.scalars().first()


async def get_by_id(db: AsyncSession, user_id: int) -> Optional[User]:
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalars().first()


async def count_all(db: AsyncSession) -> int:
    result = await db.execute(select(func.count()).select_from(User))
    return result.scalar_one()


async def create(db: AsyncSession, payload: UserCreate) -> User:
    from app.core.security import hash_password

    user = User(
        username=payload.username,
        hashed_password=hash_password(payload.password),
        role=payload.role,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


async def record_login(db: AsyncSession, user: User) -> None:
    """Stamp last_login_at without a full model refresh."""
    user.last_login_at = datetime.now(timezone.utc)
    db.add(user)
