"""One node per routable tool.

Every node wraps its own try/except so a bad view/model/agent failure
degrades to a narrated apology (via ``tool_result.raw_payload.error``)
instead of failing the whole chat turn.
"""

from __future__ import annotations

import json

from agents.churn.agent import generate_retention_plan
from agents.churn.engine import run_churn_analysis
from agents.constants import (
    DEFAULT_CHURN_MODEL,
    DEFAULT_FORECAST_MODEL,
    DEFAULT_INSIGHTS_MODEL,
    DEFAULT_MARKETING_MODEL,
    DEFAULT_VIZ_MODEL,
)
from agents.forecasting.metric_discovery import rank_metrics
from agents.forecasting.pipeline import ForecastPipeline
from agents.forecasting.report import build_forecast_report_md
from agents.forecasting.validation import list_forecastable_metrics
from agents.marketing.agent import generate_marketing_strategy, slim_for_prompt
from agents.marketing.engine import run_marketing_analytics
from agents.visualization.viz_graph import build_viz_graph
from backend.app.services.analysis_chat import run_grounded_chat
from backend.app.services.insights_service import generate_grounded_insights
from backend.app.services.views import resolve_view
from tools.sandbox import describe_figure

from .state import CopilotState


def _error_result(tool: str, exc: Exception | str) -> dict:
    return {
        "tool_result": {
            "tool": tool,
            "figure": None,
            "table": None,
            "report_md": None,
            "raw_payload": {"error": str(exc)},
        }
    }


def _run_visualization(state: CopilotState) -> dict:
    try:
        df = resolve_view(state["project_id"], "analytics")
        model = state["model_prefs"].get("viz") or DEFAULT_VIZ_MODEL
        graph = build_viz_graph()
        result = graph.invoke(
            {
                "user_query": state["message"],
                "schema_info": "",
                "table_name": "",
                "model": model,
                "existing_chart_count": 0,
                "generated_code": "",
                "execution_result": {},
                "retry_count": 0,
                "last_error": "",
                "df": df,
            }
        )
        exec_result = result.get("execution_result", {}) or {}
        fig = exec_result.get("fig")
        if exec_result.get("error") and fig is None:
            return _error_result("visualization", exec_result["error"])
        # describe_figure() gives narrate/explain something real to ground on —
        # exec_result["output"] alone is almost always empty since the chart
        # IS the script's output, not printed text (this was the root cause
        # of "explain this chart" follow-ups failing with an empty-payload
        # apology).
        fig_dict = json.loads(fig.to_json()) if fig is not None else None
        chart_summary = describe_figure(fig_dict) if fig_dict is not None else {}
        return {
            "tool_result": {
                "tool": "visualization",
                "figure": fig_dict,
                "table": None,
                "report_md": None,
                "raw_payload": {"chart_summary": chart_summary, "output": exec_result.get("output", "")},
            }
        }
    except Exception as exc:
        return _error_result("visualization", exc)


def _run_insights(state: CopilotState) -> dict:
    try:
        df = resolve_view(state["project_id"], "analytics")
        model = state["model_prefs"].get("insights") or DEFAULT_INSIGHTS_MODEL
        result = generate_grounded_insights(df, "", model=model)
        audit = result.audit
        return {
            "tool_result": {
                "tool": "insights",
                "figure": None,
                "table": None,
                "report_md": result.report_md,
                "raw_payload": {
                    # The ranked brief and the figure audit replace the old raw
                    # analytics dump: a downstream synthesis step needs to know
                    # what mattered and where each number came from, not the
                    # whole payload it could mine a new figure out of.
                    "key_findings": [item["text"] for item in audit["evidence"]],
                    "figures": audit["figures"],
                    "blind_spots": audit["blind_spots"],
                    "grounded": result.strict.is_clean,
                    "verification": audit["certificate"],
                },
            }
        }
    except Exception as exc:
        return _error_result("insights", exc)


def _pick_forecast_target(df, hint: str) -> str | None:
    """Resolve the ONE metric to forecast this turn.

    ``ForecastPipeline.run(targets=None)`` defaults to its top-3 ranked
    metrics, each backtested across multiple candidate models — fine for the
    dedicated Forecasting page, but far too slow (100+ model fits observed)
    for a single ad-hoc chat question. A copilot turn always forecasts
    exactly one metric: whichever the router heard the user name, else the
    single highest-ranked forecastable metric.
    """
    _date_col, metrics = list_forecastable_metrics(df)
    if not metrics:
        return None
    if hint:
        norm_hint = hint.lower().replace("_", "").replace("-", "").replace(" ", "")
        for m in metrics:
            norm_m = m.lower().replace("_", "").replace("-", "").replace(" ", "")
            if norm_hint in norm_m or norm_m in norm_hint:
                return m
    return rank_metrics(metrics)[0]


