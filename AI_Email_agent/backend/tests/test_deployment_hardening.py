"""
Phase 9 — Deployment Hardening Tests
──────────────────────────────────────
Validates all Phase 9 additions:

  1.  .dockerignore coverage (backend + frontend)
  2.  Backend Dockerfile — multi-stage structure, non-root user, venv, healthcheck
  3.  Frontend Dockerfile — multi-stage (node → nginx)
  4.  nginx.conf — gzip, security headers, SPA routing, static caching
  5.  docker-compose.yml — anchors, service list, healthchecks, volumes
  6.  env_check module — placeholder detection, required-field validation,
      missing-file warnings
  7.  Health endpoints — /live, /ready  (unit + integration)
  8.  Startup integration — env_check is wired into main.py lifespan
  9.  .env.example — completeness (all required variables present)
"""

from __future__ import annotations

import pathlib
import re
import textwrap
from typing import List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT = pathlib.Path(__file__).resolve().parents[2]       # AI_Email_agent/
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend_react"

BACKEND_DOCKERIGNORE = BACKEND / ".dockerignore"
FRONTEND_DOCKERIGNORE = FRONTEND / ".dockerignore"
BACKEND_DOCKERFILE    = BACKEND / "Dockerfile"
FRONTEND_DOCKERFILE   = FRONTEND / "Dockerfile"
NGINX_CONF            = FRONTEND / "nginx.conf"
COMPOSE_FILE          = ROOT / "docker-compose.yml"
ENV_EXAMPLE           = BACKEND / ".env.example"
ENV_CHECK_MODULE      = BACKEND / "app" / "core" / "env_check.py"
MAIN_MODULE           = BACKEND / "app" / "main.py"
HEALTH_ENDPOINT       = BACKEND / "app" / "api" / "v1" / "endpoints" / "health.py"


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════════════
# 1. .dockerignore coverage
# ══════════════════════════════════════════════════════════════════════════════

class TestDockerignoreFiles:

    def test_backend_dockerignore_exists(self):
        assert BACKEND_DOCKERIGNORE.exists(), \
            "backend/.dockerignore is missing — Docker builds will include .venv, __pycache__, .env"

    def test_frontend_dockerignore_exists(self):
        assert FRONTEND_DOCKERIGNORE.exists(), \
            "frontend_react/.dockerignore is missing — Docker builds will include node_modules/"

    def test_backend_ignores_venv(self):
        content = read(BACKEND_DOCKERIGNORE)
        assert ".venv" in content or "venv" in content, \
            "backend/.dockerignore should exclude virtual environment directories"

    def test_backend_ignores_dotenv(self):
        content = read(BACKEND_DOCKERIGNORE)
        assert ".env" in content, \
            "backend/.dockerignore should exclude .env (secrets must not be baked into images)"

    def test_backend_ignores_pycache(self):
        content = read(BACKEND_DOCKERIGNORE)
        assert "__pycache__" in content, \
            "backend/.dockerignore should exclude __pycache__"

    def test_backend_ignores_credentials(self):
        content = read(BACKEND_DOCKERIGNORE)
        assert "credentials" in content, \
            "backend/.dockerignore should exclude credentials/ (mounted as volume at runtime)"

    def test_backend_ignores_tests(self):
        content = read(BACKEND_DOCKERIGNORE)
        assert "tests" in content, \
            "backend/.dockerignore should exclude tests/ to keep production images lean"

    def test_frontend_ignores_node_modules(self):
        content = read(FRONTEND_DOCKERIGNORE)
        assert "node_modules" in content, \
            "frontend_react/.dockerignore must exclude node_modules/ (npm ci re-installs inside Docker)"

    def test_frontend_ignores_dist(self):
        content = read(FRONTEND_DOCKERIGNORE)
        assert "dist" in content, \
            "frontend_react/.dockerignore should exclude dist/ (built inside Docker)"

    def test_frontend_ignores_dotenv(self):
        content = read(FRONTEND_DOCKERIGNORE)
        assert ".env" in content, \
            "frontend_react/.dockerignore should exclude .env files"


# ══════════════════════════════════════════════════════════════════════════════
# 2. Backend Dockerfile — multi-stage, non-root, venv
# ══════════════════════════════════════════════════════════════════════════════

