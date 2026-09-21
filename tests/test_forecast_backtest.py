from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from agents.forecasting.tools.backtest import (
    _splits,
    backtest_model,
    select_model,
    skill_vs_naive,
)
from agents.forecasting.tools.models import (
    FAST_MODELS,
    available_models,
    drift,
    is_available,
    linear_trend,
    naive,
    seasonal_naive,
)

# ── Model functions ──────────────────────────────────────────────────────────


def test_naive_repeats_last_value() -> None:
    y = np.array([1.0, 2.0, 9.0])
    out = naive(y, 4, "daily")
    assert list(out) == [9.0, 9.0, 9.0, 9.0]


def test_drift_extrapolates_slope() -> None:
    y = np.array([0.0, 1.0, 2.0, 3.0])  # slope 1 per step
    out = drift(y, 3, "daily")
    np.testing.assert_allclose(out, [4.0, 5.0, 6.0])


def test_linear_trend_fits_line() -> None:
    y = np.array([10.0 + 2.0 * i for i in range(20)])
    out = linear_trend(y, 5, "daily")
    np.testing.assert_allclose(out, [10 + 2 * i for i in range(20, 25)], rtol=1e-6)


def test_seasonal_naive_repeats_season() -> None:
    season = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
    y = np.array(season * 3)
    out = seasonal_naive(y, 7, "daily")
    np.testing.assert_allclose(out, season)


def test_models_return_requested_length() -> None:
    y = np.arange(15, dtype=float)
    for fn in (naive, drift, linear_trend, seasonal_naive):
        assert len(fn(y, 6, "daily")) == 6


def test_available_models_always_has_naive() -> None:
    assert "Naive" in available_models(2, "daily")
    # Prophet needs >= 24 points
    assert is_available("Prophet", 10) is False


# ── Back-testing ─────────────────────────────────────────────────────────────


def test_splits_expanding_window() -> None:
    splits = _splits(n=40, h=5, folds=3, min_train=10)
    assert len(splits) == 3
    # oldest fold first, windows are contiguous and non-overlapping
    assert splits == [(25, 30), (30, 35), (35, 40)]


def test_backtest_model_runs() -> None:
    y = np.array([10.0 + 2.0 * i for i in range(40)])
    dates = pd.date_range("2024-01-01", periods=40, freq="D").values
    metrics = backtest_model(linear_trend, y, dates, h=5, freq="daily", folds=3)
    assert metrics is not None
    assert metrics["folds"] >= 1
    assert metrics["mape"] is not None


def test_select_model_prefers_trend_over_naive_on_trending_data() -> None:
    y = np.array([10.0 + 2.0 * i for i in range(40)])
    dates = pd.date_range("2024-01-01", periods=40, freq="D").values
    board = select_model(y, dates, h=5, freq="daily", candidates=FAST_MODELS)
    assert board, "leaderboard should not be empty"
    best = board[0]["model"]
    # A clean upward trend must be modelled better than a flat random walk.
    assert best in {"Linear Trend", "Drift"}
    skill = skill_vs_naive(board, best)
    assert skill is not None and skill > 0


def test_skill_vs_naive_handles_missing() -> None:
    assert skill_vs_naive([], "Naive") is None


# ── Engine integration (needs Prophet installed) ─────────────────────────────


def test_engine_auto_selection_outputs_contract() -> None:
    pytest.importorskip("prophet")
    from agents.forecasting.tools.engines import ForecastEngine, SeriesData

    y = np.array([10.0 + 2.0 * i for i in range(20)])  # < 24 → Prophet excluded, fast
    dates = pd.date_range("2024-01-01", periods=20, freq="D").values
    result = ForecastEngine.run("Auto", SeriesData(y=y, dates=dates, frequency="daily"), horizon=5)

    for key in ("forecast", "dates", "lower", "upper", "metrics",
                "selected_model", "model_selection_reason", "cv_results"):
        assert key in result, f"missing {key}"
    assert len(result["forecast"]) == 5
    assert len(result["dates"]) == 5
    assert len(result["lower"]) == 5 and len(result["upper"]) == 5
    assert result["selected_model"] in {r["model"] for r in result["cv_results"]}


def test_engine_forced_model_unavailable_falls_back() -> None:
    pytest.importorskip("prophet")
    from agents.forecasting.tools.engines import ForecastEngine, SeriesData

    y = np.array([10.0 + 2.0 * i for i in range(20)])
    dates = pd.date_range("2024-01-01", periods=20, freq="D").values
    # Prophet needs >= 24 points, so forcing it on 20 points must fall back.
    result = ForecastEngine.run("Prophet", SeriesData(y=y, dates=dates, frequency="daily"), horizon=4)
    assert result["selected_model"] != "Prophet"
    assert len(result["forecast"]) == 4
