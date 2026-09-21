"""
Anomaly handling: keeping one bad period from poisoning the whole forecast.

Real operational data contains periods that are not demand signal at all — an
outage, a failed nightly export, a warehouse closure, a one-off bulk order.
This project's own sample data has three such weeks (e.g. the week ending
2025-03-16 holds 3 order rows and $365 of revenue, against $8.7k in the weeks
either side).  Left in the training window, a single period like that drags the
level estimate down, inflates every residual-based interval, and can decide
which model "wins" the back-test.

Two properties matter and are easy to get wrong:

**Local, not global.**  Judging outliers against the median of the entire
series only works on a flat series.  On anything trending or seasonal, the
trend itself looks like a deviation at the ends, so genuine anomalies in the
middle hide inside a hugely inflated global spread.  Detection here runs
against a *local* rolling level.

**Causal.**  A centred rolling window peeks at the future.  Repairing a
training window with a centred median leaks information the model would not
have had, which quietly inflates back-test scores — the exact opposite of what
a back-test is for.  Everything here uses trailing windows only, so a series
repaired at time *t* is identical whether or not later data exists.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class AnomalyReport:
    """Which periods looked wrong, and what was done about them."""

    mask: np.ndarray  # bool, True where the observation was treated as anomalous
    repaired: np.ndarray  # the series after repair
    indices: list[int]
    severity: list[float]  # robust z-score per flagged index

    @property
    def count(self) -> int:
        return int(self.mask.sum())

    def summary(self, dates: pd.DatetimeIndex | None = None) -> list[dict]:
        out = []
        for i, z in zip(self.indices, self.severity):
            entry: dict = {"index": int(i), "z": round(float(z), 2)}
            if dates is not None and i < len(dates):
                entry["date"] = str(pd.Timestamp(dates[i]).date())
            out.append(entry)
        return out


def _local_level(y: np.ndarray, window: int) -> np.ndarray:
    """Trailing rolling median — the local "normal" each point is judged against.

    The window is trailing (never centred) so this is safe to run inside a
    back-test fold, and it is seeded with an expanding median so early points
    still get a sensible reference instead of being dropped.
    """
    s = pd.Series(y, dtype=float)
    trailing = s.rolling(window, min_periods=2).median()
    return trailing.fillna(s.expanding(min_periods=1).median()).to_numpy()


def detect_anomalies(
    y: np.ndarray,
    freq: str = "daily",
    threshold: float = 4.0,
    min_n: int = 12,
) -> AnomalyReport:
    """Flag observations that are implausible against their local neighbourhood.

    Deviations are scored as robust z-scores of ``y - local_level`` using the
    MAD of those deviations, so the threshold means the same thing whether the
    series runs in tens or in millions.  ``threshold`` is deliberately loose
    (4 MADs): the goal is to catch outages and data errors, not to shave the
    natural volatility a forecast is supposed to represent.
    """
    y = np.asarray(y, dtype=float)
    n = len(y)
    empty = np.zeros(n, dtype=bool)
    if n < min_n:
        return AnomalyReport(mask=empty, repaired=y.copy(), indices=[], severity=[])

    window = {"daily": 14, "weekly": 8, "monthly": 6}.get(freq, 14)
    window = max(3, min(window, n // 3))

    level = _local_level(y, window)
    dev = y - level
    mad = float(np.median(np.abs(dev - np.median(dev)))) * 1.4826
    if mad <= 1e-9:
        # A perfectly regular series: fall back to spread around the level.
        mad = float(np.std(dev))
    if mad <= 1e-9:
        return AnomalyReport(mask=empty, repaired=y.copy(), indices=[], severity=[])

    z = np.abs(dev) / mad
    mask = z > threshold

    # Never flag so much that the "anomalies" are the series. If more than 10%
    # of points trip the threshold, the spread — not the points — is unusual,
    # and repairing them would be reshaping the data to taste.
    if mask.sum() > max(1, int(0.1 * n)):
        keep = np.argsort(z)[::-1][: max(1, int(0.1 * n))]
        mask = np.zeros(n, dtype=bool)
        mask[keep] = True
        mask &= z > threshold

    # Winsorise rather than replace. Pulling a flagged point all the way back to
    # the local level erases real events: on this project's data a 4-MAD filter
    # flags the 2023-11-26 Black Friday week, and replacing it teaches the model
    # that Black Friday never happened. Clipping to the threshold boundary keeps
    # the period exceptional — it stays the biggest week of the year — while
    # removing the leverage that lets one point dictate the level, the trend and
    # every residual-based interval.
    repaired = y.copy()
    if mask.any():
        boundary = level + np.sign(dev) * threshold * mad
        repaired[mask] = boundary[mask]

    idx = [int(i) for i in np.flatnonzero(mask)]
    return AnomalyReport(
        mask=mask, repaired=repaired, indices=idx, severity=[float(z[i]) for i in idx]
    )


def repair(y: np.ndarray, freq: str = "daily", threshold: float = 4.0) -> np.ndarray:
    """Convenience wrapper returning just the repaired series."""
    return detect_anomalies(y, freq=freq, threshold=threshold).repaired


def detect_level_shift(
    y: np.ndarray, min_segment: int = 8
) -> tuple[int | None, float]:
    """Find the most recent sustained step change in the mean, if any.

    A forecast trained across a regime change (a price rise, a new market, a
    channel switching on) averages two different businesses together and lands
    between them.  This returns the index where the level last shifted and the
    size of the shift relative to the pre-shift spread, so the caller can decide
    whether to train on the recent regime only.

    Deliberately conservative: it only reports a shift that both segments are
    long enough to evidence, and it compares against within-segment variability
    rather than raw magnitude, so ordinary volatility does not read as a regime.
    """
    y = np.asarray(y, dtype=float)
    n = len(y)
    if n < 3 * min_segment:
        return None, 0.0

    best_idx, best_score = None, 0.0
    # Only consider the back half: an old shift is already absorbed in the
    # history and is not a reason to throw data away.
    for cut in range(max(min_segment, n // 2), n - min_segment):
        left, right = y[:cut], y[cut:]
        pooled = np.sqrt((np.var(left) + np.var(right)) / 2.0)
        if pooled <= 1e-9:
            continue
        score = abs(float(np.mean(right) - np.mean(left))) / pooled
        if score > best_score:
            best_idx, best_score = cut, score

    # ~2 pooled standard deviations: a difference this large between two long
    # stretches is not something ordinary noise produces.
    if best_score < 2.0:
        return None, round(best_score, 3)
    return best_idx, round(best_score, 3)
