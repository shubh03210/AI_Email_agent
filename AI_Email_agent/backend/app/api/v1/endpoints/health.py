"""
Health Endpoints
─────────────────
Public endpoints for liveness / readiness checking and operator monitoring.

Routes (all public — no auth required):

  GET /health/live   — Kubernetes-style liveness probe.
                        Returns 200 immediately if the process is up.
                        No external calls — safe to hit every second.

  GET /health/ready  — Kubernetes-style readiness probe.
                        Returns 200 only when DB + Redis are reachable.
                        Orchestrators use this to control traffic routing.

  GET /health/       — Legacy combined check (DB + Redis). Kept for backward
                        compatibility with existing docker-compose healthchecks.

  GET /health/worker — Detailed worker health: queue depths, DLQ size,
                        Celery worker count, Redis info.

  GET /health/worker/dlq — Paginated DLQ entry listing (newest first).

Phase 6 improvements:
  - Real Redis PING replaces the previous TCP socket check.
  - Queue depths for all three queues (default, agent, gmail).
  - DLQ size and latest entry for operator visibility.
  - Celery worker ping (0.5 s timeout) with active task counts.

Phase 9 additions:
  - /health/live  — instant liveness probe (no I/O).
  - /health/ready — readiness probe (checks DB + Redis connectivity).
  - Dockerfile HEALTHCHECK updated to use /health/live.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Query, status
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.logging import logger
from app.db.session import check_db_connection

# Public router — no auth required (liveness/readiness probes, legacy check)
router = APIRouter()

# Ops router — requires authentication (exposes internal infra details)
# Mounted with _auth dependency in api.py so operators/admins only.
ops_router = APIRouter()


# ── Redis helpers (synchronous — called via run_in_executor) ──────────────────

def _redis_ping() -> dict[str, Any]:
    """
    Execute a real Redis PING and collect basic metrics.

    Returns a dict with:
      - connected (bool)
      - latency_ms (int | None)
      - used_memory_mb (float | None)
      - version (str | None)
    """
    import time
    try:
        import redis as redis_lib
        client = redis_lib.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        t0 = time.perf_counter()
        client.ping()
        latency_ms = int((time.perf_counter() - t0) * 1000)

        try:
            info = client.info("memory")
            used_mb = round(info.get("used_memory", 0) / 1_048_576, 2)
        except Exception:
            used_mb = None

        try:
            srv = client.info("server")
            version = srv.get("redis_version")
        except Exception:
            version = None

        return {
            "connected":      True,
            "latency_ms":     latency_ms,
            "used_memory_mb": used_mb,
            "version":        version,
        }
    except Exception as exc:
        logger.warning(f"[health] Redis PING failed: {exc}")
        return {"connected": False, "latency_ms": None, "used_memory_mb": None, "version": None}


def _queue_depths() -> dict[str, int]:
    """
    Return pending task counts for all three Celery queues.

    In Celery's Redis transport, each queue is stored as a Redis list
    whose key equals the queue name.  LLEN returns the current depth.
    """
    from app.workers.celery_app import queue_depth
    return {
        "default": queue_depth("default"),
        "agent":   queue_depth("agent"),
        "gmail":   queue_depth("gmail"),
    }


def _dlq_info() -> dict[str, Any]:
    """Return DLQ size and (optionally) the most recent entry."""
    from app.workers.celery_app import dlq_length, dlq_read
    size = dlq_length()
    latest = dlq_read(offset=0, limit=1)
    return {
        "size":   size,
        "latest": latest[0] if latest else None,
    }


def _celery_worker_info() -> dict[str, Any]:
    """
    Ping live Celery workers with a 0.5 s timeout.

    Returns worker names and active task counts.  Safe to call from the
    health endpoint — always returns a valid dict even if no workers
    respond.
    """
    try:
        from app.workers.celery_app import celery_app
        inspector = celery_app.control.inspect(timeout=0.5)
        ping_result = inspector.ping() or {}
        active_result = inspector.active() or {}

        workers = list(ping_result.keys())
        active_counts = {w: len(tasks) for w, tasks in active_result.items()}
        total_active = sum(active_counts.values())

        return {
            "worker_count":  len(workers),
            "workers":       workers,
            "active_tasks":  total_active,
            "active_per_worker": active_counts,
        }
    except Exception as exc:
        logger.warning(f"[health] Celery worker ping failed: {exc}")
        return {
            "worker_count": 0,
            "workers": [],
            "active_tasks": 0,
            "active_per_worker": {},
            "error": str(exc),
        }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get(
    "/live",
    summary="Liveness probe — is the process up?",
    response_description="Always 200 while the process is running",
)
async def health_live() -> JSONResponse:
    """
    Kubernetes-style **liveness** probe.

    Returns 200 immediately with no database or Redis calls.
    Container orchestrators (and the Dockerfile HEALTHCHECK) should poll
    this endpoint.  If it fails, the container should be restarted.
    """
    return JSONResponse(
        content={"status": "ok", "version": settings.VERSION},
        status_code=status.HTTP_200_OK,
    )


@router.get(
    "/ready",
    summary="Readiness probe — are DB and Redis reachable?",
    response_description="200 when ready to serve traffic, 503 when degraded",
)
async def health_ready() -> JSONResponse:
    """
    Kubernetes-style **readiness** probe.

    Checks:
      - PostgreSQL connectivity (SELECT 1, 5 s timeout)
      - Redis PING (2 s timeout)

    Returns 200 when both services are reachable; 503 otherwise.
    Container orchestrators use this to decide whether to route traffic to
    this instance.  When 503, the container stays running but receives no
    requests until it recovers.
    """
    loop = asyncio.get_event_loop()
    db_ok, redis_info = await asyncio.gather(
        check_db_connection(),
        loop.run_in_executor(None, _redis_ping),
    )
    redis_ok: bool = redis_info["connected"]
    all_ok = db_ok and redis_ok

    payload = {
        "status":  "ready" if all_ok else "not_ready",
        "version": settings.VERSION,
        "db":      "connected" if db_ok else "unreachable",
        "redis":   "connected" if redis_ok else "unreachable",
    }
    code = status.HTTP_200_OK if all_ok else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(content=payload, status_code=code)


@router.get(
    "/",
    summary="Basic liveness check (legacy — prefer /live or /ready)",
    response_description="DB and Redis reachability",
)
async def health_check() -> JSONResponse:
    """
    Lightweight liveness probe.

    Checks:
      - PostgreSQL connectivity (SELECT 1, 5 s timeout)
      - Redis PING (2 s timeout)

    Returns 200 when both are reachable; 503 otherwise.
    """
    loop = asyncio.get_event_loop()
    db_ok, redis_info = await asyncio.gather(
        check_db_connection(),
        loop.run_in_executor(None, _redis_ping),
    )
    redis_ok: bool = redis_info["connected"]

    all_ok = db_ok and redis_ok
    payload = {
        "status":  "ok" if all_ok else "degraded",
        "version": settings.VERSION,
        "db":      "connected" if db_ok else "unreachable",
        "redis":   "connected" if redis_ok else "unreachable",
    }
    code = status.HTTP_200_OK if all_ok else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(content=payload, status_code=code)


@ops_router.get(
    "/worker",
    summary="Worker health — queue depths, DLQ, Celery workers  [operator+]",
    response_description="Detailed infrastructure status for operator monitoring",
)
async def worker_health() -> JSONResponse:
    """
    Detailed infrastructure health for operator dashboards and alerting.

    Reports:
      - Redis PING + memory usage
      - Queue depths for all three queues (default, agent, gmail)
      - Dead-letter queue (DLQ) size and most-recent entry
      - Celery worker count and per-worker active task counts

    Always returns 200 with a ``degraded`` / ``ok`` status field so that
    monitoring tools can read the payload even when services are down.
    """
    loop = asyncio.get_event_loop()
    db_ok, redis_info, depths, dlq_data, worker_data = await asyncio.gather(
        check_db_connection(),
        loop.run_in_executor(None, _redis_ping),
        loop.run_in_executor(None, _queue_depths),
        loop.run_in_executor(None, _dlq_info),
        loop.run_in_executor(None, _celery_worker_info),
    )

    redis_ok     = redis_info["connected"]
    workers_ok   = worker_data["worker_count"] > 0
    all_ok       = db_ok and redis_ok

    payload: dict[str, Any] = {
        "status":  "ok" if all_ok else "degraded",
        "version": settings.VERSION,
        "db":      "connected" if db_ok else "unreachable",
        "redis": {
            "connected":      redis_info["connected"],
            "latency_ms":     redis_info["latency_ms"],
            "used_memory_mb": redis_info["used_memory_mb"],
            "version":        redis_info["version"],
        },
        "queues": depths,
        "dlq": {
            "size":   dlq_data["size"],
            "latest": dlq_data["latest"],
        },
        "celery": {
            "workers_reachable": workers_ok,
            **worker_data,
        },
    }

    return JSONResponse(content=payload, status_code=status.HTTP_200_OK)


@ops_router.get(
    "/worker/dlq",
    summary="Dead-letter queue entries  [operator+]",
    response_description="Paginated list of permanently-failed tasks",
)
async def worker_dlq(
    offset: int = Query(default=0, ge=0, description="Number of entries to skip"),
    limit:  int = Query(default=20, ge=1, le=100, description="Max entries to return"),
) -> JSONResponse:
    """
    Paginated view of the dead-letter queue.

    Entries are ordered newest-first (LPUSH order).  Use ``offset`` and
    ``limit`` for pagination.

    Each entry contains:
      - task_id, task_name, queue
      - args, kwargs (may be truncated for large payloads)
      - exception_type, exception_message
      - retry_count, failed_at
    """
    loop = asyncio.get_event_loop()
    from app.workers.celery_app import dlq_length, dlq_read

    entries, total = await asyncio.gather(
        loop.run_in_executor(None, lambda: dlq_read(offset=offset, limit=limit)),
        loop.run_in_executor(None, dlq_length),
    )

    return JSONResponse(
        content={
            "total":   total,
            "offset":  offset,
            "limit":   limit,
            "entries": entries,
        },
        status_code=status.HTTP_200_OK,
    )
