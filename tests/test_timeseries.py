from __future__ import annotations

import numpy as np
import pandas as pd

from agents.analytics.timeseries import compute_timeseries


def test_compute_timeseries_no_time_column() -> None:
    df = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})
    result = compute_timeseries(df)
    assert result.get("available") is False


def test_compute_timeseries_with_daily_data() -> None:
    rng = np.random.default_rng(42)
    dates = pd.date_range("2024-01-01", periods=100, freq="D")
    values = 100 + np.cumsum(rng.normal(0, 2, 100))
    df = pd.DataFrame({"date": dates, "revenue": values})
    result = compute_timeseries(df)
    assert result.get("available") is True
    assert result.get("time_column") == "date"
    assert result.get("trend_direction") in ("up", "down", "flat")


def test_compute_timeseries_anomalies() -> None:
    rng = np.random.default_rng(42)
    dates = pd.date_range("2024-01-01", periods=50, freq="D")
    values = np.ones(50) * 100
    values[25] = 500
    df = pd.DataFrame({"date": dates, "revenue": values})
    result = compute_timeseries(df)
    if result.get("available"):
        assert result.get("anomalies", {}).get("z_score_count", 0) >= 1


def test_compute_timeseries_empty_after_resample() -> None:
    dates = pd.date_range("2024-01-01", periods=3, freq="D")
    df = pd.DataFrame({"date": dates, "revenue": [1, 2, 3]})
    result = compute_timeseries(df)
    assert isinstance(result, dict)


def test_compute_timeseries_short_series() -> None:
    df = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=5), "revenue": [1, 2, 3, 4, 5]})
    result = compute_timeseries(df)
    assert isinstance(result, dict)