class TestBackendDockerfile:

    @pytest.fixture(autouse=True)
    def content(self):
        self._content = read(BACKEND_DOCKERFILE)

    def test_has_builder_stage(self):
        assert "AS builder" in self._content, \
            "Backend Dockerfile should have a 'builder' stage for compiling dependencies"

    def test_has_runtime_stage(self):
        assert "AS runtime" in self._content or "FROM python" in self._content.split("AS builder")[1], \
            "Backend Dockerfile should have a separate runtime stage"

    def test_copies_venv_from_builder(self):
        assert "--from=builder" in self._content, \
            "Runtime stage should COPY the virtual environment from the builder stage"

    def test_no_build_tools_in_runtime(self):
        # build-essential should only appear in the builder section (before AS runtime)
        if "AS runtime" in self._content:
            runtime_section = self._content.split("AS runtime")[1]
            assert "build-essential" not in runtime_section, \
                "build-essential must NOT be installed in the runtime stage"

    def test_non_root_user(self):
        assert "useradd" in self._content or "adduser" in self._content, \
            "Dockerfile should create a non-root user"
        assert "USER appuser" in self._content or "USER " in self._content, \
            "Dockerfile should switch to a non-root user before CMD/ENTRYPOINT"

    def test_python_unbuffered_env(self):
        assert "PYTHONUNBUFFERED" in self._content, \
            "PYTHONUNBUFFERED=1 should be set to flush log output immediately"

    def test_healthcheck_uses_live_endpoint(self):
        assert "/health/live" in self._content, \
            "Dockerfile HEALTHCHECK should use /api/v1/health/live (lightweight liveness probe)"

    def test_expose_port(self):
        assert "EXPOSE 8000" in self._content, \
            "Dockerfile should EXPOSE 8000"

    def test_entrypoint_set(self):
        assert "ENTRYPOINT" in self._content, \
            "Dockerfile should define ENTRYPOINT (entrypoint.sh runs migrations + uvicorn)"


# ══════════════════════════════════════════════════════════════════════════════
# 3. Frontend Dockerfile — multi-stage
# ══════════════════════════════════════════════════════════════════════════════

class TestFrontendDockerfile:

    @pytest.fixture(autouse=True)
    def content(self):
        self._content = read(FRONTEND_DOCKERFILE)

    def test_has_build_stage(self):
        assert "AS build" in self._content, \
            "Frontend Dockerfile should have a 'build' stage (node → npm run build)"

    def test_has_serve_stage(self):
        assert "nginx" in self._content.lower(), \
            "Frontend Dockerfile should use nginx to serve the built SPA"

    def test_copies_dist_to_nginx(self):
        assert "--from=build" in self._content, \
            "Serve stage should COPY /app/dist from the build stage"

    def test_expose_port_3000(self):
        assert "EXPOSE 3000" in self._content, \
            "Frontend Dockerfile should EXPOSE 3000 (matching nginx.conf listen port)"

    def test_npm_ci_used(self):
        assert "npm ci" in self._content, \
            "Dockerfile should use 'npm ci' (reproducible installs from package-lock.json)"


# ══════════════════════════════════════════════════════════════════════════════
# 4. nginx.conf — gzip, security headers, SPA routing
# ══════════════════════════════════════════════════════════════════════════════

class TestNginxConf:

    @pytest.fixture(autouse=True)
    def content(self):
        self._content = read(NGINX_CONF)

    def test_listens_on_3000(self):
        assert "listen 3000" in self._content, \
            "nginx.conf should listen on port 3000 (matches Dockerfile EXPOSE and compose port mapping)"

    def test_gzip_enabled(self):
        assert "gzip" in self._content and "gzip_types" in self._content, \
            "nginx.conf should enable gzip compression with explicit content types"

    def test_security_header_frame_options(self):
        assert "X-Frame-Options" in self._content, \
            "nginx.conf should add X-Frame-Options header to prevent clickjacking"

    def test_security_header_content_type(self):
        assert "X-Content-Type-Options" in self._content, \
            "nginx.conf should add X-Content-Type-Options: nosniff"

    def test_security_header_xss_protection(self):
        assert "X-XSS-Protection" in self._content, \
            "nginx.conf should add X-XSS-Protection header"

    def test_spa_fallback_routing(self):
        assert "try_files" in self._content and "index.html" in self._content, \
            "nginx.conf should have try_files fallback to index.html for React SPA routing"

    def test_api_proxy(self):
        assert "proxy_pass" in self._content and "backend:8000" in self._content, \
            "nginx.conf should proxy /api requests to the backend service"

    def test_no_cache_for_index_html(self):
        assert "no-cache" in self._content and "index.html" in self._content, \
            "nginx.conf should set no-cache headers for index.html so deploys take effect immediately"

    def test_static_asset_caching(self):
        assert "immutable" in self._content or "1y" in self._content, \
            "nginx.conf should aggressively cache static assets (JS/CSS with content-hash filenames)"

    def test_hidden_file_deny(self):
        assert "/\\." in self._content or r"/\." in self._content, \
            "nginx.conf should deny access to hidden files (e.g. .env, .git)"


