"""
JWT Token Blacklist
────────────────────
Redis-backed revocation store for JWT tokens.

How it works
────────────
  1. Every JWT now contains a ``jti`` (JWT ID) claim — a UUID generated at
     issuance time (see ``app.core.security.create_access_token``).
  2. On logout, the ``jti`` is written to Redis with a TTL equal to the
     remaining token lifetime.  Revoked tokens naturally expire out of the
     store once they would have expired anyway.
  3. ``get_current_user`` (``app.api.v1.deps``) calls ``is_token_revoked``
     on every authenticated request.  If the jti is in Redis the request is
     rejected with HTTP 401.

Redis key format:
  jti:revoked:<uuid>   →  "1"   TTL = remaining token seconds
"""

from __future__ import annotations

import redis.asyncio as aioredis

from app.core.config import settings
from app.core.logging import logger

# Key prefix to avoid collisions with other Redis namespaces
_PREFIX = "jti:revoked:"


async def revoke_token(jti: str, ttl_seconds: int) -> None:
    """
    Add a JWT ID to the revocation set.

    Args:
        jti:         The ``jti`` claim value from the token.
        ttl_seconds: Seconds until the token would naturally expire.
                     The Redis key is set with this TTL so it self-cleans.
    """
    if ttl_seconds <= 0:
        # Token is already expired — nothing to revoke.
        return
    try:
        r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        async with r:
            await r.setex(f"{_PREFIX}{jti}", ttl_seconds, "1")
        logger.info(f"[token_blacklist] Token revoked | jti={jti} ttl={ttl_seconds}s")
    except Exception as exc:
        # Non-fatal: if Redis is down, log and continue.  The token will still
        # be valid, but this is preferable to breaking the logout endpoint.
        logger.warning(f"[token_blacklist] Could not revoke token jti={jti}: {exc}")


async def is_token_revoked(jti: str) -> bool:
    """
    Return True if the given JWT ID has been revoked.

    Args:
        jti: The ``jti`` claim value from the decoded token.

    Returns:
        True  → reject the request (HTTP 401).
        False → token is valid (or Redis is unavailable — fail open to avoid
                breaking all auth when Redis restarts).
    """
    try:
        r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        async with r:
            return await r.exists(f"{_PREFIX}{jti}") > 0
    except Exception as exc:
        # Fail open: if Redis is unreachable, assume the token is not revoked
        # rather than blocking every authenticated user.
        logger.warning(f"[token_blacklist] Redis unavailable for revocation check: {exc}")
        return False
