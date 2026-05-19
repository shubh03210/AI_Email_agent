from langgraph.graph import StateGraph, END
from app.agents.state import AgentState
from app.agents.nodes.classify_intent import classify_intent
from app.agents.nodes.negotiation import negotiation
from app.agents.nodes.scheduling import scheduling
from app.agents.nodes.rescheduling import rescheduling
from app.agents.nodes.reply_generation import reply_generation


def build_graph() -> StateGraph:
    graph = StateGraph(AgentState)

    graph.add_node("classify_intent", classify_intent)
    graph.add_node("negotiation", negotiation)
    graph.add_node("scheduling", scheduling)
    graph.add_node("rescheduling", rescheduling)
    graph.add_node("reply_generation", reply_generation)

    graph.set_entry_point("classify_intent")

    graph.add_conditional_edges(
        "classify_intent",
        lambda state: state["intent"],
        {
            "negotiate": "negotiation",
            "schedule": "scheduling",
            "reschedule": "rescheduling",
        },
    )

    for node in ["negotiation", "scheduling", "rescheduling"]:
        graph.add_edge(node, "reply_generation")

    graph.add_edge("reply_generation", END)

    return graph.compile()


agent_graph = build_graph()