# ══════════════════════════════════════════════════════════════════════════════
# 5. docker-compose.yml — anchors, services, healthchecks, volumes
# ══════════════════════════════════════════════════════════════════════════════

class TestDockerCompose:

    @pytest.fixture(autouse=True)
    def content(self):
        self._content = read(COMPOSE_FILE)

    def test_uses_yaml_anchors(self):
        assert "x-backend-base" in self._content or "&backend-base" in self._content, \
            "docker-compose.yml should use YAML anchors to DRY shared backend service config"

    def test_all_five_services_defined(self):
        for service in ("redis", "backend", "celery_worker", "celery_beat", "frontend"):
            assert service in self._content, \
                f"docker-compose.yml is missing the '{service}' service"

    def test_redis_healthcheck(self):
        assert "redis-cli" in self._content and "ping" in self._content, \
            "Redis service should have a healthcheck using redis-cli ping"

    def test_backend_depends_on_redis_healthy(self):
        assert "service_healthy" in self._content, \
            "Backend should depend on Redis being healthy before starting"

    def test_named_volumes_declared(self):
        for vol in ("redis_data", "backend_logs", "celery_beat_data"):
            assert vol in self._content, \
                f"docker-compose.yml should declare named volume '{vol}'"

    def test_credentials_mounted_readonly(self):
        assert "credentials:/app/credentials" in self._content, \
            "Credentials should be mounted as a volume (not baked into the image)"

    def test_celery_worker_queue_config(self):
        assert "queues=default,agent,gmail" in self._content, \
            "Celery worker should consume from all three queues: default, agent, gmail"

    def test_frontend_healthcheck_present(self):
        assert "localhost:3000" in self._content, \
            "Frontend service should have a healthcheck targeting port 3000"

    def test_celery_beat_schedule_volume(self):
        assert "celery_beat_data" in self._content, \
            "Celery beat should use a volume for the persistent schedule file"


# ══════════════════════════════════════════════════════════════════════════════
# 6. env_check module
# ══════════════════════════════════════════════════════════════════════════════

class TestEnvCheckModule:

    def test_module_exists(self):
        assert ENV_CHECK_MODULE.exists(), \
            "app/core/env_check.py is missing"

    def test_check_environment_importable(self):
        content = read(ENV_CHECK_MODULE)
        assert "def check_environment" in content, \
            "env_check.py should define a check_environment() function"

    def test_log_env_report_importable(self):
        content = read(ENV_CHECK_MODULE)
        assert "def log_env_report" in content, \
            "env_check.py should define a log_env_report() function"

    def test_validates_groq_api_key(self):
        content = read(ENV_CHECK_MODULE)
        assert "GROQ_API_KEY" in content, \
            "env_check.py should validate the GROQ_API_KEY setting"

    def test_validates_database_url(self):
        content = read(ENV_CHECK_MODULE)
        assert "DATABASE_URL" in content or "database_url" in content.lower(), \
            "env_check.py should validate the DATABASE_URL setting"

    def test_validates_redis_url(self):
        content = read(ENV_CHECK_MODULE)
        assert "REDIS_URL" in content or "redis_url" in content.lower(), \
            "env_check.py should validate the REDIS_URL setting"

    def test_validates_secret_key(self):
        content = read(ENV_CHECK_MODULE)
        assert "SECRET_KEY" in content, \
            "env_check.py should warn about insecure SECRET_KEY defaults"

    def test_checks_credential_files(self):
        content = read(ENV_CHECK_MODULE)
        assert "credentials" in content.lower() or "gmail_credentials" in content.lower(), \
            "env_check.py should check whether the Gmail credentials file exists"

    def test_env_report_dataclass_has_errors_and_warnings(self):
        content = read(ENV_CHECK_MODULE)
        assert "errors" in content and "warnings" in content, \
            "EnvReport should contain separate errors and warnings lists"

    def test_abort_on_errors_parameter(self):
        content = read(ENV_CHECK_MODULE)
        assert "abort_on_errors" in content, \
            "log_env_report() should support abort_on_errors parameter"

    def test_placeholder_detection(self):
        content = read(ENV_CHECK_MODULE)
        assert "_looks_like_placeholder" in content or "placeholder" in content.lower(), \
            "env_check.py should detect placeholder values and warn the operator"


