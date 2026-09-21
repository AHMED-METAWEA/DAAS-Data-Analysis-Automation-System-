"""
Tests for the series-preparation layer.

Each test here pins a bug that was silently costing forecast accuracy before
the layer existed — the calendar being compressed by dropped periods, partial
buckets read as demand collapses, and gaps meaning different things for
additive metrics than for rates.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from agents.forecasting.tools.preparation import (
    FREQ_SPEC,
    aggregation_for,
    build_series,
    freq_alias,
    prepare_data,
)

# ── The calendar must stay aligned ───────────────────────────────────────────


def test_gaps_are_filled_so_the_calendar_is_not_compressed():
    # 10 calendar days but only 7 with orders. Grouping by date yields 7
    # "consecutive" points spanning 10 real days, which de-aligns every
    # index-based seasonal model downstream.
    dates = ["2024-01-01", "2024-01-02", "2024-01-03",
             "2024-01-06", "2024-01-07", "2024-01-09", "2024-01-10"]
    df = pd.DataFrame({"d": dates, "revenue": [10.0] * 7})
    s = build_series(df, "d", "revenue", "daily")

    assert s.n == 10, "series must span the full calendar, not just observed days"
    assert s.filled_periods == 3
    assert list(pd.DatetimeIndex(s.dates).day) == list(range(1, 11))


def test_additive_gaps_become_zero_not_interpolated():
    df = pd.DataFrame({
        "d": ["2024-01-01", "2024-01-03"],
        "revenue": [100.0, 300.0],
    })
    s = build_series(df, "d", "revenue", "daily")
    # No orders on Jan 2 means zero revenue, not the average of its neighbours.
    assert s.y[1] == 0.0
    assert s.aggregation == "sum"


def test_rate_gaps_are_interpolated_not_zeroed():
    df = pd.DataFrame({
        "d": ["2024-01-01", "2024-01-03"],
        "unit_price": [10.0, 30.0],
    })
    s = build_series(df, "d", "unit_price", "daily")
    # A day with no orders has no average price — it does not have one of zero.
    assert s.aggregation == "mean"
    assert s.y[1] == 20.0


def test_empty_sum_periods_are_detected_despite_pandas_zero_filling():
    # `resample().sum()` reports an empty bucket as 0.0 rather than NaN, so the
    # fill count has to come from row counts or it silently reads as zero.
    df = pd.DataFrame({"d": ["2024-01-01", "2024-01-04"], "revenue": [5.0, 5.0]})
    s = build_series(df, "d", "revenue", "daily")
    assert s.filled_periods == 2
    assert any("no records" in n for n in s.notes)


# ── Partial buckets are not demand collapses ─────────────────────────────────


def test_trailing_partial_week_is_dropped():
    # Data ends mid-week: the final W-SUN bucket holds 3 days. Left in, it looks
    # like a ~60% collapse right where every model anchors.
    df = pd.DataFrame({
        "d": pd.date_range("2024-01-01", periods=17, freq="D"),  # Mon .. Wed
        "revenue": [100.0] * 17,
    })
    s = build_series(df, "d", "revenue", "weekly")
    assert "trailing" in s.dropped_partial
    # Every surviving bucket is a full 7 x 100.
    assert np.allclose(s.y, 700.0)


def test_leading_partial_month_is_dropped():
    df = pd.DataFrame({
        "d": pd.date_range("2024-01-15", periods=80, freq="D"),
        "revenue": [10.0] * 80,
    })
    s = build_series(df, "d", "revenue", "monthly")
    assert "leading" in s.dropped_partial
    assert pd.Timestamp(s.dates[0]).month == 2


def test_complete_weeks_are_kept_intact():
    df = pd.DataFrame({
        "d": pd.date_range("2024-01-01", periods=14, freq="D"),  # Mon .. Sun
        "revenue": [1.0] * 14,
    })
    s = build_series(df, "d", "revenue", "weekly")
    assert s.dropped_partial == []
    assert s.y.sum() == 14.0


# ── Frequency grid consistency ───────────────────────────────────────────────


def test_history_and_future_share_one_frequency_alias():
    # History was resampled to "MS" while future dates were generated at "ME",
    # so monthly forecasts landed on stamps matching no historical point and the
    # whole run produced "no date overlap".
    from agents.forecasting.tools.models import freq_to_pandas

    for label in ("daily", "weekly", "monthly"):
        assert freq_to_pandas(label) == freq_alias(label) == FREQ_SPEC[label][0]


def test_monthly_series_lands_on_month_start():
    df = pd.DataFrame({
        "d": pd.date_range("2024-01-01", periods=120, freq="D"),
        "revenue": [1.0] * 120,
    })
    s = build_series(df, "d", "revenue", "monthly")
    assert all(pd.Timestamp(d).day == 1 for d in s.dates)


# ── Shape diagnostics ────────────────────────────────────────────────────────


def test_intermittent_series_is_flagged():
    y = [0.0, 0.0, 5.0, 0.0, 0.0, 0.0, 3.0, 0.0, 0.0, 4.0, 0.0, 0.0]
    df = pd.DataFrame({"d": pd.date_range("2024-01-01", periods=len(y)), "units": y})
    s = build_series(df, "d", "units", "daily")
    assert s.intermittent is True
    assert s.zero_share > 0.5


def test_dense_series_is_not_flagged_intermittent():
    df = pd.DataFrame({
        "d": pd.date_range("2024-01-01", periods=60, freq="D"),
        "revenue": np.linspace(100, 200, 60),
    })
    s = build_series(df, "d", "revenue", "daily")
    assert s.intermittent is False
    assert s.constant is False
    assert s.all_positive is True


def test_constant_series_is_flagged():
    df = pd.DataFrame({
        "d": pd.date_range("2024-01-01", periods=30, freq="D"),
        "revenue": [42.0] * 30,
    })
    assert build_series(df, "d", "revenue", "daily").constant is True


def test_empty_input_degrades_without_raising():
    df = pd.DataFrame({"d": [], "revenue": []})
    s = build_series(df, "d", "revenue", "daily")
    assert s.n == 0
    assert s.notes


def test_to_dict_is_json_safe():
    import json

    df = pd.DataFrame({
        "d": pd.date_range("2024-01-01", periods=30, freq="D"),
        "revenue": np.arange(30, dtype=float),
    })
    json.dumps(build_series(df, "d", "revenue", "daily").to_dict())


# ── Aggregation choice ───────────────────────────────────────────────────────


def test_aggregation_for_additive_vs_rate():
    for col in ("revenue", "total_price", "quantity", "profit", "order_count"):
        assert aggregation_for(col) == "sum", col
    for col in ("unit_price", "discount_pct", "avg_order_value", "margin_ratio"):
        assert aggregation_for(col) == "mean", col


# ── Backwards-compatible wrapper ─────────────────────────────────────────────


def test_prepare_data_wrapper_maps_legacy_rules():
    df = pd.DataFrame({
        "d": pd.date_range("2024-01-01", periods=14, freq="D"),
        "revenue": [1.0] * 14,
    })
    out = prepare_data(df, "d", "revenue", rule="W")
    assert list(out.columns) == ["ds", "y"]
    assert out["y"].sum() == 14.0
