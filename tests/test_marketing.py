from __future__ import annotations

import numpy as np
import pandas as pd

from agents.marketing.engine import (
    compute_channel_performance,
    run_marketing_analytics,
)
from agents.marketing.segmentation import SEGMENT_GRID, compute_rfm


def _sample_df(n_customers: int = 40, n_orders: int = 300) -> pd.DataFrame:
    """Synthetic transaction data with customer / date / revenue / category."""
    rng = np.random.default_rng(42)
    customers = [f"C{idx:03d}" for idx in range(n_customers)]
    dates = pd.date_range("2024-01-01", "2024-12-31", freq="D")
    return pd.DataFrame({
        "order_id": [f"ORD-{idx}" for idx in range(n_orders)],
        "customer_id": rng.choice(customers, n_orders),
        "order_date": rng.choice(dates, n_orders),
        "total_price": rng.uniform(10, 500, n_orders).round(2),
        "category": rng.choice(["Electronics", "Office", "Furniture"], n_orders),
        "region": rng.choice(["North", "South", "East", "West"], n_orders),
    })


# ── Segmentation ────────────────────────────────────────────────────────────

def test_grid_is_complete() -> None:
    # Every (R, FM) pair in 1..5 must map to a segment.
    for r in range(1, 6):
        for fm in range(1, 6):
            assert (r, fm) in SEGMENT_GRID


def test_compute_rfm_basic() -> None:
    rfm = compute_rfm(_sample_df())
    assert rfm["available"] is True
    assert rfm["total_customers"] > 0
    assert rfm["segments"]
    # Customer percentages should sum to ~100.
    total_pct = sum(s["customer_pct"] for s in rfm["segments"].values())
    assert 99.0 <= total_pct <= 101.0
    # Scores produce valid segment names.
    for name in rfm["segments"]:
        assert name in set(SEGMENT_GRID.values())


def test_compute_rfm_missing_customer_column() -> None:
    df = pd.DataFrame({"x": [1, 2, 3], "y": [4, 5, 6]})
    rfm = compute_rfm(df)
    assert rfm["available"] is False
    assert "reason" in rfm


def test_compute_rfm_no_time_column_falls_back() -> None:
    df = pd.DataFrame({
        "customer_id": ["A", "A", "B", "C", "C", "C"],
        "total_price": [10, 20, 30, 40, 50, 60],
    })
    rfm = compute_rfm(df)
    # No date column but monetary present -> still segments customers.
    assert rfm["available"] is True
    assert rfm["total_customers"] == 3


# ── KPIs / channels ─────────────────────────────────────────────────────────

def test_compute_marketing_kpis_shapes() -> None:
    df = _sample_df()
    payload = run_marketing_analytics(df)
    mk = payload["marketing_kpis"]
    assert "repeat_purchase_rate" in mk
    assert "churn_risk_pct" in mk
    assert "aov" in mk


def test_channel_performance_detects_dimensions() -> None:
    df = _sample_df()
    from agents.analytics.schema_intel import build_schema_summary
    channels = compute_channel_performance(df, build_schema_summary(df))
    # region + category should both be picked up.
    assert any("region" in k.lower() for k in channels)
    assert any("category" in k.lower() for k in channels)


# ── Full pipeline ───────────────────────────────────────────────────────────

def test_run_marketing_analytics_payload_keys() -> None:
    payload = run_marketing_analytics(_sample_df())
    for key in ("schema", "kpi", "rfm", "marketing_kpis", "channels", "metadata"):
        assert key in payload
    assert payload["metadata"]["row_count"] > 0