class TestEnvCheckLogic:
    """Unit tests for check_environment() logic via mocked settings."""

    def _make_settings(self, **overrides):
        """Return a mock Settings object with sensible defaults."""
        defaults = {
            "GROQ_API_KEY": "gsk_real_api_key_1234567890",
            "DATABASE_URL": "postgresql+asyncpg://user:pass@host.pooler.supabase.com:6543/postgres",
            "REDIS_URL": "redis://redis:6379/0",
            "SECRET_KEY": "a" * 64,
            "ADMIN_PASSWORD": "strong-password-here",
            "GMAIL_CREDENTIALS_JSON": "/nonexistent/path/credentials.json",
            "CALENDAR_CREDENTIALS_JSON": "/nonexistent/path/calendar.json",
        }
        defaults.update(overrides)
        m = MagicMock()
        for k, v in defaults.items():
            setattr(m, k, v)
        return m

    def test_empty_groq_key_is_error(self):
        from app.core.env_check import check_environment
        settings = self._make_settings(GROQ_API_KEY="")
        with patch("app.core.env_check.settings", settings):
            report = check_environment()
        assert any("GROQ_API_KEY" in e for e in report.errors), \
            "Empty GROQ_API_KEY should be a critical error"

    def test_placeholder_groq_key_is_warning(self):
        from app.core.env_check import check_environment
        settings = self._make_settings(GROQ_API_KEY="your-groq-api-key-here")
        with patch("app.core.env_check.settings", settings):
            report = check_environment()
        assert any("GROQ_API_KEY" in w for w in report.warnings), \
            "Placeholder GROQ_API_KEY should produce a warning"

    def test_empty_redis_url_is_error(self):
        from app.core.env_check import check_environment
        settings = self._make_settings(REDIS_URL="")
        with patch("app.core.env_check.settings", settings):
            report = check_environment()
        assert any("REDIS_URL" in e for e in report.errors), \
            "Empty REDIS_URL should be a critical error"

    def test_insecure_secret_key_is_warning(self):
        from app.core.env_check import check_environment
        settings = self._make_settings(SECRET_KEY="change-me-in-production-use-secrets")
        with patch("app.core.env_check.settings", settings):
            report = check_environment()
        assert any("SECRET_KEY" in w for w in report.warnings), \
            "Default insecure SECRET_KEY should produce a warning"

    def test_valid_config_has_no_errors(self):
        from app.core.env_check import check_environment
        settings = self._make_settings()
        with patch("app.core.env_check.settings", settings):
            report = check_environment()
        assert report.errors == [], \
            f"Valid config should produce no errors, got: {report.errors}"

    def test_abort_on_errors_raises_runtime_error(self):
        from app.core.env_check import check_environment, log_env_report
        settings = self._make_settings(GROQ_API_KEY="", REDIS_URL="")
        with patch("app.core.env_check.settings", settings):
            report = check_environment()
        with pytest.raises(RuntimeError, match="Startup aborted"):
            log_env_report(report, abort_on_errors=True)

    def test_abort_on_errors_false_does_not_raise(self):
        from app.core.env_check import check_environment, log_env_report
        settings = self._make_settings(GROQ_API_KEY="", REDIS_URL="")
        with patch("app.core.env_check.settings", settings):
            report = check_environment()
        # Should not raise even with errors
        log_env_report(report, abort_on_errors=False)

    def test_report_is_valid_only_when_no_errors(self):
        from app.core.env_check import EnvReport
        ok = EnvReport(errors=[], warnings=["a warning"])
        bad = EnvReport(errors=["an error"], warnings=[])
        assert ok.is_valid is True
        assert bad.is_valid is False


