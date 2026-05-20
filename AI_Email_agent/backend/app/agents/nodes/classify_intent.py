"""
Node: classify_intent
──────────────────────
First node in the graph. Reads the conversation history and classifies
the intent of the prospect's latest email using the LLM.

Phase 5 hardening:
  - Confidence threshold guard: high-stakes intents (interested, negotiating)
    are downgraded to "ambiguous" when confidence < INTENT_CONFIDENCE_THRESHOLD.
  - Repeated ambiguity detection: each ambiguous result increments
    EmailThread.ambiguous_count; at AMBIGUOUS_ESCALATION_THRESHOLD the thread
    is flagged agent_escalated=True and a human-handoff reply is prepared.
  - State validation: intent is coerced through validate_intent() and
    confidence through validate_confidence() before any routing decision.
  - Non-ambiguous intent resets the ambiguous counter so stale counters
    don't trigger false escalations later.

Output keys added to state:
  intent              — one of the 7 valid intent labels
  intent_confidence   — 0.0–1.0 (validated and clamped)
  intent_reasoning    — one-sentence explanation
  reply_instruction   — pre-filled for non-interactive intents
  agent_escalated     — True if thread is now escalated (new or pre-existing)
  escalation_reason   — why escalation was triggered
  ambiguous_count     — updated consecutive-ambiguous counter
"""

from __future__ import annotations

from typing import Optional

from app.agents.prompts import (
    CLARIFICATION_USER,
    CLASSIFY_INTENT_SYSTEM,
    CLASSIFY_INTENT_USER,
    DECLINE_RESPONSE_USER,
)
from app.agents.state import AgentState
from app.agents.validation import (
    build_escalation_reply,
    should_downgrade_to_ambiguous,
    validate_confidence,
    validate_intent,
)
from app.core.config import settings
from app.core.logging import logger
from app.db.session import AsyncSessionLocal
from app.services.llm_service import IntentClassification, get_llm_service
from app.services.memory_service import increment_ambiguous_count, reset_ambiguous_count


