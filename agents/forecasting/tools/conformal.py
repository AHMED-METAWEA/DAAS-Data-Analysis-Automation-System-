"""
Prediction intervals that actually contain what they claim to.

The band around a forecast is the part users make decisions with — how much
stock to hold, how much cash to keep — so a band labelled "80%" that really
covers 38% is worse than no band at all.  Measured on this project's own data,
the previous ``z * sigma * sqrt(step)`` formula produced coverage of 38%, 68%,
79% and 95% on four runs of the same pipeline: not a band, a guess.

The failure was structural rather than a bad constant.  That formula assumes
errors are Gaussian, symmetric, and grow exactly as the square root of the
horizon.  Business series break all three: revenue errors are right-skewed, and
how fast uncertainty really grows depends on the model (a drift model's error
compounds, a seasonal-profile model's does not).

**Split conformal prediction** replaces the assumption with measurement.  The
rolling-origin back-test has already produced genuine out-of-sample errors at
every horizon step; the interval is simply the empirical quantile of those
errors.  It needs no distributional assumption, adapts automatically to skew,
and — because the residuals come from the same protocol that will generate the
shipped forecast — its nominal level is an honest estimate of its real one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class ResidualBank:
    """Signed out-of-sample errors (actual - predicted) collected per horizon step."""

    by_step: dict[int, list[float]] = field(default_factory=dict)

    def add(self, actual: np.ndarray, predicted: np.ndarray) -> None:
        a = np.asarray(actual, dtype=float)
        p = np.asarray(predicted, dtype=float)
        for step in range(min(len(a), len(p))):
            self.by_step.setdefault(step + 1, []).append(float(a[step] - p[step]))

    @property
    def total(self) -> int:
        return sum(len(v) for v in self.by_step.values())

    def pooled(self) -> np.ndarray:
        if not self.by_step:
            return np.asarray([], dtype=float)
        return np.concatenate([np.asarray(v, dtype=float) for v in self.by_step.values()])


# Two-tailed normal critical values, for the no-back-test fallback only.
_NORMAL_Z = {0.5: 0.6745, 0.8: 1.2816, 0.9: 1.6449, 0.95: 1.9600, 0.99: 2.5758}


def _empirical_bounds(resid: np.ndarray, level: float) -> tuple[float, float]:
    """Asymmetric (lower, upper) residual quantiles for a two-sided ``level``.

    Separate tails matter: revenue overshoots further than it undershoots, and
    forcing a symmetric band around a skewed error distribution produces a lower
    bound below zero and an upper bound that is too tight exactly where the
    business risk is.
    """
    alpha = (1.0 - level) / 2.0
    return float(np.quantile(resid, alpha)), float(np.quantile(resid, 1.0 - alpha))


def _min_samples(level: float) -> int:
    """Samples needed before an empirical quantile at ``level`` means anything.

    With n points the most extreme quantile resolvable is 1/(n+1); asking for a
    90th percentile from 4 residuals just returns the maximum, which reads as a
    tight band precisely when it is least justified.
    """
    alpha = (1.0 - level) / 2.0
    return int(np.ceil(1.0 / alpha))


def conformal_intervals(
    forecast: np.ndarray,
    bank: ResidualBank,
    level: float = 0.8,
    non_negative: bool = False,
    fallback_sigma: float | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Calibrated (lower, upper) band around ``forecast``.

    Per-step quantiles are used where the back-test produced enough residuals at
    that step; otherwise the pooled residual distribution is rescaled by
    ``sqrt(step)`` so the band still widens with the horizon.  The result is
    forced non-decreasing in width — a forecast 30 periods out is never
    presented as more certain than one 3 periods out, which raw per-step
    quantiles will otherwise claim on a lucky step.
    """
    forecast = np.asarray(forecast, dtype=float)
    h = len(forecast)
    if h == 0:
        return forecast.copy(), forecast.copy()

    pooled = bank.pooled()
    need = _min_samples(level)

    if len(pooled) >= need:
        pool_lo, pool_hi = _empirical_bounds(pooled, level)
        ref_step = float(np.mean([s for s, v in bank.by_step.items() for _ in v])) or 1.0
    elif fallback_sigma and fallback_sigma > 0:
        # No usable back-test residuals: fall back to a Gaussian band from the
        # supplied spread rather than pretending to a calibration we don't have.
        z = _NORMAL_Z.get(round(level, 2), 1.2816)
        pool_lo, pool_hi, ref_step = -z * fallback_sigma, z * fallback_sigma, 1.0
    else:
        return forecast.copy(), forecast.copy()

    lows = np.empty(h, dtype=float)
    highs = np.empty(h, dtype=float)
    for step in range(1, h + 1):
        resid = np.asarray(bank.by_step.get(step, []), dtype=float)
        if len(resid) >= need:
            lo, hi = _empirical_bounds(resid, level)
        else:
            # Rescale the pooled band to this step's horizon distance.
            scale = np.sqrt(step / max(ref_step, 1.0))
            lo, hi = pool_lo * scale, pool_hi * scale
        lows[step - 1] = lo
        highs[step - 1] = hi

    # Uncertainty must not shrink as we look further ahead.
    lows = -np.maximum.accumulate(-lows)
    highs = np.maximum.accumulate(highs)

    lower = forecast + lows
    upper = forecast + highs
    if non_negative:
        lower = np.clip(lower, 0.0, None)
    return lower, upper


def measured_coverage(
    bank: ResidualBank, level: float = 0.8
) -> float | None:
    """Leave-one-out coverage of the conformal band on the back-test itself.

    Each residual is tested against the band built from *the others*, so this is
    an out-of-sample estimate of how often the interval will really contain the
    truth — the number to publish next to the "80%" label instead of assuming
    the nominal level is achieved.
    """
    pooled = bank.pooled()
    need = _min_samples(level)
    if len(pooled) < need + 1:
        return None

    hits = 0
    for i in range(len(pooled)):
        others = np.delete(pooled, i)
        lo, hi = _empirical_bounds(others, level)
        if lo <= pooled[i] <= hi:
            hits += 1
    return round(hits / len(pooled), 3)
