"""
Auth API
─────────
JWT token issuance and revocation via OAuth2 password flow.

POST /auth/token   →  verify credentials → return signed JWT + role
POST /auth/logout  →  revoke the current JWT (adds jti to Redis blacklist)
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import get_db
from app.core.config import settings
from app.core.logging import logger
from app.core.rate_limit import limiter
from app.core.security import create_access_token, decode_access_token, verify_password
from app.core.token_blacklist import revoke_token
from app.repositories import user_repo
from app.schemas.user import TokenResponse

_oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_STR}/auth/token")

router = APIRouter()


@router.post(
    "/token",
    response_model=TokenResponse,
    summary="Obtain a Bearer access token",
    response_description="Signed JWT with role claim, valid for ACCESS_TOKEN_EXPIRE_MINUTES",
)
@limiter.limit("5/minute")
async def login(
    request: Request,
    form: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """
    Accepts standard OAuth2 password-flow form fields (``username`` + ``password``).

    - Looks up the user in the database.
    - Verifies the bcrypt-hashed password.
    - Records last_login_at.
    - Returns a signed JWT containing ``sub`` (username) and ``role``.

    Returns HTTP 401 on bad credentials or inactive account.
    """
    invalid_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Incorrect username or password",
        headers={"WWW-Authenticate": "Bearer"},
    )

    user = await user_repo.get_by_username(db, form.username)

    if user is None or not verify_password(form.password, user.hashed_password):
        logger.warning(f"Failed login attempt | username={form.username!r}")
        raise invalid_exc

    # get_by_username already filters is_active=True, but be explicit
    if not user.is_active:
        logger.warning(f"Login attempt for inactive account | username={form.username!r}")
        raise invalid_exc

    await user_repo.record_login(db, user)

    token = create_access_token(subject=user.username, role=user.role)
    logger.info(f"User authenticated | username={user.username!r} role={user.role}")

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        role=user.role,
    )


@router.post(
    "/logout",
    summary="Revoke the current access token",
    status_code=status.HTTP_200_OK,
)
async def logout(
    token: str = Depends(_oauth2_scheme),
) -> dict:
    """
    Invalidate the caller's JWT by adding its ``jti`` to the Redis revocation
    blacklist.  The blacklist entry expires automatically when the token would
    have naturally expired, so no background cleanup is needed.

    After calling this endpoint the token is immediately invalid — any
    subsequent request using it will receive HTTP 401.
    """
    try:
        payload = decode_access_token(token)
    except Exception:
        # Already expired or malformed — nothing to revoke.
        return {"message": "Logged out."}

    jti: str | None = payload.get("jti")
    exp: int | None = payload.get("exp")

    if jti and exp:
        now_ts = int(datetime.now(tz=timezone.utc).timestamp())
        ttl = max(0, exp - now_ts)
        await revoke_token(jti, ttl)
        logger.info(
            f"[logout] Token revoked | "
            f"user={payload.get('sub')!r} jti={jti} ttl={ttl}s"
        )

    return {"message": "Logged out."}