async def classify_intent(state: AgentState) -> AgentState:
    """
    Classify the intent of the prospect's latest email.

    Reads:
        conversation_text, thread_id, prospect_name,
        agent_escalated (pre-existing), ambiguous_count (pre-existing)

    Writes:
        intent, intent_confidence, intent_reasoning,
        reply_instruction  (for declined / ambiguous / escalated shortcuts),
        agent_escalated, escalation_reason, ambiguous_count

    On error:
        Sets error + error_node, defaults intent to 'ambiguous'.
    """
    thread_id: int = state.get("thread_id")
    conversation_text: str = state.get("conversation_text", "")
    prospect_name: Optional[str] = state.get("prospect_name")

    logger.info(f"[classify_intent] thread={thread_id}")

    # ── Pre-existing escalation guard ────────────────────────────────────────
    # If the thread was already escalated (repeated ambiguity, API failure, etc.)
    # skip the LLM call and route straight to a human-handoff reply.
    if state.get("agent_escalated"):
        escalation_reason = state.get("escalation_reason") or "Thread flagged for human review."
        logger.warning(
            f"[classify_intent] thread={thread_id} already escalated — "
            "skipping LLM classification"
        )
        return {
            **state,
            "intent": "ambiguous",
            "intent_confidence": 0.0,
            "intent_reasoning": escalation_reason,
            "reply_instruction": build_escalation_reply(
                prospect_name=prospect_name,
                reason=escalation_reason,
            ),
        }

    # ── No conversation guard ─────────────────────────────────────────────────
    if not conversation_text or conversation_text == "(No messages yet)":
        logger.warning(f"[classify_intent] No conversation text for thread {thread_id}")
        return {
            **state,
            "intent": "ambiguous",
            "intent_confidence": 0.0,
            "intent_reasoning": "No conversation text available.",
            "error": "No conversation text found.",
            "error_node": "classify_intent",
        }

    try:
        # ── LLM classification ────────────────────────────────────────────────
        # Use the async wrapper so the event loop is not blocked during the
        # (potentially multi-second) Groq HTTP request.
        llm = get_llm_service()
        result: IntentClassification = await llm.async_generate_structured(
            system_prompt=CLASSIFY_INTENT_SYSTEM,
            user_message=CLASSIFY_INTENT_USER.format(
                conversation_text=conversation_text
            ),
            output_schema=IntentClassification,
        )

        # ── State validation ──────────────────────────────────────────────────
        intent = validate_intent(result.intent, thread_id=thread_id)
        confidence = validate_confidence(result.confidence, thread_id=thread_id)

        # ── Confidence threshold guard ────────────────────────────────────────
        # If the LLM returned a high-stakes intent with low confidence, it's
        # safer to treat it as ambiguous than to trigger irreversible actions
        # (calendar booking, counter-offer) on a weak signal.
        original_intent = intent
        if should_downgrade_to_ambiguous(intent, confidence, settings.INTENT_CONFIDENCE_THRESHOLD):
            logger.warning(
                f"[classify_intent] thread={thread_id} — downgrading "
                f"'{intent}' (confidence={confidence:.2f} < threshold="
                f"{settings.INTENT_CONFIDENCE_THRESHOLD}) → 'ambiguous'"
            )
            intent = "ambiguous"

        logger.info(
            f"[classify_intent] thread={thread_id} intent={intent} "
            f"confidence={confidence:.2f}"
            + (f" (downgraded from '{original_intent}')" if intent != original_intent else "")
        )

        updates: AgentState = {
            **state,
            "intent": intent,
            "intent_confidence": confidence,
            "intent_reasoning": result.reasoning,
        }

        # ── Ambiguity tracking + escalation ───────────────────────────────────
        if intent == "ambiguous":
            new_count, escalated = await _handle_ambiguous(
                thread_id,
                settings.AMBIGUOUS_ESCALATION_THRESHOLD,
            )
            updates["ambiguous_count"] = new_count

            if escalated:
                reason = (
                    f"Repeated ambiguous intent: {new_count} consecutive "
                    "classifications — forwarding to human operator."
                )
                updates["agent_escalated"] = True
                updates["escalation_reason"] = reason
                updates["reply_instruction"] = build_escalation_reply(
                    prospect_name=prospect_name,
                    reason=reason,
                )
                logger.warning(
                    f"[classify_intent] thread={thread_id} escalated after "
                    f"{new_count} ambiguous results"
                )
            else:
                # Standard ambiguous — ask for clarification
                updates["reply_instruction"] = CLARIFICATION_USER.format(
                    conversation_text=conversation_text
                )
        else:
            # Clear intent detected — reset the ambiguous counter
            await _reset_ambiguous(thread_id)
            updates["ambiguous_count"] = 0

            # Pre-fill reply_instruction for terminal intents
            if intent == "declined":
                updates["reply_instruction"] = DECLINE_RESPONSE_USER.format(
                    conversation_text=conversation_text
                )

        return updates

    except Exception as exc:
        logger.exception(f"[classify_intent] thread={thread_id} failed")
        return {
            **state,
            "intent": "ambiguous",
            "intent_confidence": 0.0,
            "intent_reasoning": f"Classification failed: {exc}",
            "error": str(exc),
            "error_node": "classify_intent",
        }


# ── Private helpers ────────────────────────────────────────────────────────────

async def _handle_ambiguous(thread_id: int, threshold: int) -> tuple[int, bool]:
    """Increment ambiguous_count in DB; returns (new_count, escalated)."""
    try:
        async with AsyncSessionLocal() as db:
            count, escalated = await increment_ambiguous_count(db, thread_id, threshold)
            await db.commit()
            return count, escalated
    except Exception as exc:
        logger.warning(
            f"[classify_intent] Could not update ambiguous_count for "
            f"thread={thread_id}: {exc}"
        )
        return 0, False


async def _reset_ambiguous(thread_id: int) -> None:
    """Reset ambiguous_count to 0 on a clear non-ambiguous intent."""
    try:
        async with AsyncSessionLocal() as db:
            await reset_ambiguous_count(db, thread_id)
            await db.commit()
    except Exception as exc:
        logger.warning(
            f"[classify_intent] Could not reset ambiguous_count for "
            f"thread={thread_id}: {exc}"
        )
