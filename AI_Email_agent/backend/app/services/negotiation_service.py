"""
Negotiation Service
────────────────────
Pure business logic for offer evaluation, counter-offer strategy, and walkaway decisions.

Responsibilities:
  - Evaluate if a prospect's offer is acceptable
  - Generate intelligent counter-offers within budget ceiling
  - Decide when to walk away politely
  - Track negotiation state transitions
  - Persist negotiation state to DB
  - Produce human-readable negotiation summaries for LLM context

This service contains ZERO LLM calls.
All LLM-based decisions live in llm_service.py.
This service enforces business rules and DB persistence.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import logger
from app.models.negotiation import Negotiation, NegotiationStatus


# ── Constants ─────────────────────────────────────────────────────────────────

MAX_COUNTER_ROUNDS = 3          # Walk away after this many failed counters
MIN_OFFER_STEP_PERCENT = 0.05   # Minimum move per counter (5% of max budget)
SPLIT_DIFF_RATIO = 0.5          # Split the difference 50/50 on counter


# ── Result Types ──────────────────────────────────────────────────────────────

class NegotiationAction(str, Enum):
    ACCEPT      = "accept"
    COUNTEROFFER = "counteroffer"
    WALKAWAY    = "walkaway"
    HOLD        = "hold"


@dataclass
class NegotiationResult:
    action: NegotiationAction
    proposed_amount: Optional[float]    # set when action is COUNTEROFFER
    reasoning: str
    walkaway_reason: Optional[str]      # set when action is WALKAWAY
    updated_status: str                 # the new NegotiationStatus value


# ── Pure Business Logic ───────────────────────────────────────────────────────

def evaluate_offer(
    prospect_offer: float,
    max_budget: float,
    current_offer: Optional[float],
    counter_round: int,
    previous_prospect_offer: Optional[float] = None,
) -> NegotiationResult:
    """
    Evaluate a prospect's offer against budget constraints and negotiation history.

    Rules (in priority order):
    1. If prospect_offer <= max_budget → ACCEPT immediately.
    2. If counter_round >= MAX_COUNTER_ROUNDS → WALKAWAY.
    3. If prospect's new ask >= previous ask (not moving down) → WALKAWAY.
    4. If first counter → split the difference between max_budget and prospect_offer.
    5. Otherwise → move our counter toward the prospect's new number.

    Args:
        prospect_offer:          The dollar amount the prospect is currently asking for.
        max_budget:              The hard ceiling — NEVER expose or exceed this.
        current_offer:           Our last counter-offer (None on the first round).
        counter_round:           Number of counter rounds already completed.
        previous_prospect_offer: The prospect's previous ask (None on first round).
                                 Used to detect if the prospect is moving at all.

    Returns:
        NegotiationResult with action and all context needed to draft a reply.
    """
    # Rule 1: Within budget → accept
    if prospect_offer <= max_budget:
        return NegotiationResult(
            action=NegotiationAction.ACCEPT,
            proposed_amount=prospect_offer,
            reasoning=(
                f"Prospect's offer ${prospect_offer:,.0f} is within "
                f"budget ceiling of ${max_budget:,.0f}."
            ),
            walkaway_reason=None,
            updated_status=NegotiationStatus.ACCEPTED.value,
        )

    # Rule 2: Too many rounds → walk away
    if counter_round >= MAX_COUNTER_ROUNDS:
        return NegotiationResult(
            action=NegotiationAction.WALKAWAY,
            proposed_amount=None,
            reasoning=(
                f"After {counter_round} counter rounds, prospect's offer "
                f"${prospect_offer:,.0f} still exceeds our max budget of ${max_budget:,.0f}."
            ),
            walkaway_reason=(
                f"We've reached our maximum budget of ${max_budget:,.0f} "
                "and unfortunately cannot go higher at this time."
            ),
            updated_status=NegotiationStatus.WALKAWAY.value,
        )

    # Rule 3: Prospect not moving → walk away
    if (
        previous_prospect_offer is not None
        and prospect_offer >= previous_prospect_offer
    ):
        return NegotiationResult(
            action=NegotiationAction.WALKAWAY,
            proposed_amount=None,
            reasoning=(
                f"Prospect's new ask ${prospect_offer:,.0f} is not lower than "
                f"their previous ask ${previous_prospect_offer:,.0f}. Walking away."
            ),
            walkaway_reason=(
                f"Unfortunately our budget is firm at ${max_budget:,.0f} "
                "and we're unable to bridge the gap. "
                "We'd love to reconnect if your situation changes."
            ),
            updated_status=NegotiationStatus.WALKAWAY.value,
        )

    # Rule 4 / 5: Counter-offer
    if current_offer is None:
        # First counter: split the difference, capped at max_budget
        counter = _split_difference(max_budget, prospect_offer)
        reasoning = (
            f"First counter: splitting the difference between our max "
            f"${max_budget:,.0f} and prospect's ask ${prospect_offer:,.0f} "
            f"→ proposing ${counter:,.0f}."
        )
    else:
        # Prospect moved down — meet them closer to their new number
        counter = _split_difference(max_budget, prospect_offer)
        counter = max(counter, current_offer)   # never step backward
        counter = min(counter, max_budget)       # hard cap
        reasoning = (
            f"Prospect moved to ${prospect_offer:,.0f}. "
            f"Moving our counter from ${current_offer:,.0f} → ${counter:,.0f}."
        )

    return NegotiationResult(
        action=NegotiationAction.COUNTEROFFER,
        proposed_amount=round(counter, 2),
        reasoning=reasoning,
        walkaway_reason=None,
        updated_status=NegotiationStatus.ACTIVE.value,
    )


def _split_difference(our_max: float, their_ask: float) -> float:
    """
    Calculate a counter-offer by splitting the difference.
    We offer (our_max + their_ask) / 2, capped at our_max.
    """
    split = (our_max + their_ask) / 2
    return min(split, our_max)


def should_accept(offer: float, max_budget: float) -> bool:
    """Quick check — is an offer within budget?"""
    return offer <= max_budget


def should_walkaway(counter_round: int) -> bool:
    """Quick check — have we exhausted our counter rounds?"""
    return counter_round >= MAX_COUNTER_ROUNDS


def format_negotiation_context(negotiation: Negotiation) -> str:
    """
    Build a concise negotiation context string for the LLM prompt.
    Gives the LLM everything it needs to draft the right response.
    """
    lines = [
        f"Negotiation Status: {negotiation.status}",
        f"Maximum Budget (NEVER reveal): ${negotiation.max_budget:,.0f}",
    ]
    if negotiation.current_offer is not None:
        lines.append(f"Our Last Counter-Offer: ${negotiation.current_offer:,.0f}")
    else:
        lines.append("Our Last Counter-Offer: None (first round)")

    return "\n".join(lines)


# ── DB Persistence ────────────────────────────────────────────────────────────

async def get_or_create_negotiation(
    db: AsyncSession,
    thread_id: int,
    max_budget: Optional[float] = None,
) -> Negotiation:
    """
    Fetch the negotiation record for a thread, or create one if it doesn't exist.

    Args:
        db:         Async DB session.
        thread_id:  Email thread DB ID.
        max_budget: Budget ceiling — used only when creating a new record.
                    Falls back to AGENT_DEFAULT_BUDGET_CEILING from config.

    Returns:
        Negotiation ORM instance.
    """
    result = await db.execute(
        select(Negotiation).where(Negotiation.thread_id == thread_id)
    )
    negotiation = result.scalar_one_or_none()

    if negotiation is None:
        budget = max_budget or settings.AGENT_DEFAULT_BUDGET_CEILING
        negotiation = Negotiation(
            thread_id=thread_id,
            max_budget=budget,
            current_offer=None,
            status=NegotiationStatus.PENDING.value,
        )
        db.add(negotiation)
        await db.flush()
        logger.info(
            f"Created negotiation record for thread {thread_id} "
            f"| max_budget=${budget:,.0f}"
        )

    return negotiation


async def record_counter_offer(
    db: AsyncSession,
    negotiation: Negotiation,
    result: NegotiationResult,
) -> Negotiation:
    """
    Persist the outcome of an evaluate_offer() call to the DB.

    Updates:
      - current_offer (if action is COUNTEROFFER or ACCEPT)
      - status (reflects the new NegotiationStatus)

    Args:
        db:          Async DB session.
        negotiation: The Negotiation ORM instance to update.
        result:      The NegotiationResult from evaluate_offer().

    Returns:
        Updated Negotiation instance.
    """
    if result.proposed_amount is not None:
        negotiation.current_offer = result.proposed_amount

    negotiation.status = result.updated_status
    db.add(negotiation)
    await db.flush()

    logger.info(
        f"Negotiation {negotiation.id} updated | "
        f"action={result.action.value} | "
        f"offer={negotiation.current_offer} | "
        f"status={negotiation.status}"
    )
    return negotiation


async def get_negotiation_by_thread(
    db: AsyncSession,
    thread_id: int,
) -> Optional[Negotiation]:
    """Fetch a negotiation record by thread ID. Returns None if not found."""
    result = await db.execute(
        select(Negotiation).where(Negotiation.thread_id == thread_id)
    )
    return result.scalar_one_or_none()


async def mark_negotiation_accepted(
    db: AsyncSession,
    negotiation: Negotiation,
    final_amount: float,
) -> Negotiation:
    """Mark a negotiation as accepted at the final agreed amount."""
    negotiation.current_offer = final_amount
    negotiation.status = NegotiationStatus.ACCEPTED.value
    db.add(negotiation)
    await db.flush()
    logger.info(
        f"Negotiation {negotiation.id} ACCEPTED at ${final_amount:,.0f}"
    )
    return negotiation


async def mark_negotiation_walkaway(
    db: AsyncSession,
    negotiation: Negotiation,
) -> Negotiation:
    """Mark a negotiation as walked away."""
    negotiation.status = NegotiationStatus.WALKAWAY.value
    db.add(negotiation)
    await db.flush()
    logger.info(f"Negotiation {negotiation.id} marked as WALKAWAY.")
    return negotiation
