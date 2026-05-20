"""
Agent Observability
────────────────────
Reusable higher-order function that wraps any LangGraph node with
DB-backed AgentRun logging and structured console output.

Public API:
    with_observability(node_fn, node_name)
        → wrapped async node function with identical signature

Design principles:
  - Observability is a cross-cutting concern; zero logic lives inside nodes.
  - Each wrapped call opens its OWN DB session, independent of the node's
    business-logic session, so a DB failure in one cannot affect the other.
  - All observability exceptions are silently swallowed; they MUST NEVER
    crash or alter the outcome of the agent.
  - Input/output payloads are sanitised: no large blobs (conversation_text,
    available_slots, messages list) — only compact decision-relevant fields.

Phase 5 — Failure-safe execution:
  - Unhandled node exceptions are NO LONGER re-raised.  Instead, the wrapper
    catches them, records ERROR status in the AgentRun, and returns a safe
    error state with a fallback reply_instruction.
  - This ensures the LangGraph graph ALWAYS reaches reply_generation and
    send_reply, producing a graceful fallback reply rather than a silent
    crash.  One failing node cannot abort the entire conversation.
  - Escalation events (agent_escalated transitions) are captured in the
    AgentRun output snapshot so operators can audit them via the /logs API.

Usage (in graph.py):
    from app.agents.observability import with_observability
    g.add_node("classify_intent", with_observability(classify_intent, "classify_intent"))
"""

from __future__ import annotations

import time
from typing import Any, Callable, Optional

from app.agents.state import AgentState
from app.core.logging import logger
from app.db.session import AsyncSessionLocal
from app.models.agent_run import RunStatus
from app.services.memory_service import finish_agent_run, start_agent_run


# ── Public wrapper ─────────────────────────────────────────────────────────────

def with_observability(node_fn: Callable, node_name: str) -> Callable:
    """
    Return an async wrapper around *node_fn* that records an AgentRun row
    in the database for every execution.

    The wrapper:
      1. Opens an independent AsyncSession for observability writes.
      2. Inserts an AgentRun row with RUNNING status and sanitised input.
      3. Awaits the original node function.
      4. Updates the row with SUCCESS / ERROR status, latency, sanitised
         output, and (on error) the exception message.
      5. Closes the session unconditionally.

    Any exception from steps 1–2 or 4–5 is caught and logged as a WARNING;
    the agent result is never affected.
    """

    async def _wrapped(state: AgentState) -> AgentState:
        thread_id: int = state.get("thread_id", 0)
        run = None
        obs_db = None
        t0: float = time.perf_counter()

        # ── Open observability session + record start ──────────────────────
        try:
            obs_db = AsyncSessionLocal()
            run, t0 = await start_agent_run(
                obs_db,
                thread_id,
                node_name,
                _input_snapshot(state, node_name),
            )
        except Exception as start_exc:
            logger.warning(
                f"[observability] start failed for '{node_name}' "
                f"thread={thread_id}: {start_exc}"
            )
            # obs_db may be open even if start_agent_run raised — close it now
            # so we don't leak the connection for the rest of this node's run.
            if obs_db is not None:
                try:
                    await obs_db.close()
                except Exception:
                    pass
                obs_db = None

        # ── Execute original node ──────────────────────────────────────────
        result_state: AgentState = state
        final_status: str = RunStatus.SUCCESS.value
        error_msg: Optional[str] = None

        try:
            result_state = await node_fn(state)

            # Node caught its own exception and set error keys in state
            if (
                result_state.get("error_node") == node_name
                and result_state.get("error")
            ):
                final_status = RunStatus.ERROR.value
                error_msg = str(result_state["error"])

            # Capture escalation events in the audit trail
            if result_state.get("agent_escalated") and not state.get("agent_escalated"):
                logger.warning(
                    f"[observability] ESCALATION | node='{node_name}' "
                    f"thread={thread_id} "
                    f"reason='{result_state.get('escalation_reason')}'"
                )

        except Exception as node_exc:
            # ── Failure-safe: convert unhandled exception to error state ──
            # We do NOT re-raise.  Instead we produce a safe state that lets
            # the graph continue to reply_generation and send a fallback reply.
            # This prevents one misbehaving node from silently aborting an
            # entire conversation run.
            final_status = RunStatus.ERROR.value
            error_msg = f"{type(node_exc).__name__}: {node_exc}"
            logger.exception(
                f"[observability] Node '{node_name}' raised unexpectedly — "
                f"converting to safe error state (thread={thread_id})"
            )
            result_state = {
                **state,
                "error": error_msg,
                "error_node": node_name,
                "reply_instruction": (
                    "Let the prospect know their message was received and that "
                    "you'll follow up shortly. Keep it brief and apologetic."
                ),
            }

        # ── Finalise AgentRun record ───────────────────────────────────────
        if run is not None and obs_db is not None:
            try:
                output = _output_snapshot(state, result_state, node_name)
                if error_msg:
                    # Truncate to fit DB column (Text, generous but let's cap)
                    run.error_message = error_msg[:2000]

                await finish_agent_run(
                    obs_db,
                    run,
                    t0,
                    final_status,
                    output_payload=output or None,
                )
                await obs_db.commit()

                logger.info(
                    f"[{node_name}] run_id={run.id} status={final_status} "
                    f"latency={run.latency_ms}ms thread={thread_id}"
                )
            except Exception as fin_exc:
                logger.warning(
                    f"[observability] finish failed for '{node_name}' "
                    f"thread={thread_id}: {fin_exc}"
                )
            finally:
                # Unconditional close — obs_db is always open when run is not None
                try:
                    await obs_db.close()
                except Exception:
                    pass

        return result_state

    # Preserve the original function's name for LangGraph internal bookkeeping
    _wrapped.__name__ = node_fn.__name__
    _wrapped.__qualname__ = node_fn.__qualname__
    return _wrapped


