"""
API Router
──────────
Assembles all versioned sub-routers under /api/v1.

Authentication model
────────────────────
  Public  :  /health  /auth      (no token required)
  Protected: all other routes    (valid Bearer JWT required)

Authorisation is enforced at the individual endpoint level via
require_admin / require_operator_or_admin dependencies — not here,
so that fine-grained per-operation control is explicit and co-located
with the endpoint code.
"""

from fastapi import APIRouter, Depends

from app.api.v1.deps import get_current_user
from app.api.v1.endpoints import (
    agent,
    auth,
    config,
    health,
    logs,
    meetings,
    metrics,
    prospects,
    threads,
)

api_router = APIRouter()

# ── Public ─────────────────────────────────────────────────────────────────────
# /health, /health/live, /health/ready — safe for orchestrators/load-balancers
api_router.include_router(health.router, prefix="/health", tags=["Health"])
api_router.include_router(auth.router,   prefix="/auth",   tags=["Auth"])

# ── Protected (authentication required; authorisation per-endpoint) ─────────────
_auth = [Depends(get_current_user)]

api_router.include_router(prospects.router, prefix="/prospects", tags=["Prospects"], dependencies=_auth)
api_router.include_router(threads.router,   prefix="/threads",   tags=["Threads"],   dependencies=_auth)
api_router.include_router(meetings.router,  prefix="/meetings",  tags=["Meetings"],  dependencies=_auth)
api_router.include_router(config.router,    prefix="/config",    tags=["Config"],    dependencies=_auth)
api_router.include_router(agent.router,     prefix="/agent",     tags=["Agent"],     dependencies=_auth)
api_router.include_router(logs.router,      prefix="/logs",      tags=["Logs"],      dependencies=_auth)
api_router.include_router(metrics.router,   prefix="/metrics",   tags=["Metrics"],   dependencies=_auth)

# /health/worker + /health/worker/dlq — expose internal infra details; auth required
api_router.include_router(health.ops_router, prefix="/health", tags=["Health"], dependencies=_auth)