def _run_forecasting(state: CopilotState) -> dict:
    try:
        df = resolve_view(state["project_id"], "forecast")
        model = state["model_prefs"].get("forecast") or DEFAULT_FORECAST_MODEL
        target = _pick_forecast_target(df, state.get("forecast_metric_hint", ""))
        if target is None:
            return _error_result("forecasting", "No forecastable metric found in this project's data.")
        result = ForecastPipeline().run(
            df,
            targets=[target],
            horizon_days=state.get("forecast_horizon_days", 30),
            llm_model=model,
            store=False,
        )
        if not result.get("forecastable"):
            return _error_result("forecasting", result.get("validation_message") or "Not forecastable.")
        outputs = result.get("forecast_outputs", [])
        table = [
            {
                "metric": o.get("metric"),
                "current_value": o.get("current_value"),
                "forecasted_value": o.get("forecasted_value"),
                "change_percent": o.get("change_percent"),
                "selected_model": o.get("selected_model"),
            }
            for o in outputs
        ]
        figure = None
        figs = result.get("execution_figures", [])
        if figs:
            from tools.sandbox import sanitize_fig

            figure = json.loads(sanitize_fig(figs[0]).to_json())
        return {
            "tool_result": {
                "tool": "forecasting",
                "figure": figure,
                "table": table,
                "report_md": build_forecast_report_md(result),
                "raw_payload": {"outputs": table},
            }
        }
    except Exception as exc:
        return _error_result("forecasting", exc)


def _run_marketing(state: CopilotState) -> dict:
    try:
        df = resolve_view(state["project_id"], "marketing")
        model = state["model_prefs"].get("marketing") or DEFAULT_MARKETING_MODEL
        result = run_marketing_analytics(data_df=df)
        report_md = generate_marketing_strategy(df, "", result, model=model)
        segments = (result.get("rfm") or {}).get("segments") or {}
        table = [{"segment": k, **v} for k, v in segments.items()] or None
        return {
            "tool_result": {
                "tool": "marketing",
                "figure": None,
                "table": table,
                "report_md": report_md,
                "raw_payload": slim_for_prompt(
                    {
                        "marketing_kpis": result.get("marketing_kpis"),
                        "rfm": result.get("rfm"),
                        "channels": result.get("channels"),
                    }
                ),
            }
        }
    except Exception as exc:
        return _error_result("marketing", exc)


def _run_churn(state: CopilotState) -> dict:
    try:
        df = resolve_view(state["project_id"], "customer_360")
        model = state["model_prefs"].get("churn") or DEFAULT_CHURN_MODEL
        payload = run_churn_analysis(data_df=df)
        if not payload.get("available"):
            return _error_result("churn", payload.get("reason") or "Churn analysis unavailable.")
        report_md = generate_retention_plan(payload, data_df=df, model=model)
        table = payload.get("at_risk_customers", [])[:15] or None
        # Cap at_risk_customers the same way generate_retention_plan already
        # does (agents/churn/agent.py) — top_n defaults to 200, and dumping
        # all 200 rows into a narration/explain prompt blows past Groq's
        # per-minute token budget on every single call.
        slim = {k: v for k, v in payload.items() if not k.startswith("_")}
        slim["at_risk_customers"] = table
        return {
            "tool_result": {
                "tool": "churn",
                "figure": None,
                "table": table,
                "report_md": report_md,
                "raw_payload": slim,
            }
        }
    except Exception as exc:
        return _error_result("churn", exc)


def run_tool(tool: str, state: CopilotState) -> dict:
    """Dispatch to one tool node by name. Used by the streaming endpoint's
    multi-step executor (``backend/app/services/copilot_stream.py``), which runs
    each planned step directly rather than through the LangGraph edges. Each
    handler already self-wraps its failures into a narrated error payload, so an
    unknown tool is the only case this needs to guard."""
    handler = _TOOL_FUNCS.get(tool)
    if handler is None:
        return _error_result(tool, f"Unknown tool: {tool}")
    return handler(state)


def _run_general(state: CopilotState) -> dict:
    try:
        df = resolve_view(state["project_id"], "analytics")
        model = state["model_prefs"].get("insights") or DEFAULT_INSIGHTS_MODEL
        result = run_grounded_chat(
            system_context=(
                "You are DAAS, a business-intelligence assistant grounded in "
                "this project's saved data. Use ONLY the data described below — never "
                "invent numbers. If you can compute the answer, write a ```python``` "
                "block using the DataFrame `df` (already loaded). Keep answers concise "
                "and cite concrete figures."
            ),
            history=state.get("history", [])[-6:],
            message=state["message"],
            df=df,
            model=model,
            purpose="insights",
        )
        return {
            "tool_result": {
                "tool": "general",
                "figure": result.get("figure"),
                "table": result.get("preview"),
                "report_md": None,
                "raw_payload": {
                    "answer": result["answer"],
                    "error": result.get("error"),
                    "grounded": result.get("grounded", True),
                },
            }
        }
    except Exception as exc:
        return _error_result("general", exc)


# name -> node function, for the streaming multi-step executor. Defined after
# the functions so every handler is already bound.
_TOOL_FUNCS = {
    "visualization": _run_visualization,
    "insights": _run_insights,
    "forecasting": _run_forecasting,
    "marketing": _run_marketing,
    "churn": _run_churn,
    "general": _run_general,
}
