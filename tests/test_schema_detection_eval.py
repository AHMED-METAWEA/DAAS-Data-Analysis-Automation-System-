"""Evaluation harness for the zero-config schema-role detector.

Schema detection is the load-bearing dependency under every KPI: if it labels
the wrong column as revenue/customer/date, the numbers are grounded but wrong.
These fixtures pin the detector on realistic AND adversarial/ambiguous inputs,
and assert that ambiguity is *disclosed* via warnings rather than trusted
silently — bringing this layer up to the validation rigor of churn/forecasting.
"""

from __future__ import annotations

import pandas as pd

from agents.analytics.schema_intel import build_schema_summary


def _clean_line_total() -> pd.DataFrame:
    return pd.DataFrame({
        "order_date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"]),
        "order_id": ["O1", "O2", "O3", "O4"],
        "customer_id": ["C1", "C1", "C2", "C3"],   # repeats -> real customer id
        "product": ["A", "B", "A", "C"],
        "category": ["x", "y", "x", "y"],
        "total_amount": [120.0, 80.0, 60.0, 200.0],
        "quantity": [2, 1, 1, 3],
    })


# ── Correct detection on well-named data ─────────────────────────────────────

def test_clean_schema_detects_all_roles_with_no_warnings() -> None:
    s = build_schema_summary(_clean_line_total())
    assert s["time_column"] == "order_date"
    assert s["monetary_columns"][0] == "total_amount"
    assert s["customer_column"] == "customer_id"
    assert s["order_column"] == "order_id"
    assert s["product_column"] == "product"
    assert s["detection_confidence"]["revenue_column"] == "high"
    assert s["detection_confidence"]["time_column"] == "high"
    # Nothing ambiguous -> no cautions.
    assert s["warnings"] == []


def test_unit_price_schema_is_confident_and_clean() -> None:
    df = pd.DataFrame({
        "order_date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
        "customer_id": ["C1", "C1", "C2"],
        "unit_price": [10.0, 20.0, 15.0],
        "quantity": [2, 1, 4],
    })
    s = build_schema_summary(df)
    assert s["monetary_columns"][0] == "unit_price"
    assert s["detection_confidence"]["revenue_column"] in ("high", "medium")
    assert not any("auto-detected by data type" in w for w in s["warnings"])


# ── Adversarial: revenue guessed by dtype (no money-like name) ───────────────

def test_revenue_detected_by_type_is_flagged_low_confidence() -> None:
    df = pd.DataFrame({
        "order_date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
        "customer_id": ["C1", "C1", "C2"],
        "metric_a": [100.0, 200.0, 150.0],   # numeric, but no money-like name
    })
    s = build_schema_summary(df)
    assert s["monetary_columns"][0] == "metric_a"
    assert s["detection_confidence"]["revenue_column"] == "low"
    assert any("auto-detected by data type" in w for w in s["warnings"])


# ── Adversarial: customer id that is actually a row id ───────────────────────

def test_unique_per_row_customer_id_is_flagged() -> None:
    df = pd.DataFrame({
        "order_date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
        "customer_id": ["C1", "C2", "C3"],     # one per row -> suspicious
        "total_amount": [10.0, 20.0, 30.0],
    })
    s = build_schema_summary(df)
    assert any("distinct value in every" in w for w in s["warnings"])


# ── Adversarial: no date column disables downstream analysis ─────────────────

def test_missing_date_column_is_flagged() -> None:
    df = pd.DataFrame({
        "customer_id": ["C1", "C1", "C2"],
        "total_amount": [10.0, 20.0, 30.0],
    })
    s = build_schema_summary(df)
    assert s["time_column"] is None
    assert s["detection_confidence"]["time_column"] == "none"
    assert any("No date/time column" in w for w in s["warnings"])


# ── Adversarial: two equally money-named columns ─────────────────────────────

def test_ambiguous_revenue_columns_are_flagged() -> None:
    df = pd.DataFrame({
        "order_date": pd.to_datetime(["2024-01-01", "2024-01-02"]),
        "customer_id": ["C1", "C2"],
        "sales": [100.0, 200.0],
        "revenue": [90.0, 180.0],
    })
    s = build_schema_summary(df)
    assert any("Multiple columns could be revenue" in w for w in s["warnings"])


# ── Adversarial: mostly-negative "revenue" ───────────────────────────────────

def test_mostly_negative_revenue_is_flagged() -> None:
    df = pd.DataFrame({
        "order_date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"]),
        "customer_id": ["C1", "C1", "C2", "C3"],
        "revenue": [-100.0, -50.0, -25.0, 10.0],
    })
    s = build_schema_summary(df)
    assert any("mostly negative" in w for w in s["warnings"])
