from __future__ import annotations

import pandas as pd

from agents.copilot import tools as copilot_tools
from agents.insights.evidence import EvidenceItem
from agents.insights.figures import FigureRegistry
from agents.reporting.grounding import GroundingResult
from backend.app.services.insights_service import GroundedInsights


def _df() -> pd.DataFrame:
    return pd.DataFrame({
        "customer_id": ["C1", "C2", "C3"],
        "order_date": ["2024-01-01", "2024-01-02", "2024-01-03"],
        "total_price": [100.0, 200.0, 300.0],
    })


def _state(message: str = "give me a summary") -> dict:
    return {
        "project_id": "p1",
        "message": message,
        "history": [],
        "model": "test-model",
        "model_prefs": {},
        "has_previous_result": False,
        "last_route": None,
        "last_tool_result": None,
        "route": "",
        "forecast_horizon_days": 30,
        "forecast_metric_hint": "",
        "clarify_question": "",
        "route_error": "",
        "tool_result": None,
        "narration": None,
    }


def test_run_insights_includes_grounded_flag(monkeypatch) -> None:
    monkeypatch.setattr(copilot_tools, "resolve_view", lambda project_id, view: _df())

    registry = FigureRegistry()
    registry.add("revenue_total", "Total revenue", 600.0, "currency", "sum of line revenue")
    fake_result = GroundedInsights(
        report_md="# Report\nAll good.",
        template_stem="decision_brief",
        grounding=GroundingResult(),
        analytics_payload={"kpi": {"revenue": 600.0}},
        registry=registry,
        evidence=[EvidenceItem(
            tag="STRENGTH", text="Total revenue is {{revenue_total}}.",
            money_at_stake=600.0, section="standing",
        )],
    )
    monkeypatch.setattr(copilot_tools, "generate_grounded_insights", lambda *a, **kw: fake_result)

    result = copilot_tools._run_insights(_state())
    tool_result = result["tool_result"]

    assert tool_result["tool"] == "insights"
    assert tool_result["report_md"] == "# Report\nAll good."
    assert tool_result["raw_payload"]["grounded"] is True
    assert any("revenue" in b.lower() for b in tool_result["raw_payload"]["key_findings"])
    # Citation tokens must be rendered before another agent ever sees them.
    assert "{{" not in " ".join(tool_result["raw_payload"]["key_findings"])
    assert "600.00" in " ".join(tool_result["raw_payload"]["key_findings"])


def test_run_insights_error_result_on_exception(monkeypatch) -> None:
    def boom(project_id, view):
        raise RuntimeError("no data")

    monkeypatch.setattr(copilot_tools, "resolve_view", boom)

    result = copilot_tools._run_insights(_state())
    tool_result = result["tool_result"]

    assert tool_result["tool"] == "insights"
    assert tool_result["raw_payload"]["error"] == "no data"
