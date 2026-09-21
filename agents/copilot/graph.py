"""Analyst Copilot supervisor graph — FEATURE_ROADMAP.md B1.

Same LangGraph convention as ``agents/visualization/viz_graph.py``: a
``TypedDict`` state, plain ``(state) -> dict`` node functions returning
partial updates, ``add_conditional_edges`` keyed off a router function,
compiled via ``graph.compile()``.

One tool per turn (no chaining): ``router -> <one tool> -> narrate -> END``,
or ``router -> clarify -> END`` directly when intent is ambiguous (zero
extra LLM calls). Each tool node self-handles its own failures, so no
retry loop is needed here.

A follow-up turn — the user asking the copilot to explain/clarify its own
last answer rather than posing a new question — skips tool execution
entirely: ``router -> explain -> END``, reusing the previous turn's cached
payload (see ``backend/app/services/copilot_sessions.py``) instead of
re-running a tool or looping back through `narrate`.
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from .narrate import _clarify, _explain, _narrate
from .router import _route
from .state import CopilotState
from .tools import (
    _run_churn,
    _run_forecasting,
    _run_general,
    _run_insights,
    _run_marketing,
    _run_visualization,
)


def _route_to_tool(state: CopilotState) -> str:
    return state.get("route", "general")


def build_copilot_graph():
    graph = StateGraph(CopilotState)
    graph.add_node("router", _route)
    graph.add_node("clarify", _clarify)
    graph.add_node("explain", _explain)
    graph.add_node("visualization", _run_visualization)
    graph.add_node("insights", _run_insights)
    graph.add_node("forecasting", _run_forecasting)
    graph.add_node("marketing", _run_marketing)
    graph.add_node("churn", _run_churn)
    graph.add_node("general", _run_general)
    graph.add_node("narrate", _narrate)

    graph.set_entry_point("router")
    graph.add_conditional_edges(
        "router",
        _route_to_tool,
        {
            "clarify": "clarify",
            "follow_up": "explain",
            "visualization": "visualization",
            "insights": "insights",
            "forecasting": "forecasting",
            "marketing": "marketing",
            "churn": "churn",
            "general": "general",
        },
    )
    graph.add_edge("clarify", END)
    graph.add_edge("explain", END)
    for node in ("visualization", "insights", "forecasting", "marketing", "churn", "general"):
        graph.add_edge(node, "narrate")
    graph.add_edge("narrate", END)
    return graph.compile()
