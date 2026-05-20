"""
Security utilities
──────────────────
Password hashing (bcrypt) and JWT creation / decoding.
"""

import uuid
from datetime import datetime, timedelta
from typing import Optional

from jose import jwt
from passlib.context import CryptContext

from app.core.config import settings

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ── Password hashing ──────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    """Return a bcrypt hash of *plain*."""
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """
    Constant-time bcrypt verification.
    Returns False instead of raising for unrecognised / malformed hashes.
    """
    try:
        return _pwd_context.verify(plain, hashed)
    except Exception:
        return False


# ── JWT ───────────────────────────────────────────────────────────────────────

def create_access_token(
    subject: str,
    role: str,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """
    Sign a JWT containing the username (sub) and role.
    *expires_delta* defaults to ACCESS_TOKEN_EXPIRE_MINUTES from settings.
    """
    expire = datetime.utcnow() + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    jti = str(uuid.uuid4())  # unique token ID for revocation (logout blacklist)
    return jwt.encode(
        {"sub": subject, "role": role, "exp": expire, "jti": jti},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


def decode_access_token(token: str) -> dict:
    """
    Decode and verify a JWT.
    Raises jose.JWTError for expired, malformed, or signature-invalid tokens.
    """
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
