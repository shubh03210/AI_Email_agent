"""
Agent State Validation
───────────────────────
Utility functions for validating and coercing AgentState values at node
boundaries.

Design principles:
  - Functions are pure and stateless — no DB calls, no side effects.
  - Every function returns a safe value; they NEVER raise.
  - All coercions are logged at WARNING level so anomalies are visible.
  - Validation is cheap — called at the top of each node, not in hot paths.

Public API:
  validate_intent(intent)          → str  (coerced to valid intent label)
  validate_confidence(confidence)  → float  (clamped to [0.0, 1.0])
  check_required_fields(state, fields, node_name)  → str | None (error msg)
  is_high_stakes_intent(intent)    → bool
  build_escalation_reply(reason)   → str  (human-handoff reply instruction)
"""

from __future__ import annotations

from typing import Any, Optional

from app.core.logging import logger


# ── Constants ──────────────────────────────────────────────────────────────────

# The complete set of valid intent labels that classify_intent can output.
VALID_INTENTS: frozenset[str] = frozenset({
    "interested",
    "curious",
    "negotiating",
    "declined",
    "ambiguous",
    "reschedule",
    "unavailable",
})

# Intents where acting on a low-confidence signal causes real harm:
#   - "interested" → triggers calendar booking
#   - "negotiating" → triggers counter-offer logic
# For these, a confidence below threshold → downgrade to "ambiguous".
HIGH_STAKES_INTENTS: frozenset[str] = frozenset({
    "interested",
    "negotiating",
})

# Human-handoff reply instruction template.
_ESCALATION_REPLY_TEMPLATE = (
    "Please let {name} know that their message has been received and that "
    "a team member will personally follow up within one business day to assist. "
    "Apologise for any uncertainty and keep the tone warm and professional."
)


# ── Intent validation ──────────────────────────────────────────────────────────

def validate_intent(intent: Optional[str], thread_id: Optional[int] = None) -> str:
    """
    Coerce *intent* to a known label.

    - Strips whitespace and lowercases.
    - Returns the original value if it is already valid.
    - Falls back to "ambiguous" for None, empty strings, or unknown labels.

    Never raises.
    """
    if not intent:
        return "ambiguous"

    try:
        normalised = str(intent).strip().lower()
    except Exception:
        return "ambiguous"
    if normalised in VALID_INTENTS:
        return normalised

    logger.warning(
        f"[validation] Unknown intent '{intent}' for thread={thread_id} — "
        "coercing to 'ambiguous'"
    )
    return "ambiguous"


# ── Confidence validation ──────────────────────────────────────────────────────

def validate_confidence(
    confidence: Any,
    thread_id: Optional[int] = None,
) -> float:
    """
    Coerce *confidence* to a float clamped to [0.0, 1.0].

    Returns 0.0 for None or non-numeric values.
    Never raises.
    """
    if confidence is None:
        return 0.0
    try:
        value = float(confidence)
    except (TypeError, ValueError):
        logger.warning(
            f"[validation] Non-numeric confidence '{confidence}' "
            f"for thread={thread_id} — defaulting to 0.0"
        )
        return 0.0

    clamped = max(0.0, min(1.0, value))
    if clamped != value:
        logger.warning(
            f"[validation] Confidence {value} out of range "
            f"for thread={thread_id} — clamped to {clamped}"
        )
    return clamped


# ── Required field check ──────────────────────────────────────────────────────

def check_required_fields(
    state: dict,
    required: list[str],
    node_name: str,
) -> Optional[str]:
    """
    Verify that all *required* keys are present and non-None in *state*.

    Returns None if all fields are present.
    Returns a human-readable error string listing the missing fields.
    Never raises.
    """
    missing = [f for f in required if state.get(f) is None]
    if not missing:
        return None

    msg = f"[{node_name}] Missing required fields: {missing}"
    logger.error(msg + f" (thread={state.get('thread_id')})")
    return msg


# ── Intent classification helpers ─────────────────────────────────────────────

def is_high_stakes_intent(intent: str) -> bool:
    """Return True if acting on this intent without sufficient confidence is risky."""
    return intent.lower() in HIGH_STAKES_INTENTS


def should_downgrade_to_ambiguous(
    intent: str,
    confidence: float,
    threshold: float,
) -> bool:
    """
    Return True if a low-confidence high-stakes intent should be downgraded
    to "ambiguous" to avoid acting on a weak signal.

    Only applies to HIGH_STAKES_INTENTS; other intents are always allowed
    to pass through regardless of confidence (they have safe fallback paths).
    """
    return is_high_stakes_intent(intent) and confidence < threshold


# ── Escalation reply ──────────────────────────────────────────────────────────

def build_escalation_reply(
    prospect_name: Optional[str] = None,
    reason: Optional[str] = None,
) -> str:
    """
    Build the reply_instruction string used when a thread is escalated to a
    human operator.

    The generated instruction is passed to reply_generation, which writes a
    polished email using this as guidance.
    """
    name = prospect_name or "the prospect"
    base = _ESCALATION_REPLY_TEMPLATE.format(name=name)

    if reason:
        logger.info(f"[validation] Escalation reply built | reason={reason}")

    return base
