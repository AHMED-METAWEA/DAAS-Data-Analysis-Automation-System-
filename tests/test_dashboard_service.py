from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from backend.app.services.dashboard_service import build_dashboard_response, chart_out


def _sales_df(n_days: int = 60) -> pd.DataFrame:
    rows = []
    base = pd.Timestamp("2024-01-01")
    oid = 0
    for d in range(n_days):
        for k in range(1 + d % 3):
            oid += 1
            rows.append({
                "order_id": f"O{oid}",
                "customer_id": f"C{oid % 25}",
                "order_date": (base + pd.Timedelta(days=d)).strftime("%Y-%m-%d"),
                "product": f"P{oid % 6}",
                "category": ["Coffee", "Tea", "Gear"][oid % 3],
                "total_price": 20.0 + (oid % 10) * 5,
            })
    return pd.DataFrame(rows)


def test_build_dashboard_response_serializes_charts_to_plotly_json() -> None:
    response = build_dashboard_response(_sales_df())

    assert response.kpis
    assert response.charts
    for chart in response.charts:
        assert isinstance(chart.figure, dict)
        assert "data" in chart.figure and "layout" in chart.figure
        # Must be plain JSON, not a leaked go.Figure object.
        assert not isinstance(chart.figure, go.Figure)


def test_build_dashboard_response_empty_df() -> None:
    response = build_dashboard_response(None)
    assert response.kpis == []
    assert response.charts == []
    assert response.row_count == 0

    response_empty = build_dashboard_response(pd.DataFrame())
    assert response_empty.kpis == []
    assert response_empty.charts == []


def test_chart_out_falls_back_to_query_title() -> None:
    fig = go.Figure(go.Bar(x=["a"], y=[1]))
    out = chart_out({"query": "Auto Chart Title", "fig": fig})
    assert out.title == "Auto Chart Title"
    assert out.width == "half"
