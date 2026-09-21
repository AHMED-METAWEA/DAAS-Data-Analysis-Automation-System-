"""
LangGraph StateGraph for the planning stage only.

Profiler → Planner → END

Returns the cleaning plan and metadata for human review.
The user can then run the cleaning graph with the (possibly edited) plan.
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from agents.cleaning.planner import planner_node
from agents.cleaning.profiler import profiler_node
from core.state import GraphState


def build_planner_graph():
    """
    Construct and compile the planning-only StateGraph.

    Returns:
        A compiled LangGraph runnable.
    """
    graph = StateGraph(GraphState)

    graph.add_node("profiler", profiler_node)
    graph.add_node("planner", planner_node)

    graph.set_entry_point("profiler")
    graph.add_edge("profiler", "planner")
    graph.add_edge("planner", END)

    return graph.compile()
