"""State for the Analyst Copilot supervisor graph — see `agents/copilot/graph.py`."""

from __future__ import annotations

from typing import Any, TypedDict


class CopilotState(TypedDict):
    project_id: str
    message: str
    history: list[dict[str, str]]
    model: str
    model_prefs: dict[str, str]

    # Populated by the endpoint from the cached CopilotSession *before* this
    # turn's graph invocation — lets the router recognize a follow-up and
    # lets `_explain` re-examine the same payload without a fresh tool call.
    has_previous_result: bool
    last_route: str | None
    last_tool_result: dict[str, Any] | None

    route: str
    forecast_horizon_days: int
    forecast_metric_hint: str
    clarify_question: str
    route_error: str

    tool_result: dict[str, Any] | None
    narration: str | None
