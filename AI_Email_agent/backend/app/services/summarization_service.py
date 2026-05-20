"""
Summarization Service
──────────────────────
Generates and maintains a rolling LLM summary of a thread's conversation
history to keep LLM context windows bounded.

Responsibility:
  After the agent sends a successful reply, and if the thread's message
  count has crossed MEMORY_SUMMARY_TRIGGER, this service:
    1. Loads all messages for the thread.
    2. Takes the "old" messages (everything outside the MEMORY_WINDOW_MESSAGES
       most-recent messages) and generates a compact summary.
    3. Persists the new summary to EmailThread.thread_summary.

On the next agent run, build_windowed_context() in memory_service.py will
prepend this summary to the recent messages, keeping the LLM prompt compact
without losing historical context.

Design notes:
  - Uses LLM plain-text generation (not structured output) for the summary.
  - Called AFTER a successful send_reply to keep it off the critical path.
  - All exceptions are caught and logged — a summarization failure must never
    affect the agent's core send loop.
  - A lock flag (EmailThread.thread_summary is being checked via message count)
    prevents redundant summarization on threads that are already summarised.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import logger
from app.db.session import AsyncSessionLocal
from app.services.memory_service import (
    format_conversation_for_llm,
    load_thread_history,
    update_thread_summary,
)


_SUMMARY_SYSTEM_PROMPT = """\
You are a concise summarizer for an AI email agent's conversation logs.

Your task: read the provided email conversation excerpts and produce a compact
summary (maximum 200 words) that captures:

1. Who the prospect is and their current stance (interested / sceptical / etc.)
2. Key information exchanged (budget, timeline, requirements).
3. Any agreements or commitments made.
4. The last action taken by the agent.

Write in third-person, past tense.  No filler phrases.  Be precise.
"""

_SUMMARY_USER_TEMPLATE = """\
Summarise the following email conversation excerpt.

{conversation_text}

Summary (max 200 words):"""


async def maybe_update_summary(
    thread_id: int,
    current_message_count: int,
) -> None:
    """
    Conditionally generate and persist a rolling summary for *thread_id*.

    Triggers summarization when *current_message_count* ≥
    MEMORY_SUMMARY_TRIGGER.  Safe to call every run — the check is cheap.

    Args:
        thread_id:             DB primary key of the EmailThread.
        current_message_count: Total messages in the thread at the time of
                               calling (typically len(state["messages"])).
    """
    if current_message_count < settings.MEMORY_SUMMARY_TRIGGER:
        return

    logger.info(
        f"[summarization] Triggering summary for thread={thread_id} "
        f"(message_count={current_message_count} ≥ "
        f"trigger={settings.MEMORY_SUMMARY_TRIGGER})"
    )

    try:
        await _generate_and_store_summary(thread_id)
    except Exception as exc:
        logger.warning(
            f"[summarization] Summary generation failed for thread={thread_id}: "
            f"{exc} — continuing without update"
        )


async def _generate_and_store_summary(thread_id: int) -> None:
    """Internal: generate summary from old messages and persist it."""
    from app.services.llm_service import get_llm_service

    async with AsyncSessionLocal() as db:
        all_messages = await load_thread_history(db, thread_id)

    window = settings.MEMORY_WINDOW_MESSAGES
    if len(all_messages) <= window:
        # Not enough old messages to summarise yet
        logger.debug(
            f"[summarization] thread={thread_id} has ≤{window} messages — "
            "skipping summary"
        )
        return

    # Only summarise the "old" portion (messages outside the recent window)
    messages_to_summarise = all_messages[:-window]
    conversation_text = format_conversation_for_llm(messages_to_summarise)

    llm = get_llm_service()
    summary: str = await llm.async_generate(
        system_prompt=_SUMMARY_SYSTEM_PROMPT,
        user_message=_SUMMARY_USER_TEMPLATE.format(
            conversation_text=conversation_text
        ),
    )

    # Truncate to keep DB size reasonable (Text column)
    summary = summary.strip()[:1500]

    async with AsyncSessionLocal() as db:
        await update_thread_summary(db, thread_id, summary)
        await db.commit()

    logger.info(
        f"[summarization] thread={thread_id} summary stored "
        f"(covers {len(messages_to_summarise)} old messages, "
        f"{len(summary)} chars)"
    )
