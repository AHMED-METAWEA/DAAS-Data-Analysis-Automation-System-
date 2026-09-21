"""Shared Executive Dashboard (KPIs + charts) serialization.

Extracted so both the standalone Dashboard page (``backend/app/api/v1/dashboard.py``)
and the Insights report (``backend/app/api/v1/insights.py``) render the exact
same deterministic, grounded-by-construction KPI/chart set from
``agents.visualization.builder.build_executive_dashboard`` through one code path.
"""

from __future__ import annotations

import json

import pandas as pd

from agents.visualization.builder import build_executive_dashboard
from backend.app.schemas.visualization import ChartOut, DashboardResponse, KpiCardOut
from tools.sandbox import sanitize_fig


def chart_out(chart: dict) -> ChartOut:
    fig = sanitize_fig(chart["fig"])
    return ChartOut(
        title=chart.get("title") or chart.get("query") or "Chart",
        insight=chart.get("insight", ""),
        width=chart.get("width", "half"),
        figure=json.loads(fig.to_json()),
    )


def build_dashboard_response(df: pd.DataFrame | None) -> DashboardResponse:
    result = build_executive_dashboard(df)
    return DashboardResponse(
        kpis=[KpiCardOut(**k) for k in result["kpis"]],
        charts=[chart_out(c) for c in result["charts"]],
        revenue_label=result["revenue_label"],
        row_count=result["row_count"],
    )
