"""
Startup Environment Checks
───────────────────────────
Called once during application lifespan startup.

Validates that required secrets and configuration values are present and
sensible *before* the first request is handled.

Design:
  - Missing CRITICAL values → ValueError (aborts startup)
  - Missing IMPORTANT values → logged WARNING (app continues, degraded)
  - Insecure defaults → logged WARNING with remediation hint

This is separate from pydantic field_validators (which validate value format)
— this module validates deployment readiness (are secrets set? are files
present? are external services reachable?).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

from app.core.config import settings
from app.core.logging import logger

_PLACEHOLDER_PATTERNS = {
    "your-",
    "replace-",
    "change-me",
    "<",
    "example",
    "placeholder",
}

_INSECURE_SECRET_DEFAULTS = {
    "change-me-in-production-use-secrets",
    "change-me",
    "secret",
    "supersecret",
    "dev-secret",
}


@dataclass
class EnvReport:
    errors:   List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0


def _looks_like_placeholder(value: str) -> bool:
    lower = value.lower()
    return any(p in lower for p in _PLACEHOLDER_PATTERNS)


def check_environment() -> EnvReport:
    """
    Run all deployment-readiness checks and return a report.

    Does NOT raise — callers decide what to do with errors vs warnings.
    """
    report = EnvReport()

    # ── GROQ API key ──────────────────────────────────────────────────────────
    if not settings.GROQ_API_KEY:
        report.errors.append(
            "GROQ_API_KEY is not set. The agent cannot generate replies without an LLM. "
            "Get a free key at https://console.groq.com/ and add it to backend/.env."
        )
    elif _looks_like_placeholder(settings.GROQ_API_KEY):
        report.warnings.append(
            "GROQ_API_KEY looks like a placeholder — verify you have set a real API key."
        )

    # ── Database URL ──────────────────────────────────────────────────────────
    db_url = settings.DATABASE_URL
    if not db_url or db_url == "postgresql+asyncpg://postgres:postgres@postgres:5432/email_agent":
        report.warnings.append(
            "DATABASE_URL is using the default placeholder. "
            "Update DATABASE_URL in backend/.env to point to your Supabase (or other) database."
        )
    elif _looks_like_placeholder(db_url):
        report.warnings.append(
            "DATABASE_URL appears to contain placeholder values — verify the connection string."
        )

    # ── Redis URL ─────────────────────────────────────────────────────────────
    if not settings.REDIS_URL:
        report.errors.append(
            "REDIS_URL is not set. Celery task queuing will not function."
        )

    # ── JWT secret ────────────────────────────────────────────────────────────
    if settings.SECRET_KEY in _INSECURE_SECRET_DEFAULTS:
        report.warnings.append(
            "SECRET_KEY is using an insecure default value. "
            "Run: python -c \"import secrets; print(secrets.token_hex(32))\" "
            "and set SECRET_KEY in backend/.env before deploying to production."
        )

    # ── Admin password ────────────────────────────────────────────────────────
    if settings.ADMIN_PASSWORD in {"change-me", "password", "admin123", "admin",
                                    "change-me-in-production-use-a-strong-password"}:
        report.warnings.append(
            "ADMIN_PASSWORD is using a default/weak value. "
            "Set a strong unique password in ADMIN_PASSWORD before deploying."
        )

    # ── Gmail credentials file ────────────────────────────────────────────────
    gmail_creds = settings.GMAIL_CREDENTIALS_JSON
    if not os.path.isabs(gmail_creds):
        gmail_creds = os.path.join(os.getcwd(), gmail_creds)
    if not os.path.exists(gmail_creds):
        report.warnings.append(
            f"Gmail credentials file not found: {settings.GMAIL_CREDENTIALS_JSON}. "
            "The Gmail integration will fail until you add this file. "
            "See README.md → Google OAuth Setup."
        )

    # ── Calendar credentials file ─────────────────────────────────────────────
    cal_creds = settings.CALENDAR_CREDENTIALS_JSON
    if not os.path.isabs(cal_creds):
        cal_creds = os.path.join(os.getcwd(), cal_creds)
    if not os.path.exists(cal_creds):
        report.warnings.append(
            f"Calendar credentials file not found: {settings.CALENDAR_CREDENTIALS_JSON}. "
            "The Google Calendar integration will fail until you add this file. "
            "See README.md → Google OAuth Setup."
        )

    return report


def log_env_report(report: EnvReport, *, abort_on_errors: bool = False) -> None:
    """
    Write the environment report to the structured logger.

    Args:
        report:           Output of check_environment().
        abort_on_errors:  When True, raises RuntimeError if any errors exist.
                          Defaults to False so the app always starts (degraded
                          functionality is better than a crash loop for
                          incremental setups).
    """
    for msg in report.warnings:
        logger.warning(f"[env-check] WARNING: {msg}")

    for msg in report.errors:
        logger.error(f"[env-check] ERROR: {msg}")

    if report.is_valid and not report.warnings:
        logger.info("[env-check] All environment checks passed.")
    elif report.is_valid:
        logger.warning(
            f"[env-check] Environment check complete with {len(report.warnings)} warning(s). "
            "The application will start but some features may be unavailable."
        )
    else:
        logger.error(
            f"[env-check] Environment check found {len(report.errors)} critical error(s). "
            "Review the errors above and update backend/.env."
        )

    if abort_on_errors and not report.is_valid:
        raise RuntimeError(
            f"Startup aborted: {len(report.errors)} environment configuration error(s). "
            "Check the log for details."
        )
