from app.agents.state import AgentState


async def scheduling(state: AgentState) -> AgentState:
    # TODO: fetch available slots from calendar_service and propose them
    return {**state, "proposed_times": []}
