from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.db.session import check_db_connection

router = APIRouter()


@router.get(
    "/",
    summary="Health check",
    response_description="Service and dependency status",
)
async def health_check():
    db_ok = await check_db_connection()
    payload = {
        "status": "ok" if db_ok else "degraded",
        "version": settings.VERSION,
        "db": "connected" if db_ok else "unreachable",
    }
    code = status.HTTP_200_OK if db_ok else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(content=payload, status_code=code)
