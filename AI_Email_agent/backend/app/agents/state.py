from typing import TypedDict, Optional, List


class AgentState(TypedDict):
    thread_id: str
    prospect_id: int
    messages: List[dict]
    intent: Optional[str]
    negotiation_stage: Optional[str]
    proposed_times: Optional[List[str]]
    confirmed_time: Optional[str]
    reply_draft: Optional[str]
    error: Optional[str]
