from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
import asyncio
import socket

from app.core.config import settings
from app.db.session import check_db_connection

router = APIRouter()


def _redis_ok() -> bool:
    """Quick TCP check — no Redis client needed."""
    try:
        host = "localhost"
        port = 6379
        # Parse host/port from REDIS_URL if available
        url = getattr(settings, "REDIS_URL", "redis://localhost:6379/0")
        if "://" in url:
            netloc = url.split("://")[1].split("/")[0]
            if ":" in netloc:
                host, port_str = netloc.rsplit(":", 1)
                port = int(port_str)
        with socket.create_connection((host, port), timeout=2.0):
            return True
    except OSError:
        return False


@router.get(
    "/",
    summary="Health check",
    response_description="Service and dependency status",
)
async def health_check():
    db_ok = await check_db_connection()
    redis_ok = await asyncio.get_event_loop().run_in_executor(None, _redis_ok)

    all_ok = db_ok and redis_ok
    payload = {
        "status": "ok" if all_ok else "degraded",
        "version": settings.VERSION,
        "db": "connected" if db_ok else "unreachable",
        "redis": "connected" if redis_ok else "unreachable",
    }
    code = status.HTTP_200_OK if all_ok else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(content=payload, status_code=code)
