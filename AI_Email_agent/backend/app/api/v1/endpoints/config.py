"""
Agent Config API
─────────────────
Endpoints for reading and updating the active agent configuration.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import get_db
from app.core.logging import logger
from app.repositories import config_repo
from app.schemas.config import AgentConfigCreate, AgentConfigRead, AgentConfigUpdate

router = APIRouter()


@router.get(
    "/",
    response_model=AgentConfigRead,
    summary="Get the active agent configuration",
)
async def get_config(db: AsyncSession = Depends(get_db)) -> AgentConfigRead:
    """
    Returns the current active agent config.
    Creates a default config if none exists yet.
    """
    config = await config_repo.get_or_create_default(db)
    return AgentConfigRead.model_validate(config)


@router.put(
    "/",
    response_model=AgentConfigRead,
    summary="Update the active agent configuration",
)
async def update_config(
    payload: AgentConfigUpdate,
    db: AsyncSession = Depends(get_db),
) -> AgentConfigRead:
    config = await config_repo.get_active(db)
    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active config found. POST /config/ to create one first.",
        )
    config = await config_repo.update(db, config, payload)
    logger.info(f"AgentConfig updated | id={config.id}")
    return AgentConfigRead.model_validate(config)


@router.post(
    "/",
    response_model=AgentConfigRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new agent configuration",
)
async def create_config(
    payload: AgentConfigCreate,
    db: AsyncSession = Depends(get_db),
) -> AgentConfigRead:
    """
    Creates a new config. If one already exists and is active,
    deactivates it before creating the new one.
    """
    existing = await config_repo.get_active(db)
    if existing:
        existing.is_active = False
        db.add(existing)

    config = await config_repo.create(db, payload)
    logger.info(f"AgentConfig created | id={config.id}")
    return AgentConfigRead.model_validate(config)
