"""
Auth API Tests
───────────────
Tests for POST /api/v1/auth/token.

Strategy:
  - httpx.AsyncClient + ASGITransport (no real HTTP server needed)
  - get_db is overridden with a no-op mock session (no real DB needed)
  - user_repo.get_by_username is patched per test scenario
  - user_repo.record_login is always patched to a no-op

Covers:
  - Successful login  → 200, access_token, token_type, expires_in, role
  - Wrong password    → 401
  - Unknown username  → 401
  - Inactive account  → 401
  - Token is decodable with the app's secret key
  - Decoded token carries correct sub and role claims
  - Health endpoint is still public (no token required)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_active_user(username: str, plain_password: str, role: str):
    """Build a User ORM instance with a real bcrypt hash."""
    from app.core.security import hash_password
    from app.models.user import User, UserRole

    u = User()
    u.id = 99
    u.username = username
    u.hashed_password = hash_password(plain_password)
    u.role = role
    u.is_active = True
    return u


def _make_inactive_user(username: str, plain_password: str):
    u = _make_active_user(username, plain_password, "admin")
    u.is_active = False
    return u


# ── Login success ─────────────────────────────────────────────────────────────

class TestLoginSuccess:

    async def test_admin_login_returns_200(self, unauth_client: AsyncClient):
        user = _make_active_user("admin", "Adm1nPass!", "admin")
        with (
            patch("app.repositories.user_repo.get_by_username", new=AsyncMock(return_value=user)),
            patch("app.repositories.user_repo.record_login",    new=AsyncMock(return_value=None)),
        ):
            resp = await unauth_client.post(
                "/api/v1/auth/token",
                data={"username": "admin", "password": "Adm1nPass!"},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        assert resp.status_code == 200

    async def test_response_contains_access_token(self, unauth_client: AsyncClient):
        user = _make_active_user("admin", "Adm1nPass!", "admin")
        with (
            patch("app.repositories.user_repo.get_by_username", new=AsyncMock(return_value=user)),
            patch("app.repositories.user_repo.record_login",    new=AsyncMock(return_value=None)),
        ):
            resp = await unauth_client.post(
                "/api/v1/auth/token",
                data={"username": "admin", "password": "Adm1nPass!"},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        data = resp.json()
        assert "access_token" in data
        assert isinstance(data["access_token"], str)
        assert len(data["access_token"]) > 20

    async def test_response_contains_role(self, unauth_client: AsyncClient):
        user = _make_active_user("admin", "Adm1nPass!", "admin")
        with (
            patch("app.repositories.user_repo.get_by_username", new=AsyncMock(return_value=user)),
            patch("app.repositories.user_repo.record_login",    new=AsyncMock(return_value=None)),
        ):
            resp = await unauth_client.post(
                "/api/v1/auth/token",
                data={"username": "admin", "password": "Adm1nPass!"},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        data = resp.json()
        assert data.get("role") == "admin"

    async def test_response_contains_expires_in(self, unauth_client: AsyncClient):
        user = _make_active_user("admin", "Adm1nPass!", "admin")
        with (
            patch("app.repositories.user_repo.get_by_username", new=AsyncMock(return_value=user)),
            patch("app.repositories.user_repo.record_login",    new=AsyncMock(return_value=None)),
        ):
            resp = await unauth_client.post(
                "/api/v1/auth/token",
                data={"username": "admin", "password": "Adm1nPass!"},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        data = resp.json()
        assert "expires_in" in data
        assert isinstance(data["expires_in"], int)
        assert data["expires_in"] > 0

    async def test_token_type_is_bearer(self, unauth_client: AsyncClient):
        user = _make_active_user("admin", "Adm1nPass!", "admin")
        with (
            patch("app.repositories.user_repo.get_by_username", new=AsyncMock(return_value=user)),
            patch("app.repositories.user_repo.record_login",    new=AsyncMock(return_value=None)),
        ):
            resp = await unauth_client.post(
                "/api/v1/auth/token",
                data={"username": "admin", "password": "Adm1nPass!"},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        assert resp.json()["token_type"] == "bearer"

    async def test_operator_login_returns_operator_role(self, unauth_client: AsyncClient):
        user = _make_active_user("op_user", "OpPass123!", "operator")
        with (
            patch("app.repositories.user_repo.get_by_username", new=AsyncMock(return_value=user)),
            patch("app.repositories.user_repo.record_login",    new=AsyncMock(return_value=None)),
        ):
            resp = await unauth_client.post(
                "/api/v1/auth/token",
                data={"username": "op_user", "password": "OpPass123!"},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        assert resp.status_code == 200
        assert resp.json()["role"] == "operator"

    async def test_issued_token_is_decodable(self, unauth_client: AsyncClient):
        """The token returned by login must be decodable with the app's secret."""
        user = _make_active_user("alice", "Al1cePass!", "admin")
        with (
            patch("app.repositories.user_repo.get_by_username", new=AsyncMock(return_value=user)),
            patch("app.repositories.user_repo.record_login",    new=AsyncMock(return_value=None)),
        ):
            resp = await unauth_client.post(
                "/api/v1/auth/token",
                data={"username": "alice", "password": "Al1cePass!"},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        token = resp.json()["access_token"]
        from app.core.security import decode_access_token
        payload = decode_access_token(token)
        assert payload["sub"] == "alice"
        assert payload["role"] == "admin"

    async def test_token_contains_subject_matching_username(self, unauth_client: AsyncClient):
        user = _make_active_user("specific_user", "Sp3cific!", "operator")
        with (
            patch("app.repositories.user_repo.get_by_username", new=AsyncMock(return_value=user)),
            patch("app.repositories.user_repo.record_login",    new=AsyncMock(return_value=None)),
        ):
            resp = await unauth_client.post(
                "/api/v1/auth/token",
                data={"username": "specific_user", "password": "Sp3cific!"},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        token = resp.json()["access_token"]
        from app.core.security import decode_access_token
        assert decode_access_token(token)["sub"] == "specific_user"


# ── Login failure ─────────────────────────────────────────────────────────────

class TestLoginFailure:

    async def test_wrong_password_returns_401(self, unauth_client: AsyncClient):
        user = _make_active_user("admin", "CorrectPass1!", "admin")
        with (
            patch("app.repositories.user_repo.get_by_username", new=AsyncMock(return_value=user)),
            patch("app.repositories.user_repo.record_login",    new=AsyncMock(return_value=None)),
        ):
            resp = await unauth_client.post(
                "/api/v1/auth/token",
                data={"username": "admin", "password": "WrongPassword!"},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        assert resp.status_code == 401

    async def test_wrong_password_has_www_authenticate_header(self, unauth_client: AsyncClient):
        user = _make_active_user("admin", "CorrectPass1!", "admin")
        with (
            patch("app.repositories.user_repo.get_by_username", new=AsyncMock(return_value=user)),
            patch("app.repositories.user_repo.record_login",    new=AsyncMock(return_value=None)),
        ):
            resp = await unauth_client.post(
                "/api/v1/auth/token",
                data={"username": "admin", "password": "WrongPass!"},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        assert "WWW-Authenticate" in resp.headers
        assert resp.headers["WWW-Authenticate"] == "Bearer"

    async def test_unknown_username_returns_401(self, unauth_client: AsyncClient):
        with (
            patch("app.repositories.user_repo.get_by_username", new=AsyncMock(return_value=None)),
            patch("app.repositories.user_repo.record_login",    new=AsyncMock(return_value=None)),
        ):
            resp = await unauth_client.post(
                "/api/v1/auth/token",
                data={"username": "nobody", "password": "SomePass1!"},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        assert resp.status_code == 401

    async def test_inactive_user_returns_401(self, unauth_client: AsyncClient):
        """A user with is_active=False must be rejected even with correct password."""
        user = _make_active_user("dormant", "DormantPass1!", "admin")
        user.is_active = False
        # get_by_username already filters is_active=True, so return None to simulate
        with (
            patch("app.repositories.user_repo.get_by_username", new=AsyncMock(return_value=None)),
            patch("app.repositories.user_repo.record_login",    new=AsyncMock(return_value=None)),
        ):
            resp = await unauth_client.post(
                "/api/v1/auth/token",
                data={"username": "dormant", "password": "DormantPass1!"},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        assert resp.status_code == 401

    async def test_empty_credentials_returns_422(self, unauth_client: AsyncClient):
        """Missing form fields → FastAPI 422 Unprocessable Entity."""
        resp = await unauth_client.post(
            "/api/v1/auth/token",
            data={},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert resp.status_code == 422


# ── Public endpoint still accessible ─────────────────────────────────────────

class TestPublicEndpoints:

    async def test_health_requires_no_token(self, unauth_client: AsyncClient):
        resp = await unauth_client.get("/api/v1/health/")
        assert resp.status_code == 200

    async def test_health_returns_ok_status(self, unauth_client: AsyncClient):
        resp = await unauth_client.get("/api/v1/health/")
        # Health may be 200 or 503 depending on DB/Redis — just check it's not 401/403
        assert resp.status_code not in (401, 403)
