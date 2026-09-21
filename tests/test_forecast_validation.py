from __future__ import annotations

import pandas as pd

from agents.forecasting.validation import (
    MIN_HISTORY,
    _check_missing_dates,
    _detect_date_column,
    _detect_numeric_metrics,
    _determine_frequency,
    list_forecastable_metrics,
)


def test_detect_date_column_datetime() -> None:
    df = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=5), "val": [1, 2, 3, 4, 5]})
    assert _detect_date_column(df) == "date"


def test_detect_date_column_string() -> None:
    df = pd.DataFrame({"order_date": ["2024-01-01", "2024-01-02"], "x": [1, 2]})
    assert _detect_date_column(df) == "order_date"


def test_detect_date_column_none() -> None:
    df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
    assert _detect_date_column(df) is None


def test_detect_date_column_created_at() -> None:
    df = pd.DataFrame({"created_at": ["2024-01-01", "2024-01-02"], "val": [1, 2]})
    col = _detect_date_column(df)
    assert col == "created_at"


def test_determine_frequency_daily() -> None:
    dates = pd.date_range("2024-01-01", periods=10, freq="D")
    assert _determine_frequency(dates) == "daily"


def test_determine_frequency_weekly() -> None:
    dates = pd.date_range("2024-01-01", periods=10, freq="W")
    assert _determine_frequency(dates) == "weekly"


def test_determine_frequency_monthly() -> None:
    dates = pd.date_range("2024-01-01", periods=6, freq="MS")
    assert _determine_frequency(dates) == "monthly"


def test_determine_frequency_short() -> None:
    dates = pd.to_datetime(["2024-01-01"])
    assert _determine_frequency(dates) == "daily"


def test_check_missing_dates_none() -> None:
    dates = pd.date_range("2024-01-01", periods=5, freq="D")
    missing = _check_missing_dates(dates, "daily")
    assert len(missing) == 0


def test_check_missing_dates_some() -> None:
    dates = pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-05"])
    missing = _check_missing_dates(dates, "daily")
    assert len(missing) == 2


def test_detect_numeric_metrics() -> None:
    df = pd.DataFrame({"date": [1, 2], "revenue": [100, 200], "name": ["a", "b"], "id": [1, 2]})
    metrics = _detect_numeric_metrics(df, "date")
    assert "revenue" in metrics
    assert "date" not in metrics
    assert "name" not in metrics


def test_list_forecastable_metrics() -> None:
    df = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=30),
        "revenue": [i * 10 for i in range(30)],
        "orders": [i for i in range(30)],
    })
    date_col, metrics = list_forecastable_metrics(df)
    assert date_col == "date"
    assert "revenue" in metrics
    assert "orders" in metrics


def test_list_forecastable_metrics_no_date() -> None:
    df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
    date_col, metrics = list_forecastable_metrics(df)
    assert date_col is None
    assert metrics == []


def test_MIN_HISTORY_values() -> None:
    assert MIN_HISTORY["daily"] == 30
    assert MIN_HISTORY["weekly"] == 12
    assert MIN_HISTORY["monthly"] == 6
