from fastapi import APIRouter
from app.api.v1.endpoints import health, prospects, threads, meetings, config

api_router = APIRouter()

api_router.include_router(health.router, prefix="/health", tags=["health"])
api_router.include_router(prospects.router, prefix="/prospects", tags=["prospects"])
api_router.include_router(threads.router, prefix="/threads", tags=["threads"])
api_router.include_router(meetings.router, prefix="/meetings", tags=["meetings"])
api_router.include_router(config.router, prefix="/config", tags=["config"])
