"""
Node: classify_intent
──────────────────────
First node in the graph. Reads the conversation history and classifies
the intent of the prospect's latest email using the LLM.

Output keys added to state:
  intent              — one of the 7 intent labels
  intent_confidence   — 0.0–1.0
  intent_reasoning    — one-sentence explanation
  reply_instruction   — pre-filled for non-interactive intents (declined, ambiguous)
"""

from __future__ import annotations

from app.agents.prompts import (
    CLARIFICATION_USER,
    CLASSIFY_INTENT_SYSTEM,
    CLASSIFY_INTENT_USER,
    DECLINE_RESPONSE_USER,
)
from app.agents.state import AgentState
from app.core.logging import logger
from app.services.llm_service import IntentClassification, get_llm_service


async def classify_intent(state: AgentState) -> AgentState:
    """
    Classify the intent of the prospect's latest email.

    Reads:
        conversation_text

    Writes:
        intent, intent_confidence, intent_reasoning
        reply_instruction  (for declined / ambiguous shortcuts)

    On error:
        Sets error + error_node, defaults intent to 'ambiguous'.
    """
    thread_id = state.get("thread_id")
    conversation_text = state.get("conversation_text", "")

    logger.info(f"[classify_intent] thread={thread_id}")

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
        llm = get_llm_service()
        result: IntentClassification = llm.generate_structured(
            system_prompt=CLASSIFY_INTENT_SYSTEM,
            user_message=CLASSIFY_INTENT_USER.format(
                conversation_text=conversation_text
            ),
            output_schema=IntentClassification,
        )

        intent = result.intent.lower().strip()
        logger.info(
            f"[classify_intent] thread={thread_id} intent={intent} "
            f"confidence={result.confidence:.2f}"
        )

        updates: AgentState = {
            **state,
            "intent": intent,
            "intent_confidence": result.confidence,
            "intent_reasoning": result.reasoning,
        }

        # Pre-fill reply_instruction for terminal intents so they can
        # skip decision nodes and go straight to reply_generation.
        if intent == "declined":
            updates["reply_instruction"] = DECLINE_RESPONSE_USER.format(
                conversation_text=conversation_text
            )
        elif intent == "ambiguous":
            updates["reply_instruction"] = CLARIFICATION_USER.format(
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
