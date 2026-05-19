from app.agents.state import AgentState


async def classify_intent(state: AgentState) -> AgentState:
    # TODO: call LLM with CLASSIFY_INTENT_PROMPT to determine intent
    return {**state, "intent": "schedule"}