# ══════════════════════════════════════════════════════════════════════════════
# 7. Health endpoints — source-level checks
# ══════════════════════════════════════════════════════════════════════════════

class TestHealthEndpointSource:

    @pytest.fixture(autouse=True)
    def content(self):
        self._content = read(HEALTH_ENDPOINT)

    def test_live_endpoint_defined(self):
        assert '"/live"' in self._content or "'/live'" in self._content or \
               "/live" in self._content, \
            "health.py should define a /live liveness endpoint"

    def test_ready_endpoint_defined(self):
        assert '"/ready"' in self._content or "'/ready'" in self._content or \
               "/ready" in self._content, \
            "health.py should define a /ready readiness endpoint"

    def test_live_endpoint_no_io(self):
        # The /live function should be short and make no DB/Redis calls
        src = self._content
        live_start = src.find("/live")
        if live_start == -1:
            pytest.skip("/live not found")
        # Find the function body — it should not call check_db or redis
        func_match = re.search(r'async def health_live.*?(?=\nasync def|\nclass |\Z)',
                                src[live_start:], re.DOTALL)
        if func_match:
            func_body = func_match.group(0)
            assert "check_db" not in func_body, \
                "/live probe must not call check_db — it should be instant"
            assert "_redis_ping" not in func_body, \
                "/live probe must not call _redis_ping — it should be instant"

    def test_ready_endpoint_checks_db_and_redis(self):
        src = self._content
        ready_start = src.find("/ready")
        if ready_start == -1:
            pytest.skip("/ready not found")
        func_match = re.search(r'async def health_ready.*?(?=\nasync def|\nclass |\Z)',
                                src[ready_start:], re.DOTALL)
        if func_match:
            func_body = func_match.group(0)
            assert "check_db" in func_body or "db_ok" in func_body, \
                "/ready probe should check database connectivity"
            assert "_redis_ping" in func_body or "redis_ok" in func_body, \
                "/ready probe should check Redis connectivity"

    def test_ready_returns_503_when_degraded(self):
        content = self._content
        assert "503" in content or "SERVICE_UNAVAILABLE" in content, \
            "Readiness probe should return 503 when DB or Redis is unreachable"


# ══════════════════════════════════════════════════════════════════════════════
# 8. Integration — env_check wired into main.py lifespan
# ══════════════════════════════════════════════════════════════════════════════

class TestMainEnvCheckIntegration:

    def test_env_check_imported_in_main(self):
        content = read(MAIN_MODULE)
        assert "env_check" in content, \
            "main.py lifespan should import and call app.core.env_check"

    def test_check_environment_called_in_lifespan(self):
        content = read(MAIN_MODULE)
        assert "check_environment" in content, \
            "main.py lifespan should call check_environment() on startup"

    def test_log_env_report_called_in_lifespan(self):
        content = read(MAIN_MODULE)
        assert "log_env_report" in content, \
            "main.py lifespan should call log_env_report() to surface warnings/errors"


