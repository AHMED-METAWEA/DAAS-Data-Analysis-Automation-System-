from __future__ import annotations

import pandas as pd

from agents.visualization.builder import build_executive_dashboard
from agents.visualization.theme import human_money


def _sales_df(n_days: int = 90) -> pd.DataFrame:
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


def test_build_executive_dashboard_basic() -> None:
    result = build_executive_dashboard(_sales_df())

    assert result["kpis"], "expected at least one KPI card"
    assert any(k["label"] == "Revenue" for k in result["kpis"])
    assert result["charts"], "expected at least one chart"
    for chart in result["charts"]:
        assert chart["title"]
        assert chart["fig"] is not None
        assert chart["insight"], f"chart '{chart['title']}' has no grounded insight text"
        assert chart["width"] in ("half", "full")
    titles = {c["title"] for c in result["charts"]}
    assert "Revenue Over Time" in titles
    assert result["row_count"] == len(_sales_df())


def test_build_executive_dashboard_empty_df_returns_empty_shape() -> None:
    result = build_executive_dashboard(pd.DataFrame())
    assert result == {"kpis": [], "charts": [], "revenue_label": "", "row_count": 0}

    result_none = build_executive_dashboard(None)
    assert result_none == {"kpis": [], "charts": [], "revenue_label": "", "row_count": 0}


def test_build_executive_dashboard_no_monetary_column_falls_back_to_record_count() -> None:
    df = pd.DataFrame({
        "order_date": pd.date_range("2024-01-01", periods=40).strftime("%Y-%m-%d"),
        "note": [f"n{i}" for i in range(40)],
    })
    result = build_executive_dashboard(df)
    assert result["revenue_label"] == "record count"
    # Time-based charts should still render off the fallback count series.
    titles = {c["title"] for c in result["charts"]}
    assert "Revenue Over Time" in titles


def test_chart_insight_strings_match_underlying_numbers() -> None:
    df = _sales_df()
    result = build_executive_dashboard(df)
    revenue_chart = next(c for c in result["charts"] if c["title"] == "Revenue Over Time")

    total_revenue = df["total_price"].sum()
    # The chart's own narration must cite the same total the KPI engine
    # independently computed — this is "grounded by construction," not an
    # LLM claim, so the two must match exactly (same compact-money format).
    assert human_money(total_revenue) in revenue_chart["insight"]
