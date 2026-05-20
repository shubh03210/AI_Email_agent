"""
LangGraph Agent — Email Wake-Up Agent
───────────────────────────────────────
Full orchestration graph for the autonomous email agent.

Graph topology:

                         ┌─────────────────────────────────┐
                         │         classify_intent          │
                         └──────────────┬──────────────────┘
                                        │
          ┌─────────────────────────────┼─────────────────────────────┐
          │                             │                             │
     interested                   negotiating                   reschedule /
     (or after                         │                        unavailable
      accepted)                        ▼                             │
          │                     ┌──────────────┐                     ▼
          │                     │  negotiation  │           ┌─────────────────┐
          │                     └──────┬───────┘           │  rescheduling   │
          │                           │                     └────────┬────────┘
          │                  accept   │  counteroffer /              │
          │               ────────────┤    walkaway                  │
          │               │           │                              │
          ▼               ▼           ▼                              │
   ┌────────────┐  (falls through to reply_generation)              │
   │ scheduling  │◄─────────────────────────────────────────────────┘
   └─────┬──────┘
         │
         ▼
  ┌──────────────────┐
  │  reply_generation │◄── curious / ambiguous / declined (shortcut)
  └────────┬─────────┘
           │
           ▼
   ┌──────────────┐
   │  send_reply   │
   └──────┬───────┘
          │
          ▼
         END

Router decisions:
  classify_intent → route_after_intent()
    interested   → scheduling
    curious      → reply_generation
    negotiating  → negotiation
    declined     → reply_generation
    ambiguous    → reply_generation
    reschedule   → rescheduling
    unavailable  → rescheduling
    <error>      → END

  negotiation → route_after_negotiation()
    accept      → scheduling
    counteroffer → reply_generation
    walkaway    → reply_generation
    hold        → reply_generation
    <error>     → reply_generation   (safe fallback)

Public API:
  agent_graph.ainvoke(initial_state)   — async single run
  run_agent(thread_id, db)             — convenience wrapper that
                                         loads memory and invokes
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph

from app.agents.nodes.classify_intent import classify_intent
from app.agents.nodes.negotiation import negotiation
from app.agents.nodes.reply_generation import reply_generation
from app.agents.nodes.rescheduling import rescheduling
from app.agents.nodes.scheduling import scheduling
from app.agents.nodes.send_reply import send_reply
from app.agents.observability import with_observability
from app.agents.state import AgentState
from app.core.logging import logger
from app.models.email_thread import ThreadStatus


# ── Routing Functions ─────────────────────────────────────────────────────────

def route_after_intent(state: AgentState) -> str:
    """
    Route from classify_intent to the appropriate decision node.

    Phase 5 additions:
      - agent_escalated guard: if the thread is flagged for human review,
        skip all decision nodes and route directly to reply_generation,
        which uses the escalation reply_instruction already set by
        classify_intent.
      - Error guard: unchanged — classify_intent errors still route to
        reply_generation with a fallback instruction.
    """
    thread_id = state.get("thread_id")
    intent = (state.get("intent") or "ambiguous").lower()
    error = state.get("error")

    # ── Agent escalation short-circuit ────────────────────────────────────────
    # When agent_escalated=True the reply_instruction has already been set
    # to the human-handoff message by classify_intent.  Skip all automation.
    if state.get("agent_escalated"):
        logger.warning(
            f"[router] thread={thread_id} agent_escalated=True — "
            "routing directly to reply_generation (human handoff)"
        )
        return "reply_generation"

    # ── classify_intent error guard ───────────────────────────────────────────
    if error and state.get("error_node") == "classify_intent":
        logger.warning(
            f"[router] classify_intent errored — routing to reply_generation. "
            f"thread={thread_id}"
        )
        return "reply_generation"

    route_map = {
        "interested":   "scheduling",
        "curious":      "reply_generation",
        "negotiating":  "negotiation",
        "declined":     "reply_generation",
        "ambiguous":    "reply_generation",
        "reschedule":   "rescheduling",
        "unavailable":  "rescheduling",
    }
    destination = route_map.get(intent, "reply_generation")
    logger.info(
        f"[router] intent='{intent}' → '{destination}' "
        f"thread={thread_id}"
    )
    return destination


def route_after_negotiation(state: AgentState) -> str:
    """
    Route from the negotiation node.

    ACCEPT  → scheduling  (move to book the meeting)
    all others → reply_generation
    """
    action = (state.get("negotiation_action") or "hold").lower()

    # Override intent set by the negotiation node to 'interested' on accept
    if action == "accept" or state.get("intent") == "interested":
        logger.info(
            f"[router] negotiation accepted — routing to scheduling. "
            f"thread={state.get('thread_id')}"
        )
        return "scheduling"

    logger.info(
        f"[router] negotiation action='{action}' → reply_generation. "
        f"thread={state.get('thread_id')}"
    )
    return "reply_generation"


# ── Graph Construction ────────────────────────────────────────────────────────

def build_graph() -> Any:
    """
    Construct and compile the LangGraph StateGraph.

    Returns a compiled graph ready for .invoke() / .ainvoke().
    """
    g = StateGraph(AgentState)

    # Register all nodes — each wrapped with the observability layer that
    # records an AgentRun DB row (start / finish / latency / error) without
    # ever affecting the node's own business logic or DB transactions.
    g.add_node("classify_intent",  with_observability(classify_intent,  "classify_intent"))
    g.add_node("negotiation",      with_observability(negotiation,      "negotiation"))
    g.add_node("scheduling",       with_observability(scheduling,       "scheduling"))
    g.add_node("rescheduling",     with_observability(rescheduling,     "rescheduling"))
    g.add_node("reply_generation", with_observability(reply_generation, "reply_generation"))
    g.add_node("send_reply",       with_observability(send_reply,       "send_reply"))

    # Entry point
    g.set_entry_point("classify_intent")

    # classify_intent → (conditional) → one of 4 destinations
    g.add_conditional_edges(
        "classify_intent",
        route_after_intent,
        {
            "scheduling":       "scheduling",
            "negotiation":      "negotiation",
            "rescheduling":     "rescheduling",
            "reply_generation": "reply_generation",
        },
    )

    # negotiation → (conditional) → scheduling or reply_generation
    g.add_conditional_edges(
        "negotiation",
        route_after_negotiation,
        {
            "scheduling":       "scheduling",
            "reply_generation": "reply_generation",
        },
    )

    # All decision nodes converge at reply_generation → send_reply → END
    g.add_edge("scheduling",   "reply_generation")
    g.add_edge("rescheduling", "reply_generation")
    g.add_edge("reply_generation", "send_reply")
    g.add_edge("send_reply", END)

    compiled = g.compile()
    logger.info("[graph] Agent graph compiled successfully.")
    return compiled


# ── Singleton ─────────────────────────────────────────────────────────────────

agent_graph = build_graph()


# ── Convenience Runner ────────────────────────────────────────────────────────

async def run_agent(thread_id: int) -> AgentState:
    """
    Load memory for a thread and invoke the agent graph.

    Args:
        thread_id: DB primary key of the EmailThread to process.

    Returns:
        Final AgentState after the graph completes.

    Usage (from Celery task or API endpoint):
        from app.agents.graph import run_agent
        final_state = await run_agent(thread_id=42)
    """
    from app.db.session import AsyncSessionLocal
    from app.services.memory_service import load_thread_memory

    logger.info(f"[run_agent] Starting agent run for thread {thread_id}")

    from app.repositories.config_repo import get_or_create_default

    async with AsyncSessionLocal() as db:
        memory = await load_thread_memory(db, thread_id)
        agent_config = await get_or_create_default(db)

    if memory is None:
        logger.error(f"[run_agent] Thread {thread_id} not found — aborting.")
        return {
            "thread_id": thread_id,
            "error": f"Thread {thread_id} not found in database.",
            "error_node": "run_agent",
            "reply_sent": False,
        }

    # Do not re-run the agent once the thread is fully closed.
    # The thread reaches CLOSED when:
    #   • a meeting is confirmed or rescheduled   (scheduling / rescheduling nodes)
    #   • the prospect declined                   (reply_generation node)
    # Any new email from the prospect after that point should be treated as a
    # brand-new conversation and handled manually — not automatically replied to.
    if memory.thread_status == ThreadStatus.CLOSED.value:
        logger.info(
            f"[run_agent] Thread {thread_id} is CLOSED — skipping automated reply."
        )
        return {
            "thread_id": thread_id,
            "reply_sent": False,
            "intent": "closed",
            "error": None,
        }

    # Seed the initial state from the memory snapshot
    initial_state: AgentState = {
        "thread_id":          memory.thread_id,
        "gmail_thread_id":    memory.gmail_thread_id,
        "prospect_id":        memory.prospect_id,
        "prospect_name":      memory.prospect_name,
        "prospect_email":     memory.prospect_email,
        "prospect_timezone":  memory.prospect_timezone,
        "subject":            memory.subject,
        "thread_status":      memory.thread_status,
        "messages":           memory.messages,
        "conversation_text":  memory.conversation_text,
        # Negotiation memory — counter_round and last_prospect_offer are loaded
        # from the DB so walkaway logic accumulates correctly across emails.
        "negotiation_status":    memory.negotiation_status,
        "max_budget":            memory.max_budget,
        "current_offer":         memory.current_offer,
        "counter_round":         memory.counter_round,
        "previous_prospect_offer": memory.last_prospect_offer,
        # Agent-config budget ceiling overrides the DB max_budget fallback in
        # the negotiation node when no prior negotiation row exists.
        "budget_ceiling":        agent_config.budget_ceiling if agent_config else None,
        # Meeting memory
        "meeting_status":     memory.meeting_status,
        "google_event_id":    memory.google_event_id,
        "scheduled_at":       (
            memory.scheduled_at.isoformat() if memory.scheduled_at else None
        ),
        "reschedule_count":   memory.reschedule_count,
        # Calendar escalation (Phase 4)
        "needs_human_review":     memory.needs_human_review,
        "calendar_failure_count": memory.calendar_failure_count,
        # Agent escalation (Phase 5)
        "agent_escalated":        memory.agent_escalated,
        "escalation_reason":      memory.escalation_reason,
        "ambiguous_count":        memory.ambiguous_count,
        "thread_summary":         memory.thread_summary,
        # Flags
        "reply_sent":         False,
        "available_slots":    [],
        # Config-driven behaviour (Phase 8)
        "tone":                          agent_config.tone if agent_config else None,
        "recruiter_name":                agent_config.recruiter_name if agent_config else "Alex",
        "recruiter_title":               agent_config.recruiter_title if agent_config else "HR Recruiter",
        "recruiter_signature":           agent_config.recruiter_signature if agent_config else "",
        "meeting_confirmation_template": agent_config.meeting_confirmation_template if agent_config else None,
    }

    logger.info(
        f"[run_agent] Invoking graph | thread={thread_id} "
        f"messages={len(memory.messages)} "
        f"neg={memory.negotiation_status} meeting={memory.meeting_status}"
    )

    final_state: AgentState = await agent_graph.ainvoke(initial_state)

    logger.info(
        f"[run_agent] Completed | thread={thread_id} "
        f"intent={final_state.get('intent')} "
        f"reply_sent={final_state.get('reply_sent')} "
        f"error={final_state.get('error')}"
    )

    return final_state
