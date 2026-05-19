"""
Agent State
────────────
Single TypedDict that flows through every node of the LangGraph.

Lifecycle:
  1. Populated by load_thread_memory() before the graph is invoked.
  2. Each node reads what it needs and adds/updates its own keys.
  3. The final state is persisted by the send_reply node.

Key design rules:
  - All fields are Optional where a node might not have set them yet.
  - No mutable defaults — use None or plain literals.
  - Annotations use built-in types (list, dict) for Python 3.10+ compatibility.
"""

from __future__ import annotations

from typing import Optional
from typing_extensions import TypedDict


class AgentState(TypedDict, total=False):
    # ── Thread / Prospect Identity ────────────────────────────────────────────
    thread_id: int                  # DB primary key of EmailThread
    gmail_thread_id: str            # Gmail's opaque thread ID
    prospect_id: int                # DB primary key of Prospect
    prospect_name: str
    prospect_email: str
    prospect_timezone: str          # e.g. "Asia/Kolkata"
    subject: str                    # Email subject line
    thread_status: str              # ThreadStatus enum value

    # ── Conversation ──────────────────────────────────────────────────────────
    messages: list[dict]            # Raw message dicts from memory_service
    conversation_text: str          # Formatted text block fed to every LLM call

    # ── Intent Classification ─────────────────────────────────────────────────
    intent: Optional[str]           # interested / curious / negotiating /
                                    # declined / ambiguous / reschedule / unavailable
    intent_confidence: Optional[float]
    intent_reasoning: Optional[str]

    # ── Negotiation ───────────────────────────────────────────────────────────
    negotiation_status: Optional[str]       # NegotiationStatus value
    max_budget: Optional[float]             # Hard ceiling — NEVER sent to LLM prompt
    current_offer: Optional[float]          # Our last counter-offer
    negotiation_action: Optional[str]       # accept / counteroffer / walkaway / hold
    negotiation_proposed_amount: Optional[float]
    negotiation_reasoning: Optional[str]
    previous_prospect_offer: Optional[float]
    counter_round: int                      # defaults to 0

    # ── Scheduling / Calendar ─────────────────────────────────────────────────
    available_slots: list[dict]             # from calendar_service.get_free_slots()
    selected_slot: Optional[dict]           # the slot the agent chose
    meeting_status: Optional[str]           # MeetingStatus value
    google_event_id: Optional[str]
    scheduled_at: Optional[str]             # ISO-8601 string
    reschedule_count: int                   # defaults to 0

    # ── Reply Generation ──────────────────────────────────────────────────────
    reply_instruction: Optional[str]        # what to write (set by each decision node)
    reply_subject: Optional[str]            # final email subject
    reply_body: Optional[str]               # final email body
    reply_sent: bool                        # True after send_reply succeeds

    # ── Run Logging ───────────────────────────────────────────────────────────
    agent_run_id: Optional[int]             # DB ID of the current AgentRun row

    # ── Error Handling ────────────────────────────────────────────────────────
    error: Optional[str]
    error_node: Optional[str]               # which node raised the error
