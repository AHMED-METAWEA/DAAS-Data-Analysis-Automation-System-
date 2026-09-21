"""
Tests for the honesty layer: signal measurement, the reliability gate, and
drift detection against previously-issued forecasts.

This is the part of the system that decides whether a number should be shown
with confidence. Its failure mode is invisible to users — a confident-looking
forecast of something unforecastable — so the checks here matter more than the
usual defensive tests.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from agents.forecasting.tools.diagnostics import (
    assess_reliability,
    diagnose_series,
    drift_report,
    recommend_granularity,
    series_fingerprint,
)

# ── Measuring how much signal a series carries ───────────────────────────────


def test_pure_noise_is_recognised_as_unforecastable():
    rng = np.random.default_rng(0)
    diag = diagnose_series(rng.normal(100, 20, 200), "daily")
    assert diag.predictability < 0.2
    assert diag.verdict in {"weak", "noise-dominated"}


def test_clean_seasonal_signal_scores_high():
    n = 200
    y = 100 + 30 * np.sin(np.arange(n) * 2 * np.pi / 7)
    diag = diagnose_series(y, "daily")
    assert diag.seasonal_strength > 0.8
    assert diag.predictability > 0.5
    assert diag.verdict == "strong"


def test_strong_trend_is_detected():
    diag = diagnose_series(np.linspace(100, 500, 120), "daily")
    assert diag.trend_strength > 0.8


def test_noise_dominated_series_carries_an_explanatory_note():
    rng = np.random.default_rng(1)
    diag = diagnose_series(rng.normal(100, 50, 120), "daily")
    if diag.verdict == "noise-dominated":
        assert any("noise" in n.lower() for n in diag.notes)


def test_short_series_degrades_gracefully():
    diag = diagnose_series(np.array([1.0, 2.0, 3.0]), "daily")
    assert diag.n == 3 and diag.notes


# ── Granularity recommendation ───────────────────────────────────────────────


def test_recommends_a_coarser_granularity_when_it_carries_more_signal():
    rng = np.random.default_rng(2)
    # Daily: swamped by noise. Weekly: a clean trend, because aggregating
    # averages the noise away.
    daily = rng.normal(100, 90, 400)
    weekly = np.linspace(700, 1400, 57)
    rec, reason = recommend_granularity({"daily": daily, "weekly": weekly}, "daily")
    assert rec == "weekly"
    assert "noise" in reason


def test_no_recommendation_when_the_current_granularity_is_already_good():
    y = 100 + 30 * np.sin(np.arange(200) * 2 * np.pi / 7)
    rec, _ = recommend_granularity({"daily": y, "weekly": y[:28]}, "daily")
    assert rec is None


def test_never_recommends_a_granularity_with_too_few_periods():
    rng = np.random.default_rng(3)
    rec, _ = recommend_granularity(
        {"daily": rng.normal(100, 90, 400), "weekly": np.linspace(1, 2, 5)}, "daily"
    )
    assert rec is None


# ── The reliability gate ─────────────────────────────────────────────────────


def test_no_skill_over_baseline_is_unreliable():
    r = assess_reliability(skill=0.0, predictability=0.5, folds=5, coverage=0.8, n_periods=200)
    assert r.level == "unreliable"
    assert r.confidence_cap == 0.30
    assert any("naive baseline" in x for x in r.reasons)


def test_noise_dominated_series_is_unreliable_even_with_skill():
    r = assess_reliability(skill=0.4, predictability=0.03, folds=6, coverage=0.8, n_periods=300)
    assert r.level == "unreliable"


def test_thin_backtest_is_unreliable():
    r = assess_reliability(skill=0.5, predictability=0.6, folds=1, coverage=0.8, n_periods=200)
    assert r.level == "unreliable"


def test_marginal_skill_is_indicative_not_reliable():
    r = assess_reliability(skill=0.05, predictability=0.5, folds=5, coverage=0.8, n_periods=200)
    assert r.level == "indicative"
    assert r.confidence_cap == 0.65


def test_miscalibrated_interval_downgrades_to_indicative():
    r = assess_reliability(skill=0.4, predictability=0.6, folds=6, coverage=0.35, n_periods=200)
    assert r.level == "indicative"
    assert any("covered 35%" in x for x in r.reasons)


def test_a_genuinely_good_forecast_passes():
    r = assess_reliability(skill=0.4, predictability=0.6, folds=6, coverage=0.78, n_periods=200)
    assert r.level == "reliable"
    assert r.confidence_cap is None


def test_reliability_is_json_safe():
    import json

    json.dumps(assess_reliability(0.4, 0.6, 6, 0.78, n_periods=200).to_dict())


# ── Fingerprinting (has the data changed?) ───────────────────────────────────


def test_fingerprint_is_stable_and_change_sensitive():
    dates = pd.date_range("2024-01-01", periods=50, freq="D")
    y = np.arange(50, dtype=float)

    assert series_fingerprint(y, dates) == series_fingerprint(y.copy(), dates)

    changed = y.copy()
    changed[10] += 1.0
    assert series_fingerprint(changed, dates) != series_fingerprint(y, dates)
    # An appended period is a different series too.
    assert series_fingerprint(
        np.append(y, 99.0), pd.date_range("2024-01-01", periods=51, freq="D")
    ) != series_fingerprint(y, dates)


# ── Drift: has the model stopped working? ────────────────────────────────────


def _prior(n=10, yhat=100.0, lo=80.0, hi=120.0):
    return pd.DataFrame({
        "ds": pd.date_range("2025-01-01", periods=n, freq="D"),
        "yhat": yhat, "yhat_lower": lo, "yhat_upper": hi,
    })


def _actual(n=10, value=100.0):
    return pd.DataFrame({
        "ds": pd.date_range("2025-01-01", periods=n, freq="D"),
        "y": np.full(n, value),
    })


def test_healthy_when_live_error_matches_the_backtest():
    r = drift_report(_prior(), _actual(value=102.0), expected_mae=5.0)
    assert r.status == "healthy"
    assert r.error_ratio is not None and r.error_ratio < 1.5
    assert r.coverage == 1.0


def test_degraded_when_live_error_runs_away():
    r = drift_report(_prior(), _actual(value=160.0), expected_mae=5.0)
    assert r.status == "degraded"
    assert r.error_ratio > 2.0
    assert "re-run" in r.message.lower()
    assert r.bias == 60.0  # actual - predicted, so the series moved up


def test_watch_band_between_healthy_and_degraded():
    r = drift_report(_prior(), _actual(value=108.0), expected_mae=5.0)
    assert r.status == "watch"


def test_too_few_overlapping_points_is_not_a_verdict():
    r = drift_report(_prior(n=2), _actual(n=2, value=900.0), expected_mae=5.0)
    assert r.status == "insufficient"


def test_missing_pieces_degrade_without_raising():
    assert drift_report(pd.DataFrame(), _actual(), 5.0).status == "no_history"
    assert drift_report(_prior(), pd.DataFrame(), 5.0).status == "no_history"

    future_actuals = pd.DataFrame({
        "ds": pd.date_range("2030-01-01", periods=3, freq="D"), "y": [1.0, 2.0, 3.0],
    })
    assert drift_report(_prior(), future_actuals, 5.0).status == "no_overlap"
    # No stored back-test error means degradation cannot be judged, not that it passed.
    assert drift_report(_prior(), _actual(value=900.0), None).status == "unscored"


def test_drift_report_is_json_safe():
    import json

    json.dumps(drift_report(_prior(), _actual(value=160.0), 5.0).to_dict())
