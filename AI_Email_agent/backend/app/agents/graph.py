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
from app.agents.state import AgentState
from app.core.logging import logger


# ── Routing Functions ─────────────────────────────────────────────────────────

def route_after_intent(state: AgentState) -> str:
    """
    Route from classify_intent to the appropriate decision node.

    Returns a node name (or END on hard errors).
    """
    intent = (state.get("intent") or "ambiguous").lower()
    error = state.get("error")

    # If the error came from classify_intent itself and we have no intent,
    # short-circuit to reply_generation with the fallback instruction.
    if error and state.get("error_node") == "classify_intent":
        logger.warning(
            f"[router] classify_intent errored — routing to reply_generation. "
            f"thread={state.get('thread_id')}"
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
        f"thread={state.get('thread_id')}"
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

    # Register all nodes
    g.add_node("classify_intent", classify_intent)
    g.add_node("negotiation",     negotiation)
    g.add_node("scheduling",      scheduling)
    g.add_node("rescheduling",    rescheduling)
    g.add_node("reply_generation", reply_generation)
    g.add_node("send_reply",      send_reply)

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

    async with AsyncSessionLocal() as db:
        memory = await load_thread_memory(db, thread_id)

    if memory is None:
        logger.error(f"[run_agent] Thread {thread_id} not found — aborting.")
        return {
            "thread_id": thread_id,
            "error": f"Thread {thread_id} not found in database.",
            "error_node": "run_agent",
            "reply_sent": False,
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
        # Negotiation memory
        "negotiation_status":  memory.negotiation_status,
        "max_budget":          memory.max_budget,
        "current_offer":       memory.current_offer,
        "counter_round":       0,
        # Meeting memory
        "meeting_status":     memory.meeting_status,
        "google_event_id":    memory.google_event_id,
        "scheduled_at":       (
            memory.scheduled_at.isoformat() if memory.scheduled_at else None
        ),
        "reschedule_count":   memory.reschedule_count,
        # Flags
        "reply_sent":         False,
        "available_slots":    [],
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
