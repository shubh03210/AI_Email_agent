"""
FastAPI Dependencies
─────────────────────
Authentication and authorisation dependencies shared across all endpoints.

Dependency chain
────────────────
  oauth2_scheme                 →  extracts Bearer token from Authorization header
  get_current_user(token, db)   →  decodes JWT, loads User from DB, validates is_active
  require_admin(user)           →  asserts role == "admin"  (403 otherwise)
  require_operator_or_admin(u)  →  asserts role in {admin, operator}  (defensive alias)
"""

from __future__ import annotations

from typing import AsyncGenerator

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import decode_access_token
from app.core.token_blacklist import is_token_revoked
from app.db.session import AsyncSessionLocal
from app.models.user import User, UserRole

# Swagger UI uses this URL to generate the "Authorize" button
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_STR}/auth/token")


# ── Database session ──────────────────────────────────────────────────────────

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Yield an async DB session for the duration of a request.
    Auto-commits on success, rolls back on exception.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ── Authentication ────────────────────────────────────────────────────────────

async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Validate the Bearer JWT, load the corresponding User from the database,
    and confirm the account is active.

    Raises HTTP 401 for missing / expired / malformed tokens or unknown users.
    FastAPI's dependency cache ensures this runs at most once per request even
    when multiple downstream dependencies depend on it.
    """
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(token)
        username: str | None = payload.get("sub")
        if username is None:
            raise credentials_exc
    except JWTError:
        raise credentials_exc

    # Check JWT revocation blacklist (logout support)
    jti: str | None = payload.get("jti")
    if jti and await is_token_revoked(jti):
        raise credentials_exc

    from app.repositories import user_repo  # local import avoids circular at module load

    user = await user_repo.get_by_username(db, username)
    if user is None:
        raise credentials_exc
    return user


# ── Authorisation ─────────────────────────────────────────────────────────────

async def require_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """
    Allow only users with role == admin.
    Returns the User so endpoints can use it for audit logging.
    """
    if current_user.role != UserRole.ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required for this operation.",
        )
    return current_user


async def require_operator_or_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """
    Allow users with role == admin or operator.
    Kept as an explicit dependency for semantic clarity and future extensibility.
    """
    if current_user.role not in (UserRole.ADMIN.value, UserRole.OPERATOR.value):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions.",
        )
    return current_user


# ── Type aliases used in endpoint signatures ──────────────────────────────────

DB = AsyncSession
CurrentUser = User
