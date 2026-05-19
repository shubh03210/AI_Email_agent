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

from datetime import datetime
from datetime import timezone as dt_timezone

from app.agents.state import AgentState
from app.core.logging import logger
from app.db.session import AsyncSessionLocal
from app.models.email_thread import ThreadStatus
from app.services.gmail_service import GmailService
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

    logger.info(f"[send_reply] thread={thread_id} intent={intent}")

    # Guard: nothing to send
    if not reply_body:
        logger.warning(f"[send_reply] Empty reply_body — skipping send. thread={thread_id}")
        return {**state, "reply_sent": False}

    # Guard: upstream hard failure (error set, intent never resolved)
    if error and intent == "unknown":
        logger.warning(
            f"[send_reply] Skipping send due to upstream error: {error}. "
            f"thread={thread_id}"
        )
        return {**state, "reply_sent": False}

    try:
        # 1 — Send via Gmail
        gmail = GmailService()
        gmail.send_reply(
            to=prospect_email,
            subject=reply_subject,
            body=reply_body,
            thread_id=gmail_thread_id,
        )
        logger.info(
            f"[send_reply] Email sent to {prospect_email} | thread={thread_id}"
        )

        # 2 — Persist outbound message
        async with AsyncSessionLocal() as db:
            await save_message(
                db=db,
                thread_id=thread_id,
                sender="agent",
                body=reply_body,
                timestamp=datetime.now(dt_timezone.utc),
                intent=None,
            )

            # 3 — Update thread status
            new_status = _resolve_thread_status(state)
            await update_thread_status(db, thread_id, new_status)
            await db.commit()

        logger.info(
            f"[send_reply] Message saved + thread status → '{new_status}' "
            f"| thread={thread_id}"
        )

        return {**state, "reply_sent": True}

    except Exception as exc:
        logger.error(
            f"[send_reply] thread={thread_id} send failed: {exc}", exc_info=True
        )
        return {
            **state,
            "reply_sent": False,
            "error": str(exc),
            "error_node": "send_reply",
        }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _resolve_thread_status(state: AgentState) -> str:
    """
    Determine the new thread status after the reply is sent.

    Logic:
      - declined intent or walkaway negotiation action → CLOSED
      - scheduling / rescheduling intents → ACTIVE (ongoing)
      - everything else → WAITING (awaiting prospect reply)
    """
    intent = state.get("intent", "")
    negotiation_action = state.get("negotiation_action", "")
    meeting_status = state.get("meeting_status", "")

    if intent == "declined" or negotiation_action == "walkaway":
        return ThreadStatus.CLOSED.value

    if meeting_status in ("confirmed", "rescheduled"):
        return ThreadStatus.ACTIVE.value

    return ThreadStatus.WAITING.value
