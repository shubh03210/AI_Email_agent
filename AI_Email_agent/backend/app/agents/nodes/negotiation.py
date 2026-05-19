from app.agents.state import AgentState


async def negotiation(state: AgentState) -> AgentState:
    # TODO: handle negotiation logic using LLM and negotiation_service
    return {**state, "negotiation_stage": "counter_offer"}
