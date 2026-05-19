from fastapi import APIRouter

from app.api.v1.endpoints import (
    health,
    prospects,
    threads,
    meetings,
    config,
    agent,
    logs,
)

api_router = APIRouter()

api_router.include_router(health.router,     prefix="/health",     tags=["Health"])
api_router.include_router(prospects.router,  prefix="/prospects",  tags=["Prospects"])
api_router.include_router(threads.router,    prefix="/threads",    tags=["Threads"])
api_router.include_router(meetings.router,   prefix="/meetings",   tags=["Meetings"])
api_router.include_router(config.router,     prefix="/config",     tags=["Config"])
api_router.include_router(agent.router,      prefix="/agent",      tags=["Agent"])
api_router.include_router(logs.router,       prefix="/logs",       tags=["Logs"])
