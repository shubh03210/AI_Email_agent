"""
Rate Limiting
──────────────
Provides a per-IP rate limiter backed by Redis.

Usage
─────
  from app.core.rate_limit import limiter

  # In FastAPI app setup (main.py):
  app.state.limiter = limiter
  app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

  # On an endpoint:
  @limiter.limit("5/minute")
  async def my_endpoint(request: Request, ...):
      ...

The storage backend uses REDIS_URL from settings so limits survive worker
restarts and are shared across multiple app replicas.  Falls back to in-memory
storage if Redis is unavailable at import time (so local dev without Redis
still works).

Limit reference: https://limits.readthedocs.io/en/latest/storage.html
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings
from app.core.logging import logger


def _build_limiter() -> Limiter:
    """
    Attempt to construct a Redis-backed Limiter.
    Falls back to in-memory storage if the Redis URL is unavailable so that
    development environments without Redis are not broken.
    """
    try:
        lim = Limiter(
            key_func=get_remote_address,
            storage_uri=settings.REDIS_URL,
            default_limits=["300/minute"],
        )
        logger.info(f"[rate_limit] Redis-backed limiter initialised | {settings.REDIS_URL}")
        return lim
    except Exception as exc:
        logger.warning(
            f"[rate_limit] Redis unavailable ({exc}); "
            "falling back to in-memory rate limiter"
        )
        return Limiter(
            key_func=get_remote_address,
            default_limits=["300/minute"],
        )


limiter = _build_limiter()
