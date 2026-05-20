"""
Import all models here so that Base.metadata is fully populated.
This file must be imported by alembic/env.py before autogenerate runs,
and by the application lifespan before the first request is handled.
"""

from app.db.base import Base  # noqa: F401

# Domain models
from app.models.prospect import Prospect  # noqa: F401
from app.models.email_thread import EmailThread  # noqa: F401
from app.models.email_message import EmailMessage  # noqa: F401
from app.models.negotiation import Negotiation  # noqa: F401
from app.models.meeting import Meeting  # noqa: F401
from app.models.agent_config import AgentConfig  # noqa: F401
from app.models.agent_run import AgentRun  # noqa: F401

# Auth model
from app.models.user import User  # noqa: F401

__all__ = ["Base"]