# ══════════════════════════════════════════════════════════════════════════════
# 9. Health endpoint integration tests (via httpx TestClient)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestHealthEndpointsIntegration:
    """
    Integration tests for the /health/live and /health/ready endpoints.
    Uses the real FastAPI app with mocked DB and Redis dependencies.
    """

    @pytest.fixture
    async def client(self):
        from httpx import AsyncClient, ASGITransport
        from app.main import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            yield c

    @pytest.fixture
    def mock_env_check(self):
        """Prevent real env_check from erroring during test startup."""
        from app.core.env_check import EnvReport
        with patch("app.core.env_check.check_environment", return_value=EnvReport()):
            with patch("app.core.env_check.log_env_report"):
                yield

    @pytest.fixture
    def mock_bootstrap(self):
        with patch("app.main._bootstrap_admin", new=AsyncMock()):
            yield

    async def test_live_always_returns_200(self, client, mock_env_check, mock_bootstrap):
        resp = await client.get("/api/v1/health/live")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "version" in body

    async def test_ready_returns_200_when_db_and_redis_ok(
        self, client, mock_env_check, mock_bootstrap
    ):
        with patch("app.api.v1.endpoints.health.check_db_connection",
                   new=AsyncMock(return_value=True)):
            with patch("app.api.v1.endpoints.health._redis_ping",
                       return_value={"connected": True, "latency_ms": 1,
                                     "used_memory_mb": 1.0, "version": "7.0"}):
                resp = await client.get("/api/v1/health/ready")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ready"
        assert body["db"] == "connected"
        assert body["redis"] == "connected"

    async def test_ready_returns_503_when_db_unreachable(
        self, client, mock_env_check, mock_bootstrap
    ):
        with patch("app.api.v1.endpoints.health.check_db_connection",
                   new=AsyncMock(return_value=False)):
            with patch("app.api.v1.endpoints.health._redis_ping",
                       return_value={"connected": True, "latency_ms": 1,
                                     "used_memory_mb": None, "version": None}):
                resp = await client.get("/api/v1/health/ready")
        assert resp.status_code == 503
        assert resp.json()["status"] == "not_ready"
        assert resp.json()["db"] == "unreachable"

    async def test_ready_returns_503_when_redis_unreachable(
        self, client, mock_env_check, mock_bootstrap
    ):
        with patch("app.api.v1.endpoints.health.check_db_connection",
                   new=AsyncMock(return_value=True)):
            with patch("app.api.v1.endpoints.health._redis_ping",
                       return_value={"connected": False, "latency_ms": None,
                                     "used_memory_mb": None, "version": None}):
                resp = await client.get("/api/v1/health/ready")
        assert resp.status_code == 503
        assert resp.json()["status"] == "not_ready"
        assert resp.json()["redis"] == "unreachable"

    async def test_live_does_not_require_db(self, client, mock_env_check, mock_bootstrap):
        """Live probe must never call the DB — validate by patching DB to raise."""
        from sqlalchemy.exc import OperationalError

        async def broken_db():
            raise OperationalError("DB down", None, None)

        with patch("app.api.v1.endpoints.health.check_db_connection", new=broken_db):
            resp = await client.get("/api/v1/health/live")
        # Live must still return 200 even if DB is broken
        assert resp.status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# 9. .env.example completeness
# ══════════════════════════════════════════════════════════════════════════════

class TestEnvExample:

    @pytest.fixture(autouse=True)
    def content(self):
        self._content = read(ENV_EXAMPLE)

    REQUIRED_VARS: List[str] = [
        "SECRET_KEY",
        "ADMIN_USERNAME",
        "ADMIN_PASSWORD",
        "DATABASE_URL",
        "REDIS_URL",
        "CELERY_BROKER_URL",
        "CELERY_RESULT_BACKEND",
        "GROQ_API_KEY",
        "GROQ_MODEL",
        "GMAIL_CREDENTIALS_JSON",
        "GMAIL_TOKEN_JSON",
        "CALENDAR_CREDENTIALS_JSON",
        "CALENDAR_TOKEN_JSON",
        "AGENT_DEFAULT_TONE",
        "AGENT_DEFAULT_TIMEZONE",
        "AGENT_DEFAULT_BUDGET_CEILING",
        "INTENT_CONFIDENCE_THRESHOLD",
        "TASK_MAX_RETRIES_GMAIL",
        "TASK_DLQ_MAX_SIZE",
    ]

    @pytest.mark.parametrize("var", REQUIRED_VARS)
    def test_required_var_present(self, var: str):
        assert var in self._content, \
            f"backend/.env.example is missing the '{var}' variable"

    def test_agent_default_tone_is_formal(self):
        assert "AGENT_DEFAULT_TONE=formal" in self._content, \
            "AGENT_DEFAULT_TONE should default to 'formal' in .env.example (Phase 8)"

    def test_redis_url_points_to_localhost_for_local_dev(self):
        assert "localhost" in self._content, \
            ".env.example should show redis://localhost:6379/0 as the local dev Redis URL"

    def test_has_supabase_transaction_mode_hint(self):
        assert "6543" in self._content, \
            ".env.example should mention the Supabase transaction mode port (6543)"
