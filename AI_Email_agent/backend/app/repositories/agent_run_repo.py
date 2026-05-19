"""
AgentRun Repository
────────────────────
Async read operations for AgentRun logs.
AgentRun rows are written exclusively by the agent (memory_service).
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_run import AgentRun


async def get_by_id(db: AsyncSession, run_id: int) -> Optional[AgentRun]:
    result = await db.execute(
        select(AgentRun).where(AgentRun.id == run_id)
    )
    return result.scalar_one_or_none()


async def list_runs(
    db: AsyncSession,
    *,
    thread_id: Optional[int] = None,
    node_name: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
) -> Tuple[List[AgentRun], int]:
    """
    Return paginated agent run logs with optional filters.

    Returns:
        (items, total_count)
    """
    query = select(AgentRun)

    if thread_id is not None:
        query = query.where(AgentRun.thread_id == thread_id)
    if node_name:
        query = query.where(AgentRun.node_name == node_name)
    if status:
        query = query.where(AgentRun.status == status)

    count_q = select(func.count()).select_from(query.subquery())
    total: int = (await db.execute(count_q)).scalar_one()

    offset = (page - 1) * page_size
    query = (
        query
        .order_by(AgentRun.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    items = list((await db.execute(query)).scalars().all())
    return items, total
