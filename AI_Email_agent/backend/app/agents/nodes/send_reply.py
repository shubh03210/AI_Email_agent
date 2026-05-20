"""
Node: send_reply
─────────────────
Final node in the graph. Sends the generated email and persists
the outbound message to the database.

Flow:
  1. Guards: skip if reply_body is empty or an error should suppress sending.
  2. Sends the email via gmail_service.
  3. Saves the outbound message to DB via memory_service.
  4. Updates the thread status in DB.
  5. Sets reply_sent = True.

Output keys added to state:
  reply_sent  (True on success, False on failure/skip)
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from datetime import timezone as dt_timezone

from app.agents.state import AgentState
from app.core.logging import logger
from app.db.session import AsyncSessionLocal
from app.models.email_thread import ThreadStatus
from app.models.prospect import ProspectStatus
from app.services.gmail_service import reply_to_thread
from app.services.memory_service import save_message, update_thread_status


async def send_reply(state: AgentState) -> AgentState:
    """
    Send the reply email and record it in the database.

    Reads:
        thread_id, gmail_thread_id, prospect_email,
        reply_subject, reply_body, intent, error

    Writes:
        reply_sent

    Skip conditions (reply is NOT sent):
      - reply_body is empty
      - a critical error occurred in an earlier node and intent couldn't be resolved
      - intent is 'declined' AND negotiation_action is not set (i.e. pure decline,
        we still send the graceful exit)

    Thread status transitions:
      declined / walkaway      → CLOSED
      reschedule / scheduling  → ACTIVE (meeting still in play)
      all others               → WAITING (awaiting prospect response)
    """
    thread_id = state.get("thread_id")
    gmail_thread_id = state.get("gmail_thread_id", "")
    prospect_email = state.get("prospect_email", "")
    reply_subject = state.get("reply_subject") or state.get("subject", "Re: Following up")
    reply_body = state.get("reply_body", "").strip()
    intent = state.get("intent", "unknown")
    error = state.get("error")

    # Append recruiter signature (Phase 8) — injected after the LLM body.
    recruiter_signature = (state.get("recruiter_signature") or "").strip()
    if recruiter_signature and reply_body:
        reply_body = f"{reply_body}\n\n{recruiter_signature}"

    logger.info(f"[send_reply] thread={thread_id} intent={intent}")

    # Guard: nothing to send
    if not reply_body:
        logger.warning(f"[send_reply] Empty reply_body — skipping send. thread={thread_id}")
        return {**state, "reply_sent": False}

    # Guard: upstream LLM failure — skip if either intent classification or
    # reply generation errored out (reply_body would be empty/junk).
    error_node = state.get("error_node", "")
    if error and error_node in ("classify_intent", "reply_generation", "run_agent"):
        logger.warning(
            f"[send_reply] Skipping send — LLM error in {error_node!r}: {error}. "
            f"thread={thread_id}"
        )
        return {**state, "reply_sent": False}

    try:
        # 1 — Send via Gmail; capture the returned SentMessage so we can
        # persist the Gmail message_id for idempotency and audit purposes.
        # reply_to_thread uses a synchronous Google API client — run it in a
        # thread pool worker so the event loop is not blocked.
        sent_msg = await asyncio.to_thread(
            reply_to_thread,
            thread_id=gmail_thread_id,
            to=prospect_email,
            subject=reply_subject,
            body=reply_body,
        )
        gmail_sent_msg_id: str | None = sent_msg.message_id if sent_msg else None
        logger.info(
            f"[send_reply] Email sent to {prospect_email} | "
            f"thread={thread_id} gmail_msg_id={gmail_sent_msg_id}"
        )

        # 2 — Persist outbound message + update thread & prospect statuses
        async with AsyncSessionLocal() as db:
            await save_message(
                db=db,
                thread_id=thread_id,
                sender="agent",
                body=reply_body,
                timestamp=datetime.now(dt_timezone.utc),
                intent=None,
                gmail_message_id=gmail_sent_msg_id,
            )

            # 3 — Update thread status
            new_thread_status = _resolve_thread_status(state)
            await update_thread_status(db, thread_id, new_thread_status)

            # 4 — Update prospect status to reflect conversation progress
            prospect_id = state.get("prospect_id")
            if prospect_id:
                new_prospect_status = _resolve_prospect_status(state)
                if new_prospect_status:
                    from sqlalchemy import select
                    from app.models.prospect import Prospect
                    result = await db.execute(
                        select(Prospect).where(Prospect.id == prospect_id)
                    )
                    prospect = result.scalar_one_or_none()
                    if prospect and prospect.status != new_prospect_status:
                        prospect.status = new_prospect_status
                        db.add(prospect)
                        logger.info(
                            f"[send_reply] Prospect {prospect_id} status "
                            f"→ '{new_prospect_status}'"
                        )

            await db.commit()

        logger.info(
            f"[send_reply] Message saved + thread status → '{new_thread_status}' "
            f"| thread={thread_id}"
        )

        # ── Rolling summary (Phase 5) ─────────────────────────────────────
        # If the thread has grown long enough, generate a compact LLM summary
        # of the older messages so future runs use a windowed context instead
        # of the full unbounded history.  This runs AFTER the reply is sent
        # and committed, so it never blocks the critical send path.
        message_count = len(state.get("messages") or [])
        await _maybe_update_summary(thread_id, message_count + 1)

        return {**state, "reply_sent": True}

    except Exception as exc:
        logger.exception(f"[send_reply] thread={thread_id} send failed")
        return {
            **state,
            "reply_sent": False,
            "error": str(exc),
            "error_node": "send_reply",
        }


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _maybe_update_summary(thread_id: int, message_count: int) -> None:
    """Trigger rolling summary if the thread has grown long enough."""
    try:
        from app.services.summarization_service import maybe_update_summary
        await maybe_update_summary(thread_id, message_count)
    except Exception as exc:
        logger.warning(
            f"[send_reply] Rolling summary update failed for thread={thread_id}: "
            f"{exc} — continuing without update"
        )


def _resolve_thread_status(state: AgentState) -> str:
    """
    Determine the new thread status after the reply is sent.

    Terminal (CLOSED) — no more automated replies ever:
      • Meeting confirmed or rescheduled  → meeting is booked, job done
      • Prospect declined                 → conversation over
      • Negotiation walkaway              → we ended the negotiation

    Active (WAITING) — waiting for the next prospect reply:
      • Everything else
    """
    intent = state.get("intent", "")
    negotiation_action = state.get("negotiation_action", "")
    meeting_status = state.get("meeting_status", "")

    if (
        meeting_status in ("confirmed", "rescheduled", "cancelled")
        or intent == "declined"
        or negotiation_action == "walkaway"
    ):
        return ThreadStatus.CLOSED.value

    return ThreadStatus.WAITING.value


def _resolve_prospect_status(state: AgentState) -> str | None:
    """
    Map the current conversation state to a ProspectStatus value.

    Returns None when no status change is warranted.
    """
    intent = state.get("intent", "")
    negotiation_action = state.get("negotiation_action", "")
    meeting_status = state.get("meeting_status", "")

    if meeting_status in ("confirmed", "rescheduled"):
        return ProspectStatus.SCHEDULED.value

    if intent == "declined" or negotiation_action == "walkaway":
        return ProspectStatus.DECLINED.value

    if intent == "negotiating":
        return ProspectStatus.NEGOTIATING.value

    if intent in ("interested", "curious", "reschedule", "unavailable"):
        return ProspectStatus.INTERESTED.value

    return None
