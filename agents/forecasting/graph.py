from __future__ import annotations

from langgraph.graph import END, StateGraph

from agents.forecasting.forecast_models import ForecastState
from agents.forecasting.interpreter import interpret_node
from agents.forecasting.nodes import (
    drift_node,
    forecast_node,
    prepare_node,
    validate_node,
)


def build_graph() -> StateGraph:
    builder = StateGraph(ForecastState)

    builder.add_node("validate", validate_node)
    builder.add_node("prepare", prepare_node)
    builder.add_node("forecast", forecast_node)
    # Drift runs before interpretation so the narrative is written against the
    # post-drift reliability verdict, not the pre-drift one.
    builder.add_node("drift", drift_node)
    builder.add_node("interpret", interpret_node)

    builder.set_entry_point("validate")
    builder.add_edge("validate", "prepare")
    builder.add_edge("prepare", "forecast")
    builder.add_edge("forecast", "drift")
    builder.add_edge("drift", "interpret")
    builder.add_edge("interpret", END)

    return builder.compile()


forecast_graph = build_graph()
