"""
Tests for transforms, anomaly handling and conformal intervals.

These three decide whether the *number* and the *range around it* are honest.
Several of these tests pin bugs found by measurement during the rebuild: a
concave transform reported as unbiased, an outlier filter that erased Black
Friday, and an interval labelled 80% that covered anywhere from 38% to 99%.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from agents.forecasting.tools.anomalies import (
    detect_anomalies,
    detect_level_shift,
    repair,
)
from agents.forecasting.tools.conformal import (
    ResidualBank,
    conformal_intervals,
    measured_coverage,
)
from agents.forecasting.tools.transform import (
    IDENTITY,
    LOG1P,
    SQRT,
    candidate_transforms,
    get_transform,
    wrap,
)

# ── Transforms ───────────────────────────────────────────────────────────────


def test_transforms_round_trip():
    y = np.array([0.0, 1.0, 10.0, 100.0, 1000.0])
    for tr in (IDENTITY, LOG1P, SQRT):
        np.testing.assert_allclose(tr.inverse(tr.forward(y)), y, rtol=1e-6, atol=1e-9)


def test_concave_transforms_are_bias_corrected():
    # Both log and sqrt are concave, so naively inverting a fitted mean returns
    # a median — systematically below the mean. Reporting that as a revenue
    # forecast under-states every horizon total by a consistent margin.
    assert LOG1P.biased and SQRT.biased
    assert not IDENTITY.biased

    z = np.array([4.0, 4.0, 4.0])
    sigma = 1.0
    assert np.all(SQRT.inverse(z, sigma=sigma) > SQRT.inverse(z))
    assert np.all(LOG1P.inverse(z, sigma=sigma) > LOG1P.inverse(z))


def test_sqrt_bias_correction_matches_the_analytic_mean():
    # If z has mean mu and variance s^2 then E[z^2] = mu^2 + s^2.
    z, s = np.array([5.0]), 2.0
    np.testing.assert_allclose(SQRT.inverse(z, sigma=s), [5.0**2 + s**2])


def test_candidate_transforms_respects_the_domain():
    # Negative values cannot be logged or square-rooted.
    assert candidate_transforms(np.array([-5.0, 3.0, 10.0] * 5)) == [IDENTITY]
    # A near-constant series gains nothing and would pick one on noise.
    assert candidate_transforms(np.full(20, 50.0)) == [IDENTITY]
    # A normal positive, variable series gets the full menu.
    assert len(candidate_transforms(np.linspace(10, 200, 40))) == 3


def test_wrap_scores_on_the_original_scale():
    # A wrapped model must return original-scale values, or the leaderboard
    # cannot compare transformed and untransformed candidates.
    y = np.array([100.0 + 5 * i for i in range(30)])

    def last_value(train_y, h, freq, dates=None):
        return np.full(h, train_y[-1])

    out = wrap(last_value, SQRT)(y, 3, "daily", None)
    assert np.all(out > 100), "output should be on the revenue scale, not sqrt scale"


def test_wrap_rejects_non_finite_model_output():
    def broken(train_y, h, freq, dates=None):
        return np.full(h, np.nan)

    assert wrap(broken, LOG1P)(np.arange(1, 30, dtype=float), 3, "daily", None) is None


def test_get_transform_falls_back_to_identity():
    assert get_transform("nonexistent") is IDENTITY


# ── Anomalies ────────────────────────────────────────────────────────────────


def test_detects_a_local_outage_on_a_trending_series():
    # A global median/MAD filter cannot see this: the trend inflates the global
    # spread until a near-total outage in the middle looks ordinary.
    y = np.linspace(1000, 5000, 60)
    y[40] = 50.0  # the outage
    report = detect_anomalies(y, freq="daily")
    assert 40 in report.indices


def test_repair_winsorises_rather_than_erasing_the_event():
    # A 4-MAD filter flags genuine seasonal peaks (Black Friday). Replacing them
    # with the local level teaches the model the peak never happened; clipping
    # keeps it exceptional while removing its leverage.
    y = np.full(60, 100.0)
    y[30] = 900.0
    repaired = repair(y, freq="daily")
    assert repaired[30] < 900.0, "extreme leverage should be reduced"
    assert repaired[30] > 100.0, "the event must remain above the local level"


def test_repair_leaves_a_clean_series_untouched():
    rng = np.random.default_rng(0)
    y = 100 + rng.normal(0, 3, 80)
    np.testing.assert_allclose(repair(y, freq="daily"), y)


def test_never_flags_more_than_a_tenth_of_the_series():
    rng = np.random.default_rng(1)
    y = rng.normal(0, 1, 200)  # pure noise: the spread is the story, not any point
    assert detect_anomalies(y, freq="daily").count <= 20


def test_detection_is_causal():
    # Repairing a prefix must not depend on data that comes after it, or every
    # back-test score is inflated by leakage.
    rng = np.random.default_rng(2)
    y = np.concatenate([100 + rng.normal(0, 5, 60), np.full(20, 9999.0)])
    prefix_alone = repair(y[:60], freq="daily")
    prefix_within_full = repair(y, freq="daily")[:60]
    np.testing.assert_allclose(prefix_alone, prefix_within_full)


def test_short_series_is_left_alone():
    y = np.array([1.0, 50.0, 2.0])
    np.testing.assert_allclose(repair(y, freq="daily"), y)


def test_level_shift_detection():
    shifted = np.concatenate([np.full(40, 100.0), np.full(40, 300.0)])
    idx, score = detect_level_shift(shifted)
    assert idx is not None and score >= 2.0

    rng = np.random.default_rng(3)
    idx2, _ = detect_level_shift(100 + rng.normal(0, 5, 80))
    assert idx2 is None, "ordinary volatility is not a regime change"


# ── Conformal intervals ──────────────────────────────────────────────────────


def _bank_from(errors_per_fold: list[list[float]]) -> ResidualBank:
    bank = ResidualBank()
    for fold in errors_per_fold:
        bank.add(np.asarray(fold), np.zeros(len(fold)))
    return bank


def test_interval_contains_the_forecast_and_widens_with_horizon():
    rng = np.random.default_rng(4)
    bank = _bank_from([list(rng.normal(0, 10, 6)) for _ in range(8)])
    forecast = np.full(6, 100.0)
    lo, hi = conformal_intervals(forecast, bank, level=0.8)

    assert np.all(lo <= forecast) and np.all(forecast <= hi)
    widths = hi - lo
    assert np.all(np.diff(widths) >= -1e-9), "uncertainty must never shrink with horizon"


def test_coverage_is_close_to_the_nominal_level():
    # The core promise: a band labelled 80% should cover ~80%.
    rng = np.random.default_rng(5)
    bank = _bank_from([list(rng.normal(0, 10, 5)) for _ in range(20)])
    cov = measured_coverage(bank, level=0.8)
    assert cov is not None and 0.70 <= cov <= 0.90


def test_asymmetric_errors_produce_an_asymmetric_band():
    # Revenue overshoots further than it undershoots; a symmetric band would be
    # too tight exactly where the business risk is.
    bank = _bank_from([[1.0, 1.0, 1.0, 60.0, 1.0]] * 8)
    lo, hi = conformal_intervals(np.full(5, 100.0), bank, level=0.8)
    assert (hi[0] - 100.0) > (100.0 - lo[0])


def test_non_negative_series_never_gets_a_negative_lower_bound():
    bank = _bank_from([[-500.0, -400.0, -600.0]] * 8)
    lo, _ = conformal_intervals(np.full(3, 10.0), bank, level=0.8, non_negative=True)
    assert np.all(lo >= 0.0)


def test_falls_back_to_a_gaussian_band_without_residuals():
    lo, hi = conformal_intervals(
        np.full(4, 100.0), ResidualBank(), level=0.8, fallback_sigma=10.0
    )
    assert np.all(hi > lo)
    # ~1.2816 sigma either side at one step.
    np.testing.assert_allclose(hi[0] - 100.0, 12.816, rtol=0.05)


def test_degenerate_inputs_do_not_raise():
    assert conformal_intervals(np.array([]), ResidualBank(), level=0.8)[0].size == 0
    lo, hi = conformal_intervals(np.full(3, 5.0), ResidualBank(), level=0.8)
    np.testing.assert_allclose(lo, hi)  # no information -> no claimed range
    assert measured_coverage(ResidualBank(), level=0.8) is None


def test_thin_residual_sets_do_not_produce_a_confidently_tight_band():
    # With 3 residuals a 90th percentile is just the maximum, which reads as a
    # tight band precisely when it is least justified.
    thin = _bank_from([[5.0, 5.0, 5.0]])
    wide = _bank_from([[5.0, 5.0, 5.0]] * 12)
    assert measured_coverage(thin, level=0.8) is None
    assert measured_coverage(wide, level=0.8) is not None


def test_bank_groups_residuals_by_horizon_step():
    bank = ResidualBank()
    bank.add(np.array([10.0, 20.0]), np.array([1.0, 2.0]))
    bank.add(np.array([30.0, 40.0]), np.array([3.0, 4.0]))
    assert bank.by_step[1] == [9.0, 27.0]
    assert bank.by_step[2] == [18.0, 36.0]
    assert bank.total == 4
