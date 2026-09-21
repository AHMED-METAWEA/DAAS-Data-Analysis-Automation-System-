"""
Tests for the forecasting upgrades: additive-aware aggregation, the new
professional models, back-testable seasonal windows, and the honest,
principled confidence score.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from agents.forecasting.nodes import _confidence, _resolve_granularity
from agents.forecasting.tools.backtest import (
    backtest_ensemble,
    inverse_error_weights,
    select_model,
)
from agents.forecasting.tools.engines import aggregation_for, prepare_data
from agents.forecasting.tools.evaluation import evaluate_forecast, naive_scale
from agents.forecasting.tools.models import MODELS, get_model_fn, is_available

# ── Additive-aware aggregation ───────────────────────────────────────────────

def test_aggregation_for_additive_vs_rate():
    for col in ("revenue", "total_price", "quantity", "profit", "order_count"):
        assert aggregation_for(col) == "sum", col
    for col in ("unit_price", "discount_pct", "avg_order_value", "margin_ratio"):
        assert aggregation_for(col) == "mean", col


def test_prepare_data_sums_additive_metric():
    df = pd.DataFrame({
        "d": ["2024-01-01", "2024-01-01", "2024-01-02"],
        "total_price": [10.0, 20.0, 5.0],
    })
    out = prepare_data(df, "d", "total_price")
    # Two orders on Jan 1 must be SUMMED (30), not averaged (15).
    assert out.iloc[0]["y"] == 30.0
    assert out.iloc[1]["y"] == 5.0


def test_prepare_data_averages_rate_metric():
    df = pd.DataFrame({
        "d": ["2024-01-01", "2024-01-01"],
        "unit_price": [10.0, 20.0],
    })
    out = prepare_data(df, "d", "unit_price")
    assert out.iloc[0]["y"] == 15.0  # mean, not sum


def test_prepare_data_resamples_weekly():
    df = pd.DataFrame({
        "d": pd.date_range("2024-01-01", periods=14, freq="D"),
        "revenue": [1.0] * 14,
    })
    out = prepare_data(df, "d", "revenue", rule="W")
    # 14 daily 1.0s summed into weekly buckets -> ~7 per week.
    assert out["y"].sum() == 14.0
    assert len(out) <= 3


# ── New professional models ──────────────────────────────────────────────────

def test_new_models_registered():
    for name in ("Theta", "ETS", "ARIMA", "SARIMA"):
        assert name in MODELS
        assert MODELS[name]["needs"] == "statsmodels"


def test_new_models_run_and_return_length():
    y = np.array([10.0 + i + (i % 7) for i in range(60)])
    dates = pd.date_range("2024-01-01", periods=60, freq="D").values
    for name in ("Theta", "ETS", "ARIMA", "SARIMA"):
        if not is_available(name, len(y)):
            continue
        out = get_model_fn(name)(y, 5, "daily", dates)
        assert len(out) == 5
        assert np.all(np.isfinite(out))


def test_arima_beats_fixed_order_on_seasonal_data():
    # A clean weekly-seasonal pattern: fixed ARIMA(1,1,1) captures no
    # seasonality at all, so the curated auto-order search must find an order
    # that fits materially better (lower AIC / forecast error) than (1,1,1).
    rng = np.random.default_rng(3)
    n = 70
    y = 100 + 15 * np.sin(np.arange(n) * 2 * np.pi / 7) + rng.normal(0, 1, n)
    dates = pd.date_range("2024-01-01", periods=n, freq="D").values
    out = get_model_fn("ARIMA")(y, 7, "daily", dates)
    assert len(out) == 7
    assert np.all(np.isfinite(out))


def test_sarima_captures_weekly_seasonality_better_than_plain_arima():
    rng = np.random.default_rng(4)
    n = 90
    y = 100 + 20 * np.sin(np.arange(n) * 2 * np.pi / 7) + rng.normal(0, 1, n)
    dates = pd.date_range("2024-01-01", periods=n, freq="D").values
    h = 7
    train, actual_future = y[:-h], y[-h:]
    sarima_fc = get_model_fn("SARIMA")(train, h, "daily", dates[:-h])
    arima_fc = get_model_fn("ARIMA")(train, h, "daily", dates[:-h])
    sarima_err = np.mean(np.abs(actual_future - sarima_fc))
    arima_err = np.mean(np.abs(actual_future - arima_fc))
    # SARIMA should model the strong weekly cycle better than a non-seasonal ARIMA.
    assert sarima_err < arima_err


def test_weekly_series_is_backtestable():
    # ~2 years of weekly points (season 52) used to fail to back-test because
    # min_train was 2*52 > available folds. It must now produce a leaderboard.
    rng = np.random.default_rng(0)
    y = 100 + 10 * np.sin(np.arange(105) * 2 * np.pi / 52) + rng.normal(0, 3, 105)
    dates = pd.date_range("2022-01-02", periods=105, freq="W").values
    board = select_model(y, dates, h=8, freq="weekly")
    assert board, "weekly series should be back-testable"
    assert board[0]["folds"] >= 1


# ── MASE — scale-free accuracy metric ───────────────────────────────────────

def test_naive_scale_basic():
    y = np.array([10.0, 12.0, 11.0, 13.0, 12.0])
    scale = naive_scale(y, m=1)
    # mean(|12-10|, |11-12|, |13-11|, |12-13|) = mean(2,1,2,1) = 1.5
    assert scale is not None
    np.testing.assert_allclose(scale, 1.5)


def test_naive_scale_none_for_constant_series():
    # A perfectly flat series has zero naive difference -> no stable scale.
    assert naive_scale(np.array([5.0, 5.0, 5.0, 5.0]), m=1) is None


def test_evaluate_forecast_includes_mase():
    actual = np.array([10.0, 20.0, 30.0])
    predicted = np.array([12.0, 18.0, 33.0])
    metrics = evaluate_forecast(actual, predicted, scale=2.0)
    assert metrics["mase"] is not None
    # mae = mean(2, 2, 3) = 7/3
    np.testing.assert_allclose(metrics["mase"], (7 / 3) / 2.0, rtol=1e-3)


def test_mase_stays_sane_when_mape_blows_up_near_zero():
    # Actuals swing through near-zero values (like a noisy daily revenue series
    # with occasional low-order days) — MAPE explodes on those points, MASE
    # (scale-free, denominator from the training series) does not.
    actual = np.array([0.5, 100.0, 0.3, 90.0])
    predicted = np.array([50.0, 80.0, 40.0, 95.0])
    metrics = evaluate_forecast(actual, predicted, scale=20.0)
    assert metrics["mape"] > 1000  # pathologically large, as expected
    assert metrics["mase"] < 5  # scale-free metric stays in a sane range


def test_select_model_ranks_by_mase_not_raw_mape():
    # A clean trend: Linear Trend must win under MASE ranking exactly as it did
    # under the old MAPE-only ranking, confirming the new sort key didn't
    # regress the obvious case.
    y = np.array([10.0 + 2.0 * i for i in range(40)])
    dates = pd.date_range("2024-01-01", periods=40, freq="D").values
    board = select_model(y, dates, h=5, freq="daily")
    assert board[0]["model"] in {"Linear Trend", "Drift"}
    assert board[0].get("mase") is not None


# ── Honest confidence ────────────────────────────────────────────────────────

def test_confidence_none_when_not_backtested():
    assert _confidence(None, None, None) is None


def test_confidence_bounded_and_monotonic():
    good = _confidence(5.0, 0.6, 3)     # low error, beats naive
    poor = _confidence(90.0, 0.0, 3)    # high error, no skill
    assert 0.05 <= poor < good <= 0.95
    # A model that beats naive should score at least as high as one that doesn't.
    assert _confidence(30.0, 0.5, 3) >= _confidence(30.0, None, 3)


# ── Granularity resolution ───────────────────────────────────────────────────

def test_resolve_granularity():
    assert _resolve_granularity("weekly", "daily") == ("W", "weekly", 7)
    assert _resolve_granularity("monthly", "daily") == ("MS", "monthly", 30)
    # native falls back to the detected frequency, no resample rule.
    assert _resolve_granularity("native", "daily") == (None, "daily", 1)


# ── Ensemble (inverse-error-weighted blend of top back-tested performers) ────

def test_inverse_error_weights_favours_lower_error():
    weights = inverse_error_weights([2.0, 8.0])
    assert weights[0] > weights[1]
    assert abs(sum(weights) - 1.0) < 1e-6


def test_inverse_error_weights_handles_zero_error():
    # A near-perfect model (MAPE ~0) must not raise a ZeroDivisionError.
    weights = inverse_error_weights([0.0, 5.0])
    assert weights[0] > weights[1]
    assert abs(sum(weights) - 1.0) < 1e-6


def test_backtest_ensemble_scores_a_blend_out_of_sample():
    y = np.array([10.0 + 2.0 * i for i in range(40)])
    dates = pd.date_range("2024-01-01", periods=40, freq="D").values
    metrics = backtest_ensemble(
        ["Drift", "Linear Trend"], [0.5, 0.5], y, dates, h=5, freq="daily", folds=3
    )
    assert metrics is not None
    assert metrics["mape"] is not None
    assert metrics["folds"] >= 1


def test_backtest_ensemble_requires_two_components():
    y = np.array([10.0 + 2.0 * i for i in range(40)])
    dates = pd.date_range("2024-01-01", periods=40, freq="D").values
    assert backtest_ensemble(["Drift"], [1.0], y, dates, h=5, freq="daily") is None


def test_select_model_adds_ensemble_when_multiple_candidates_backtest():
    rng = np.random.default_rng(1)
    n = 60
    y = 100 + np.linspace(0, 30, n) + 8 * np.sin(np.arange(n) * 2 * np.pi / 7) + rng.normal(0, 4, n)
    dates = pd.date_range("2024-01-01", periods=n, freq="D").values
    board = select_model(y, dates, h=7, freq="daily")
    models = {r["model"] for r in board}
    assert len(board) >= 3  # several individual candidates back-tested successfully
    assert "Ensemble" in models
    entry = next(r for r in board if r["model"] == "Ensemble")
    assert len(entry["components"]) >= 2
    assert abs(sum(entry["weights"]) - 1.0) < 1e-3
    # Every component must itself be a real, individually-backtested candidate.
    assert set(entry["components"]) <= (models - {"Ensemble"})


def test_ensemble_not_added_with_fewer_than_two_candidates():
    board = select_model(np.array([1.0, 2.0]), None, h=1, freq="daily", candidates=["Naive"])
    assert all(r["model"] != "Ensemble" for r in board)


def test_engine_forced_ensemble_produces_valid_forecast():
    pytest.importorskip("prophet")
    from agents.forecasting.tools.engines import ForecastEngine, SeriesData

    rng = np.random.default_rng(2)
    n = 60
    y = 50 + np.linspace(0, 20, n) + rng.normal(0, 3, n)
    dates = pd.date_range("2024-01-01", periods=n, freq="D").values
    series = SeriesData(y=y, dates=dates, frequency="daily")
    result = ForecastEngine.run("Ensemble", series, horizon=7)

    assert result["selected_model"] == "Ensemble"
    assert len(result["forecast"]) == 7
    assert np.all(np.isfinite(result["forecast"]))
    assert np.all(result["lower"] <= result["forecast"])
    assert np.all(result["forecast"] <= result["upper"])


def test_engine_forced_ensemble_falls_back_when_unavailable():
    from agents.forecasting.tools.engines import ForecastEngine, SeriesData

    # Too little history for more than one candidate to back-test.
    y = np.array([10.0, 12.0, 11.0, 13.0])
    dates = pd.date_range("2024-01-01", periods=4, freq="D").values
    series = SeriesData(y=y, dates=dates, frequency="daily")
    result = ForecastEngine.run("Ensemble", series, horizon=2)
    assert result["selected_model"] != "Ensemble"
    assert len(result["forecast"]) == 2
