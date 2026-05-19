"""
AgentConfig Repository
───────────────────────
There is only one active config at a time.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_config import AgentConfig
from app.schemas.config import AgentConfigCreate, AgentConfigUpdate


async def get_active(db: AsyncSession) -> Optional[AgentConfig]:
    """Return the single active AgentConfig, or None if none exists."""
    result = await db.execute(
        select(AgentConfig)
        .where(AgentConfig.is_active.is_(True))
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_by_id(db: AsyncSession, config_id: int) -> Optional[AgentConfig]:
    result = await db.execute(
        select(AgentConfig).where(AgentConfig.id == config_id)
    )
    return result.scalar_one_or_none()


async def create(db: AsyncSession, payload: AgentConfigCreate) -> AgentConfig:
    config = AgentConfig(**payload.model_dump())
    db.add(config)
    await db.flush()
    await db.refresh(config)
    return config


async def update(
    db: AsyncSession,
    config: AgentConfig,
    payload: AgentConfigUpdate,
) -> AgentConfig:
    data = payload.model_dump(exclude_none=True)
    for field, value in data.items():
        setattr(config, field, value)
    db.add(config)
    await db.flush()
    await db.refresh(config)
    return config


async def get_or_create_default(db: AsyncSession) -> AgentConfig:
    """
    Return the active config. If none exists, create a default one.
    Called at startup / first-run to ensure config is always available.
    """
    config = await get_active(db)
    if config is None:
        config = await create(db, AgentConfigCreate())
        await db.commit()
    return config
