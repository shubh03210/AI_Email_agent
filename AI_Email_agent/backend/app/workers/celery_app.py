"""
Celery Application
───────────────────
Configures the Celery app, task queues, routing, and beat schedule.

Queues:
  default  — general tasks (cleanup, misc)
  agent    — AI agent runs (CPU/IO heavy, can scale independently)
  gmail    — Gmail polling + email sending (rate-limited by Gmail API)

Beat Schedule:
  poll_inbox          — runs every GMAIL_POLL_INTERVAL_SECONDS (default 60s)
  cleanup_old_logs    — runs daily at 02:00 UTC
"""

from celery import Celery
from celery.schedules import crontab
from kombu import Exchange, Queue

from app.core.config import settings

# ── App ───────────────────────────────────────────────────────────────────────

celery_app = Celery(
    "email_agent",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.workers.tasks"],
)

# ── Serialisation ─────────────────────────────────────────────────────────────

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # Store results for 24 h then expire
    result_expires=86400,
    # Acknowledge task only after it completes (prevents silent loss on crash)
    task_acks_late=True,
    # Don't prefetch more than 1 task per worker slot (prevents starvation)
    worker_prefetch_multiplier=1,
)

# ── Queues & Routing ──────────────────────────────────────────────────────────

default_exchange = Exchange("default", type="direct")
agent_exchange   = Exchange("agent",   type="direct")
gmail_exchange   = Exchange("gmail",   type="direct")

celery_app.conf.task_queues = (
    Queue("default", default_exchange, routing_key="default"),
    Queue("agent",   agent_exchange,   routing_key="agent"),
    Queue("gmail",   gmail_exchange,   routing_key="gmail"),
)

celery_app.conf.task_default_queue        = "default"
celery_app.conf.task_default_exchange     = "default"
celery_app.conf.task_default_routing_key  = "default"

celery_app.conf.task_routes = {
    "app.workers.tasks.run_agent_task":     {"queue": "agent",   "routing_key": "agent"},
    "app.workers.tasks.send_outreach_task": {"queue": "gmail",   "routing_key": "gmail"},
    "app.workers.tasks.poll_inbox_task":    {"queue": "gmail",   "routing_key": "gmail"},
    "app.workers.tasks.cleanup_old_logs":   {"queue": "default", "routing_key": "default"},
}

# ── Beat Schedule ─────────────────────────────────────────────────────────────

celery_app.conf.beat_schedule = {
    "poll-inbox": {
        "task":     "app.workers.tasks.poll_inbox_task",
        "schedule": settings.GMAIL_POLL_INTERVAL_SECONDS,  # seconds
        "options":  {"queue": "gmail"},
    },
    "cleanup-old-logs": {
        "task":     "app.workers.tasks.cleanup_old_logs",
        "schedule": crontab(hour=2, minute=0),             # daily at 02:00 UTC
        "options":  {"queue": "default"},
    },
}
