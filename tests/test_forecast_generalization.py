"""
Does the forecaster generalise beyond the dataset it was developed against?

Every accuracy decision in this package was measured on one sample dataset
(daily e-commerce orders). That is exactly the situation in which a system
quietly becomes fitted to its test set — tuned constants that happen to suit one
shape of data, and a pipeline that has never seen a series behave differently.

These tests build series whose properties are known *by construction* and assert
the pipeline reaches the right **conclusion**, not merely that it returns a
number. They cover shapes the development dataset never contained: intermittent
demand, exponential growth, negative values, rate metrics, regime changes,
constant series, non-English identifiers and histories too short to model.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from agents.forecasting.pipeline import ForecastPipeline

N = 200


def _run(
    df: pd.DataFrame,
    target: str,
    date_col: str = "date",
    granularity: str = "native",
    max_cost: str = "medium",
):
    # "medium" drops only the slowest families (SARIMA, STL-ARIMA, Prophet) and
    # keeps the whole pipeline — preparation, transforms, selection, conformal
    # intervals, the reliability gate. None of the properties asserted here
    # depend on those families being candidates, and it takes the suite from
    # minutes to seconds. `test_full_model_library_runs_end_to_end` covers the
    # unrestricted path.
    result = ForecastPipeline().run(
        df, targets=[target], horizon_days=30, standard_horizons=[30],
        granularity=granularity, store=False, max_cost=max_cost,
    )
    outputs = result.get("forecast_outputs") or []
    return result, (outputs[0] if outputs else None)


def _frame(values, start="2023-01-01", freq="D", col="revenue", date_col="date"):
    return pd.DataFrame({date_col: pd.date_range(start, periods=len(values), freq=freq), col: values})


@pytest.fixture(scope="module")
def rng():
    return np.random.default_rng(11)


# ── It should find real signal ───────────────────────────────────────────────


def test_clean_seasonal_series_is_forecast_confidently(rng):
    t = np.arange(N)
    y = 2000 + 5 * t + 400 * np.sin(t * 2 * np.pi / 7) + rng.normal(0, 60, N)
    _, out = _run(_frame(y), "revenue")

    assert out["reliability"] == "reliable"
    assert out["skill_score"] > 0.1, "a clean seasonal series must beat the naive baseline"
    assert out["predictability"] > 0.5
    assert out["horizon_total"] is not None


def test_exponential_growth_is_forecast_well(rng):
    # Multiplicative growth is a shape the development dataset never contained.
    # Asserted as an *outcome* rather than a mechanism: whether a transform is
    # needed depends on how much of the variance is level-dependent, and with
    # low multiplicative noise an exponential smoother handles the growth on the
    # raw scale. Pinning the transform here would be testing the route, not the
    # destination.
    t = np.arange(N)
    y = 1000 * np.exp(0.006 * t) * rng.lognormal(0, 0.05, N)
    _, out = _run(_frame(y, col="mrr"), "mrr")

    assert out["reliability"] == "reliable"
    assert out["skill_score"] > 0.1
    assert out["horizon_total"] > 0


@pytest.mark.parametrize("seed", [0, 1, 2, 3])
def test_transform_is_adopted_only_when_variance_scales_with_level(seed):
    """The transform decision itself, tested where the right answer is known.

    Multiplicative noise is what a log is *for*; constant additive noise is what
    it must leave alone. An earlier version scored transforms by the median error
    across a screening set, which cancelled a real ~4% gain on the two candidates
    actually in contention against the mediocre models that were never going to
    be selected — and so never transformed anything.
    """
    from agents.forecasting.tools.backtest import select_transform

    n = 300
    t = np.arange(n)
    dates = pd.date_range("2023-01-01", periods=n, freq="D").values
    rng = np.random.default_rng(seed)

    multiplicative = 1000 * np.exp(0.005 * t) * rng.lognormal(0, 0.30, n)
    homoscedastic = 5000 + 10 * t + rng.normal(0, 300, n)

    assert select_transform(
        multiplicative, dates, 30, "daily", folds=3, objective="rmsse"
    ).name in {"log1p", "sqrt"}
    assert select_transform(
        homoscedastic, dates, 30, "daily", folds=3, objective="rmsse"
    ).name == "none"


# ── It should refuse to invent signal ────────────────────────────────────────


def test_pure_noise_is_never_reported_as_reliable(rng):
    # The critical safety property. A confident forecast of noise is the one
    # failure a user cannot detect for themselves.
    _, out = _run(_frame(rng.normal(5000, 1500, N)), "revenue")

    assert out["reliability"] != "reliable"
    assert out["predictability"] < 0.15
    assert out["confidence_score"] <= 0.35


def test_constant_series_does_not_crash_or_claim_reliability():
    _, out = _run(_frame(np.full(N, 777.0)), "revenue")
    assert out is not None and not out.get("error")
    assert out["reliability"] != "reliable"


def test_history_too_short_is_refused_rather_than_modelled(rng):
    result, _ = _run(_frame(rng.normal(100, 10, 9)), "revenue")
    assert result["forecastable"] is False


def test_random_walk_is_caught_by_the_skill_check(rng):
    # A random walk has a highly *trackable level* — predictability alone rates
    # it well — but nothing beats the naive forecast. The two reliability checks
    # are complementary and this is the case that needs the second one.
    y = np.cumsum(rng.normal(0, 5, N)) + 5000
    _, out = _run(_frame(y), "revenue")
    assert out["reliability"] != "reliable" or out["skill_score"] > 0.1


# ── It should adapt to the shape of the metric ───────────────────────────────


def test_intermittent_demand_routes_to_demand_rate_models(rng):
    units = np.where(rng.random(N) < 0.12, rng.integers(1, 9, N), 0).astype(float)
    _, out = _run(_frame(units, col="units"), "units")

    assert out["data_quality"]["intermittent"] is True
    # Level/trend/seasonal models fit the zeros rather than the demand, so they
    # must not be candidates at all.
    candidates = {r["model"] for r in out["cv_results"]}
    assert not (candidates & {"ARIMA", "SARIMA", "ETS", "Prophet", "STL-ETS", "Theta"})


def test_rate_metric_is_averaged_not_summed(rng):
    t = np.arange(N)
    y = 50 + 8 * np.sin(t * 2 * np.pi / 7) + rng.normal(0, 2, N)
    _, out = _run(_frame(y, col="avg_order_value"), "avg_order_value")

    assert out["aggregation"] == "mean"
    # A 30-day average order value must stay on the scale of a single order.
    assert 20 < out["horizon_total"] < 100


def test_negative_values_block_domain_restricted_transforms(rng):
    # Profit swings through zero; log and sqrt are undefined there.
    _, out = _run(_frame(rng.normal(200, 900, N), col="profit"), "profit")
    assert out["transform"] == "none"


def test_weekly_native_data_is_handled(rng):
    y = 20000 + 120 * np.arange(120) + 3000 * np.sin(np.arange(120) * 2 * np.pi / 52)
    _, out = _run(_frame(y + rng.normal(0, 800, 120), freq="W-SUN"), "revenue",
                  granularity="weekly")
    assert out is not None and not out.get("error")
    assert out["granularity"] == "weekly"


def test_non_english_identifiers_are_supported(rng):
    # This project handles Arabic table/column names elsewhere; forecasting
    # must not be the place that breaks on them.
    y = 5000 + 3 * np.arange(N) + rng.normal(0, 200, N)
    df = _frame(y, col="الإيرادات", date_col="التاريخ")
    _, out = _run(df, "الإيرادات", date_col="التاريخ")
    assert out is not None and not out.get("error")
    assert out["horizon_total"] is not None


# ── It should notice when the data itself is the problem ─────────────────────


def test_mid_series_outage_is_detected_and_repaired(rng):
    t = np.arange(N)
    y = 4000 + 300 * np.sin(t * 2 * np.pi / 7) + rng.normal(0, 150, N)
    y[120:135] = 0.0  # a data-pipeline failure, not a collapse in demand
    _, out = _run(_frame(y), "revenue")

    assert out["anomalies_detected"] > 0
    assert any("anomal" in n.lower() for n in out["notes"])


def test_level_shift_is_reported(rng):
    y = np.concatenate([
        3000 + rng.normal(0, 200, N // 2),
        9000 + rng.normal(0, 200, N - N // 2),
    ])
    _, out = _run(_frame(y), "revenue")
    assert out["level_shift"] is not None


def test_missing_periods_are_filled_and_disclosed(rng):
    dates = pd.date_range("2023-01-01", periods=N, freq="D")
    keep = rng.random(N) > 0.15  # drop ~15% of days entirely
    df = pd.DataFrame({"date": dates[keep], "revenue": rng.normal(3000, 200, keep.sum())})
    _, out = _run(df, "revenue")

    assert out["data_quality"]["filled_periods"] > 0
    # The model must see the full calendar, not a compressed one.
    assert out["training_periods"] >= N - 2


# ── Uncertainty must stay calibrated across shapes ───────────────────────────


@pytest.mark.parametrize("kind", ["seasonal", "trend", "noisy"])
def test_interval_coverage_stays_near_nominal(rng, kind):
    t = np.arange(N)
    if kind == "seasonal":
        y = 2000 + 400 * np.sin(t * 2 * np.pi / 7) + rng.normal(0, 80, N)
    elif kind == "trend":
        y = 1000 + 8 * t + rng.normal(0, 120, N)
    else:
        y = rng.normal(3000, 700, N)

    _, out = _run(_frame(y), "revenue")
    coverage = out["measured_coverage"]
    assert coverage is not None
    # Conformal intervals are calibrated from observed out-of-sample error, so
    # coverage should land near its label whatever the series looks like.
    assert 0.6 <= coverage <= 0.95, f"{kind}: 80% band covered {coverage:.0%}"


def test_published_band_always_contains_the_published_total(rng):
    y = 1000 + 8 * np.arange(N) + rng.normal(0, 120, N)
    _, out = _run(_frame(y), "revenue")
    assert out["horizon_total_lower"] <= out["horizon_total"] <= out["horizon_total_upper"]


def test_full_model_library_runs_end_to_end(rng):
    """One unrestricted run, so the expensive families stay exercised."""
    t = np.arange(N)
    y = 2000 + 4 * t + 350 * np.sin(t * 2 * np.pi / 7) + rng.normal(0, 70, N)
    _, out = _run(_frame(y), "revenue", max_cost="expensive")

    candidates = {r["model"] for r in out["cv_results"]}
    assert {"SARIMA", "Prophet"} & candidates, "expensive families should be evaluated"
    assert out["reliability"] == "reliable"
    assert out["horizon_total"] is not None
