"""
Shared test configuration and fixtures.

Sets up:
  - Windows SelectorEventLoop (required by asyncpg on Windows)
  - BACKEND_DIR on sys.path so `from app.*` works from any test
  - Shared async httpx client fixture
  - Shared dependency-override helpers (mock DB, admin user, operator user)
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

# ── Windows asyncio fix ───────────────────────────────────────────────────────
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# ── Path setup ────────────────────────────────────────────────────────────────
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(BACKEND_DIR)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

# Register ALL SQLAlchemy models so relationship strings resolve before any
# test instantiates an ORM object (avoids 'Negotiation' KeyError on EmailThread).
import app.db.init_db  # noqa: E402, F401


# ── Mock DB session ───────────────────────────────────────────────────────────

def make_mock_db_session() -> AsyncSession:
    """Return a MagicMock that satisfies AsyncSession's commit/rollback interface."""
    session = MagicMock(spec=AsyncSession)
    session.commit = AsyncMock(return_value=None)
    session.rollback = AsyncMock(return_value=None)
    session.close = AsyncMock(return_value=None)
    session.flush = AsyncMock(return_value=None)
    session.refresh = AsyncMock(return_value=None)
    session.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(first=MagicMock(return_value=None)))))
    session.add = MagicMock(return_value=None)
    return session


async def mock_get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency override: yields a no-op mock session."""
    yield make_mock_db_session()


# ── User fixtures ─────────────────────────────────────────────────────────────

def make_admin_user():
    from app.models.user import User, UserRole
    u = User()
    u.id = 1
    u.username = "testadmin"
    u.role = UserRole.ADMIN.value
    u.is_active = True
    u.hashed_password = "hashed"
    return u


def make_operator_user():
    from app.models.user import User, UserRole
    u = User()
    u.id = 2
    u.username = "testoperator"
    u.role = UserRole.OPERATOR.value
    u.is_active = True
    u.hashed_password = "hashed"
    return u


# ── App client fixture ────────────────────────────────────────────────────────

@pytest.fixture(autouse=True, scope="session")
def disable_rate_limiting() -> None:
    """
    Disable slowapi rate limiting for the entire test session.

    The limiter is keyed by client IP.  In the test suite every request comes
    from 127.0.0.1, so a small burst of auth tests (>5 in a minute) would
    exceed the production /auth/token limit and cause spurious 429s.

    Setting limiter._enabled = False makes both the SlowAPIMiddleware and the
    @limiter.limit() decorator skip all limit checks entirely, which also
    prevents the 'view_rate_limit' AttributeError that occurs when the
    middleware tries to inject headers that were never populated.
    """
    try:
        from app.core.rate_limit import limiter
        # slowapi 0.1.9 uses `self.enabled` (no underscore) as the on/off flag
        limiter.enabled = False
        yield
        limiter.enabled = True
    except Exception:
        yield  # If slowapi is not installed, skip gracefully


@pytest.fixture()
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Fresh httpx AsyncClient pointed at the FastAPI app for each test."""
    from app.main import app
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as ac:
        yield ac


@pytest.fixture()
async def admin_client() -> AsyncGenerator[AsyncClient, None]:
    """Client with `get_current_user` overridden to return an admin user."""
    from app.api.v1.deps import get_current_user, get_db
    from app.main import app

    async def _admin():
        return make_admin_user()

    app.dependency_overrides[get_current_user] = _admin
    app.dependency_overrides[get_db] = mock_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.fixture()
async def operator_client() -> AsyncGenerator[AsyncClient, None]:
    """Client with `get_current_user` overridden to return an operator user."""
    from app.api.v1.deps import get_current_user, get_db
    from app.main import app

    async def _operator():
        return make_operator_user()

    app.dependency_overrides[get_current_user] = _operator
    app.dependency_overrides[get_db] = mock_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.fixture()
async def unauth_client() -> AsyncGenerator[AsyncClient, None]:
    """Client with NO auth overrides — all requests are genuinely unauthenticated."""
    from app.api.v1.deps import get_db
    from app.main import app

    app.dependency_overrides[get_db] = mock_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as ac:
        yield ac

    app.dependency_overrides.clear()
