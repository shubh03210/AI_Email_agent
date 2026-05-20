from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_SUPPORTED_ALGORITHMS = {"HS256", "HS384", "HS512"}
_INSECURE_SECRET_DEFAULTS = {
    "change-me-in-production-use-secrets",
    "change-me",
    "secret",
    "supersecret",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore",
    )

    # ── Application ────────────────────────────────────────────────────────────
    PROJECT_NAME: str = "Email Wake-Up Agent"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    # ── Security / JWT ─────────────────────────────────────────────────────────
    SECRET_KEY: str = "change-me-in-production-use-secrets"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # Bootstrap admin credentials (used once on first startup when users table is empty)
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "change-me-in-production-use-a-strong-password"

    # ── CORS ───────────────────────────────────────────────────────────────────
    CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://frontend:3000",
    ]

    # ── Database ───────────────────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@postgres:5432/email_agent"

    # ── Redis / Celery ─────────────────────────────────────────────────────────
    REDIS_URL: str = "redis://redis:6379/0"
    CELERY_BROKER_URL: str = "redis://redis:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://redis:6379/0"

    # ── LLM ────────────────────────────────────────────────────────────────────
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.3-70b-versatile"
    LLM_TEMPERATURE: float = 0.3
    LLM_MAX_TOKENS: int = 1024

    # ── Gmail OAuth ────────────────────────────────────────────────────────────
    GMAIL_CREDENTIALS_JSON: str = "credentials/gmail_credentials.json"
    GMAIL_TOKEN_JSON: str = "credentials/gmail_token.json"
    GMAIL_SCOPES: List[str] = [
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.modify",
    ]
    GMAIL_POLL_INTERVAL_SECONDS: int = 60

    # ── Google Calendar OAuth ──────────────────────────────────────────────────
    CALENDAR_CREDENTIALS_JSON: str = "credentials/calendar_credentials.json"
    CALENDAR_TOKEN_JSON: str = "credentials/calendar_token.json"
    CALENDAR_SCOPES: List[str] = [
        "https://www.googleapis.com/auth/calendar",
    ]

    # ── Agent Defaults ─────────────────────────────────────────────────────────
    AGENT_DEFAULT_TONE: str = "professional"
    AGENT_DEFAULT_TIMEZONE: str = "UTC"
    AGENT_DEFAULT_WORKING_HOURS_START: int = 9
    AGENT_DEFAULT_WORKING_HOURS_END: int = 18
    AGENT_DEFAULT_BUDGET_CEILING: float = 5000.0
    AGENT_MAX_RESCHEDULE_ATTEMPTS: int = 3

    # ── Calendar Reliability ───────────────────────────────────────────────────
    # Number of consecutive calendar operation failures before a meeting is
    # flagged for human review (needs_human_review=True on the Meeting row).
    CALENDAR_MAX_FAILURES: int = 3

    # ── LangGraph Hardening ────────────────────────────────────────────────────
    # Minimum confidence below which classify_intent downgrades high-stakes
    # intents (interested, negotiating) to "ambiguous" to avoid acting on weak
    # signals.  Range: 0.0–1.0.
    INTENT_CONFIDENCE_THRESHOLD: float = 0.45

    # Consecutive "ambiguous" classifications before the thread is escalated
    # to human review (agent_escalated=True on the EmailThread row).
    AMBIGUOUS_ESCALATION_THRESHOLD: int = 3

    # Number of most-recent messages included verbatim in the LLM context
    # window.  Older messages are replaced by the thread_summary.
    MEMORY_WINDOW_MESSAGES: int = 20

    # Minimum total message count that triggers a rolling summary update
    # after a successful reply send.  Avoids summarising very short threads.
    MEMORY_SUMMARY_TRIGGER: int = 25

    # ── Task Infrastructure (Phase 6) ─────────────────────────────────────────
    # Per-task-class maximum retry counts.
    # Retry delays use exponential backoff: base * 2^attempt, capped at max.
    TASK_MAX_RETRIES_GMAIL:   int = 5      # Gmail/outreach tasks
    TASK_MAX_RETRIES_AGENT:   int = 3      # LangGraph agent runs
    TASK_MAX_RETRIES_DEFAULT: int = 3      # Cleanup + misc tasks
    TASK_RETRY_BACKOFF_BASE:  int = 30     # seconds — initial backoff interval
    TASK_RETRY_BACKOFF_CAP:   int = 600    # seconds — maximum backoff ceiling

    # Dead-letter queue: maximum entries kept in the Redis DLQ list.
    # Older entries are evicted (LTRIM) when the cap is reached.
    TASK_DLQ_MAX_SIZE: int = 1000

    # Outreach idempotency lock TTL (seconds).
    # Prevents duplicate outreach emails when a task is retried after the Gmail
    # call succeeded but the DB write failed.  24 h is enough for any realistic
    # retry window.
    TASK_OUTREACH_IDEM_TTL: int = 86400   # 24 hours

    # Agent enqueue deduplication TTL (seconds).
    # Prevents successive poll cycles from re-enqueueing an agent run for a
    # thread that already has one queued or running.
    TASK_AGENT_ENQUEUE_TTL: int = 360     # 6 minutes (> soft_time_limit of 5m)

    # ── Validators ────────────────────────────────────────────────────────────

    @field_validator("SECRET_KEY")
    @classmethod
    def validate_secret_key(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError(
                "SECRET_KEY must be at least 32 characters long. "
                "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        if v in _INSECURE_SECRET_DEFAULTS:
            import warnings
            warnings.warn(
                "SECRET_KEY is using an insecure default value — "
                "set a strong random key via the SECRET_KEY environment variable before deploying.",
                UserWarning,
                stacklevel=2,
            )
        return v

    @field_validator("ALGORITHM")
    @classmethod
    def validate_algorithm(cls, v: str) -> str:
        if v not in _SUPPORTED_ALGORITHMS:
            raise ValueError(
                f"ALGORITHM must be one of {sorted(_SUPPORTED_ALGORITHMS)}, got {v!r}"
            )
        return v

    @field_validator("ACCESS_TOKEN_EXPIRE_MINUTES")
    @classmethod
    def validate_expire_minutes(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("ACCESS_TOKEN_EXPIRE_MINUTES must be a positive integer")
        return v

    @field_validator("ADMIN_PASSWORD")
    @classmethod
    def validate_admin_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("ADMIN_PASSWORD must be at least 8 characters")
        return v


settings = Settings()
