"""
Lazy imports to avoid graph compilation at module load time.
Use: from app.agents.graph import agent_graph, run_agent
"""


def get_agent_graph():
    from app.agents.graph import agent_graph
    return agent_graph


def get_run_agent():
    from app.agents.graph import run_agent
    return run_agent


__all__ = ["get_agent_graph", "get_run_agent"]
