from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # ── Security ───────────────────────────────────────────────────────────────
    SECRET_KEY: str = "change-me-in-production-use-secrets"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # ── CORS ───────────────────────────────────────────────────────────────────
    CORS_ORIGINS: List[str] = [
        "http://localhost:8501",
        "http://frontend:8501",
        "http://localhost:3000",
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


settings = Settings()
