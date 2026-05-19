from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from app.api.v1.api import api_router
from app.core.config import settings
from app.core.logging import logger, setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(settings.LOG_LEVEL)
    logger.info(f"Starting {settings.PROJECT_NAME} v{settings.VERSION}")
    logger.info(f"Environment: {'debug' if settings.DEBUG else 'production'}")
    logger.info(f"Docs: http://localhost:8000{settings.API_V1_STR}/docs")
    yield
    logger.info(f"Shutting down {settings.PROJECT_NAME}")


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description=(
        "Autonomous AI Email Agent — outreach, negotiation, scheduling, "
        "and meeting management via Gmail + Google Calendar + Groq LLM."
    ),
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url=f"{settings.API_V1_STR}/docs",
    redoc_url=f"{settings.API_V1_STR}/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Convenience routes ────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def root_redirect():
    """Redirect root to the interactive API docs."""
    return RedirectResponse(url=f"{settings.API_V1_STR}/docs")


@app.get("/docs", include_in_schema=False)
async def docs_redirect():
    """Short /docs alias → versioned docs."""
    return RedirectResponse(url=f"{settings.API_V1_STR}/docs")


@app.get("/health", tags=["health"], summary="API health check")
async def health():
    """Quick liveness probe — returns 200 if the API process is running."""
    return {"status": "ok", "version": settings.VERSION}


# ── Error handlers ────────────────────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception on {request.method} {request.url}: {exc}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error. Please try again later."},
    )


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    logger.warning(f"ValueError on {request.method} {request.url}: {exc}")
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": str(exc)},
    )


app.include_router(api_router, prefix=settings.API_V1_STR)
