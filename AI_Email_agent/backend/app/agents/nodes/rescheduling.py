from app.agents.state import AgentState


async def rescheduling(state: AgentState) -> AgentState:
    # TODO: handle rescheduling an existing meeting via calendar_service
    return {**state, "proposed_times": []}
