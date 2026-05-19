from app.agents.state import AgentState


async def reply_generation(state: AgentState) -> AgentState:
    # TODO: use LLM with REPLY_GENERATION_PROMPT to finalize the email draft
    return {**state, "reply_draft": ""}
