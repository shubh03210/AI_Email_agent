"""
FastAPI Dependencies
─────────────────────
Shared dependency injectors used across all endpoints.
Import with:  from app.api.v1.deps import get_db
"""

from __future__ import annotations

from typing import AsyncGenerator

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Yield an async DB session for the duration of a request.
    Auto-commits on success, rolls back on exception.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# Type alias used in endpoint signatures
DB = AsyncSession
