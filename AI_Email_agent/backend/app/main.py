from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.api.v1.api import api_router
from app.core.config import settings
from app.core.logging import logger, setup_logging
from app.core.rate_limit import limiter


async def _bootstrap_admin() -> None:
    """
    Create the default admin user on first startup if the users table is empty.
    Uses ADMIN_USERNAME / ADMIN_PASSWORD from settings (set via .env).
    This runs inside the lifespan so it shares the application's async context.
    """
    from app.db.session import AsyncSessionLocal
    from app.repositories import user_repo
    from app.schemas.user import UserCreate

    async with AsyncSessionLocal() as session:
        try:
            count = await user_repo.count_all(session)
            if count == 0:
                await user_repo.create(
                    session,
                    UserCreate(
                        username=settings.ADMIN_USERNAME,
                        password=settings.ADMIN_PASSWORD,
                        role="admin",
                    ),
                )
                await session.commit()
                logger.info(
                    f"Default admin user bootstrapped | username={settings.ADMIN_USERNAME!r}"
                )
            else:
                logger.debug(f"Users table has {count} record(s) — bootstrap skipped")
        except Exception as exc:
            await session.rollback()
            # Non-fatal: if the table doesn't exist yet (before migrations) or DB is
            # temporarily unavailable, log and continue — the app is still usable.
            logger.warning(f"Admin bootstrap skipped (will retry on next start): {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(settings.LOG_LEVEL)
    logger.info(f"Starting {settings.PROJECT_NAME} v{settings.VERSION}")
    logger.info(f"Environment: {'debug' if settings.DEBUG else 'production'}")
    logger.info(f"Docs: http://localhost:8000{settings.API_V1_STR}/docs")
    logger.info(
        "Prospect replies are processed by Celery (poll inbox + run agent). "
        "Use `python run.py` locally — uvicorn alone will not auto-run the agent."
    )

    # ── Deployment readiness checks ───────────────────────────────────────────
    # Validate environment variables and credential files at startup.
    # Errors are logged but do NOT abort startup (degraded > crash-loop).
    from app.core.env_check import check_environment, log_env_report
    env_report = check_environment()
    log_env_report(env_report, abort_on_errors=False)

    # Populate SQLAlchemy metadata so relationship strings resolve before the
    # first request (prevents mapper initialisation errors).
    import app.db.init_db  # noqa: F401

    # Seed the initial admin account if no users exist yet
    await _bootstrap_admin()

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
app.add_middleware(SlowAPIMiddleware)

# ── Rate limiting setup ───────────────────────────────────────────────────────
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ── Convenience routes ────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def root_redirect():
    return RedirectResponse(url=f"{settings.API_V1_STR}/docs")


@app.get("/docs", include_in_schema=False)
async def docs_redirect():
    return RedirectResponse(url=f"{settings.API_V1_STR}/docs")


@app.get("/health", tags=["health"], summary="API liveness probe")
async def health():
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