# ── State snapshot helpers ─────────────────────────────────────────────────────

# Only the fields that are decision-relevant for each node.
# Deliberately excludes: conversation_text, messages, available_slots,
# reply_instruction, reply_body (can be large / verbose).

_NODE_INPUT_FIELDS: dict[str, list[str]] = {
    "classify_intent": [
        "thread_id", "thread_status", "intent",
        "agent_escalated", "ambiguous_count",
    ],
    "negotiation": [
        "thread_id", "max_budget", "current_offer",
        "counter_round", "negotiation_status",
    ],
    "scheduling": [
        "thread_id", "prospect_timezone",
        "meeting_status", "google_event_id",
    ],
    "rescheduling": [
        "thread_id", "prospect_timezone",
        "reschedule_count", "google_event_id", "meeting_status",
    ],
    "reply_generation": [
        "thread_id", "intent", "intent_confidence",
        "negotiation_action", "meeting_status",
    ],
    "send_reply": [
        "thread_id", "intent",
        "meeting_status", "negotiation_action", "reply_subject",
    ],
}

_NODE_OUTPUT_FIELDS: dict[str, list[str]] = {
    "classify_intent": [
        "intent", "intent_confidence", "intent_reasoning",
        "agent_escalated", "escalation_reason", "ambiguous_count",
        "error", "error_node",
    ],
    "negotiation": [
        "negotiation_action", "negotiation_proposed_amount",
        "negotiation_reasoning", "current_offer", "counter_round",
        "error", "error_node",
    ],
    "scheduling": [
        "google_event_id", "scheduled_at", "meeting_status",
        "selected_slot", "error", "error_node",
    ],
    "rescheduling": [
        "google_event_id", "scheduled_at", "meeting_status",
        "reschedule_count", "error", "error_node",
    ],
    "reply_generation": [
        "reply_subject", "error", "error_node",
    ],
    "send_reply": [
        "reply_sent", "error", "error_node",
    ],
}


def _input_snapshot(state: AgentState, node_name: str) -> dict[str, Any]:
    """Compact dict of the fields the node will READ."""
    keys = _NODE_INPUT_FIELDS.get(node_name, ["thread_id"])
    snapshot: dict[str, Any] = {}
    for k in keys:
        val = state.get(k)
        if val is not None:
            snapshot[k] = val

    # Include message count for classify_intent — avoids storing the full list
    if node_name == "classify_intent":
        snapshot["message_count"] = len(state.get("messages") or [])

    return snapshot


def _output_snapshot(
    before: AgentState, after: AgentState, node_name: str
) -> dict[str, Any]:
    """Compact dict of the fields the node WROTE (only changed/new values)."""
    keys = _NODE_OUTPUT_FIELDS.get(node_name, ["error", "error_node"])
    snapshot: dict[str, Any] = {}

    for k in keys:
        val = after.get(k)
        if val is None:
            continue
        # Truncate strings that can be long
        if isinstance(val, str) and len(val) > 500:
            snapshot[k] = val[:500] + "…"
        elif isinstance(val, float):
            snapshot[k] = round(val, 4)
        else:
            snapshot[k] = val

    # For reply_generation: include a preview of the body
    if node_name == "reply_generation":
        body = after.get("reply_body") or ""
        snapshot["reply_body_preview"] = (body[:200] + "…") if len(body) > 200 else body

    # For send_reply: include a preview of what was sent
    if node_name == "send_reply":
        body = after.get("reply_body") or before.get("reply_body") or ""
        snapshot["reply_body_preview"] = (body[:200] + "…") if len(body) > 200 else body

    return snapshot
