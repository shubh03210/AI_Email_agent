"""
Node: negotiation
──────────────────
Handles offer evaluation and counter-offer strategy.

Flow:
  1. Loads (or creates) the Negotiation DB record.
  2. Calls negotiation_service.evaluate_offer() for pure business logic.
  3. Persists the result to DB.
  4. Sets reply_instruction for the reply_generation node.
  5. If action is ACCEPT → also queues scheduling by setting intent to 'interested'
     so the router forwards to the scheduling node next.

Output keys added to state:
  negotiation_status, negotiation_action, negotiation_proposed_amount,
  negotiation_reasoning, current_offer, reply_instruction
  (intent may be overridden to 'interested' on ACCEPT)
"""

from __future__ import annotations

from app.agents.prompts import NEGOTIATION_USER
from app.agents.state import AgentState
from app.core.logging import logger
from app.db.session import AsyncSessionLocal
from app.services import negotiation_service as neg_svc
from app.services.negotiation_service import (
    NegotiationAction,
    format_negotiation_context,
)


async def negotiation(state: AgentState) -> AgentState:
    """
    Evaluate a prospect's offer and decide the next negotiation move.

    Reads:
        thread_id, conversation_text, max_budget, current_offer,
        counter_round, previous_prospect_offer, messages

    Writes:
        negotiation_status, negotiation_action, negotiation_proposed_amount,
        negotiation_reasoning, current_offer, reply_instruction
        (may override intent to 'interested' when offer is accepted)

    On error:
        Sets error + error_node, defaults to 'hold' action.
    """
    thread_id = state.get("thread_id")
    conversation_text = state.get("conversation_text", "")
    max_budget: float = state.get("max_budget") or 5000.0
    current_offer = state.get("current_offer")
    counter_round: int = state.get("counter_round") or 0
    previous_prospect_offer = state.get("previous_prospect_offer")

    logger.info(
        f"[negotiation] thread={thread_id} max_budget={max_budget} "
        f"current_offer={current_offer} round={counter_round}"
    )

    # Extract the prospect's latest offered amount from the last message's
    # raw_payload or fall back to current_offer (best-effort parse).
    prospect_offer = _extract_prospect_offer(state, max_budget)

    try:
        async with AsyncSessionLocal() as db:
            # Ensure negotiation record exists
            neg = await neg_svc.get_or_create_negotiation(
                db, thread_id, max_budget=max_budget
            )

            # Pure business logic
            result = neg_svc.evaluate_offer(
                prospect_offer=prospect_offer,
                max_budget=max_budget,
                current_offer=current_offer,
                counter_round=counter_round,
                previous_prospect_offer=previous_prospect_offer,
            )

            # Persist
            neg = await neg_svc.record_counter_offer(db, neg, result)
            await db.commit()

        logger.info(
            f"[negotiation] thread={thread_id} action={result.action.value} "
            f"proposed={result.proposed_amount}"
        )

        # Build reply instruction
        proposed_amount_line = (
            f"Proposed counter-offer amount: ${result.proposed_amount:,.2f}"
            if result.proposed_amount is not None
            else ""
        )
        reply_instruction = NEGOTIATION_USER.format(
            conversation_text=conversation_text,
            negotiation_context=_build_context_text(neg, result),
            action=result.action.value,
            proposed_amount_line=proposed_amount_line,
            reasoning=result.reasoning,
        )

        updates: AgentState = {
            **state,
            "negotiation_status": result.updated_status,
            "negotiation_action": result.action.value,
            "negotiation_proposed_amount": result.proposed_amount,
            "negotiation_reasoning": result.reasoning,
            "current_offer": result.proposed_amount or current_offer,
            "counter_round": counter_round + 1,
            "reply_instruction": reply_instruction,
        }

        # If accepted → move to scheduling
        if result.action == NegotiationAction.ACCEPT:
            logger.info(
                f"[negotiation] Offer accepted — routing to scheduling. thread={thread_id}"
            )
            updates["intent"] = "interested"

        return updates

    except Exception as exc:
        logger.exception(f"[negotiation] thread={thread_id} failed")
        return {
            **state,
            "negotiation_action": "hold",
            "reply_instruction": (
                f"Acknowledge the prospect's offer and let them know you'll "
                f"need a moment to check internally. Keep it brief and warm."
            ),
            "error": str(exc),
            "error_node": "negotiation",
        }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_prospect_offer(state: AgentState, fallback: float) -> float:
    """
    Best-effort extraction of the prospect's offered dollar amount.

    Priority:
      1. 'negotiation_proposed_amount' already set in state (e.g. from a previous
         partial run or explicit API input).
      2. The last message's raw_payload['amount'] if present (custom field).
      3. Fall back to fallback (max_budget), which will trigger ACCEPT.
    """
    if state.get("negotiation_proposed_amount") is not None:
        return float(state["negotiation_proposed_amount"])

    messages: list[dict] = state.get("messages") or []
    for msg in reversed(messages):
        raw = msg.get("raw_payload") or {}
        if "amount" in raw:
            try:
                return float(raw["amount"])
            except (ValueError, TypeError):
                pass

    # No explicit amount found — default to slightly over max_budget so the
    # business logic runs the first counter-offer round naturally.
    return fallback * 1.2


def _build_context_text(neg: object, result: object) -> str:
    """Build a plain-text negotiation context block for the LLM prompt."""
    lines = [
        f"Status: {getattr(result, 'updated_status', 'active')}",
        f"Our Last Counter-Offer: "
        + (
            f"${getattr(result, 'proposed_amount', 0):,.0f}"
            if getattr(result, "proposed_amount", None)
            else "None"
        ),
        f"Decision: {getattr(result, 'action', 'hold')}",
    ]
    if getattr(result, "walkaway_reason", None):
        lines.append(f"Walkaway Note: {result.walkaway_reason}")
    return "\n".join(lines)
