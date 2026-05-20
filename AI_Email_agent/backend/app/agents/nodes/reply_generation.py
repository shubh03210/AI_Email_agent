"""
Node: reply_generation
───────────────────────
Polishes the raw reply_instruction from any decision node into a
fully-formed email (subject + body) ready to send.

This node is the final "writer" in the graph.
It runs after: negotiation, scheduling, rescheduling, and
for shortcut intents: declined, ambiguous, curious.

Output keys added to state:
  reply_subject, reply_body
"""

from __future__ import annotations

from app.agents.prompts import (
    AGENT_PERSONA,
    REPLY_GENERATION_SYSTEM,
    REPLY_GENERATION_USER,
)
from app.agents.state import AgentState
from app.core.config import settings
from app.core.logging import logger
from app.services.llm_service import EmailReply, get_llm_service


async def reply_generation(state: AgentState) -> AgentState:
    """
    Generate a polished email reply using the reply_instruction set by
    the previous decision node.

    Reads:
        conversation_text, reply_instruction, thread_status, intent

    Writes:
        reply_subject, reply_body

    Fallback:
        If reply_instruction is missing, generates a safe generic follow-up.

    On error:
        Sets error + error_node and a minimal plaintext fallback reply.
    """
    thread_id = state.get("thread_id")
    conversation_text = state.get("conversation_text", "")
    reply_instruction = state.get("reply_instruction") or _default_instruction(state)
    intent = state.get("intent", "unknown")

    # Prefer config-driven values seeded by run_agent(); fall back to settings.
    from app.services.tone_service import get_tone_description
    tone = state.get("tone") or settings.AGENT_DEFAULT_TONE
    tone_desc = get_tone_description(tone)
    recruiter_name  = state.get("recruiter_name")  or "Alex"
    recruiter_title = state.get("recruiter_title") or "HR Recruiter"

    logger.info(
        f"[reply_generation] thread={thread_id} intent={intent} "
        f"tone={tone} recruiter={recruiter_name}"
    )

    try:
        llm = get_llm_service()
        result: EmailReply = await llm.async_generate_structured(
            system_prompt=REPLY_GENERATION_SYSTEM.format(
                persona=AGENT_PERSONA,
                tone=tone_desc,
                recruiter_name=recruiter_name,
                recruiter_title=recruiter_title,
            ),
            user_message=REPLY_GENERATION_USER.format(
                conversation_text=conversation_text,
                reply_instruction=reply_instruction,
            ),
            output_schema=EmailReply,
        )

        logger.info(
            f"[reply_generation] thread={thread_id} "
            f"subject='{result.subject}' tone={result.tone_used} "
            f"body_chars={len(result.body)}"
        )

        return {
            **state,
            "reply_subject": result.subject,
            "reply_body": result.body,
        }

    except Exception as exc:
        logger.exception(f"[reply_generation] thread={thread_id} failed")
        # Leave reply_body empty so send_reply skips this run entirely.
        # Sending a blind fallback email when the LLM is down (e.g. rate limit)
        # causes spam loops — it is safer to silently drop this cycle.
        return {
            **state,
            "reply_subject": "",
            "reply_body": "",
            "error": str(exc),
            "error_node": "reply_generation",
        }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _default_instruction(state: AgentState) -> str:
    """
    Build a generic follow-up instruction when reply_instruction was not set
    by any decision node (should not happen in normal flow).
    """
    intent = state.get("intent", "unknown")
    name = state.get("prospect_name", "the prospect")

    intent_map = {
        "interested": (
            f"Thank {name} for their interest and let them know you'll be in "
            "touch shortly with next steps."
        ),
        "curious": (
            f"Address {name}'s questions from the thread and offer to hop on a "
            "quick call if they'd like more detail."
        ),
        "declined": (
            f"Thank {name} for their time and wish them well. Leave the door open."
        ),
        "ambiguous": (
            f"Ask {name} one clear follow-up question to understand where they stand."
        ),
    }
    return intent_map.get(
        intent,
        (
            f"Send {name} a brief, professional follow-up acknowledging their "
            "last message and moving the conversation forward."
        ),
    )
