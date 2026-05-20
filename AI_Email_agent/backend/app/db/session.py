from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.logging import logger

# Supabase connection args:
#   ssl=require       — mandatory for all Supabase endpoints
#   statement_cache_size=0 — required for Transaction mode pooler (port 6543);
#                            prepared statements can't survive across connections
_connect_args = {}
if "supabase.co" in settings.DATABASE_URL:
    _connect_args = {"ssl": "require", "statement_cache_size": 0}

# NullPool: every session opens a fresh connection and closes it on exit.
# This is the safest strategy for Supabase free-tier (15-connection cap) when
# both FastAPI handlers and Celery workers (each running in their own event
# loop) share the same database.  The tiny per-request overhead is negligible
# compared to hitting the connection limit.
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    poolclass=NullPool,
    connect_args=_connect_args,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)

# Alias used by Celery tasks — same engine/pool so the name is clear at callsite
CelerySessionLocal = AsyncSessionLocal


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields an async DB session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def check_db_connection() -> bool:
    """Health check — returns True if DB is reachable within 5 seconds."""
    import asyncio
    try:
        async def _ping():
            async with engine.connect() as conn:
                await conn.execute(__import__("sqlalchemy").text("SELECT 1"))

        await asyncio.wait_for(_ping(), timeout=5.0)
        return True
    except asyncio.TimeoutError:
        logger.warning("DB health check timed out after 5s")
        return False
    except Exception as exc:
        logger.error(f"DB connection check failed: {exc}")
        return False
