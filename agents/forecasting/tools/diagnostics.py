"""
Knowing what the data can and cannot tell us.

A forecasting system earns trust by being right about its own limits.  Two
questions decide whether a number on the screen is worth acting on, and neither
is answered by the model:

**Is there a signal here at all?**  Daily revenue for a shop taking a handful of
orders a day is dominated by arrival noise.  On this project's own data a model
allowed to *cheat* — smoothing with a centred window that sees the future, and
applying a day-of-week profile — still lands at MAE 441 against a naive
baseline of 433.  There is nothing left for a better model to capture, because
day-of-week explains 2.1% of the daily variance.  Reporting a confident daily
number there is not accuracy, it is theatre.  The same series aggregated to
30-day blocks moves from a ~76% coefficient of variation to ~27%, and its
step-to-step error falls to 15%.  The signal is real; it just does not live at
daily granularity.

**Has the world changed since we fitted?**  A model chosen on last quarter's
behaviour keeps producing confident numbers long after the business it
described stopped existing.  :func:`drift_report` compares what was actually
forecast against what happened, and says plainly when the error has left the
range the back-test predicted.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd


@dataclass
class SeriesDiagnostics:
    """Structural properties of a series, independent of any model."""

    n: int
    trend_strength: float = 0.0
    seasonal_strength: float = 0.0
    noise_share: float = 1.0
    cv: float = 0.0
    predictability: float = 0.0
    verdict: str = "unknown"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _decompose(y: np.ndarray, period: int):
    """STL parts (trend, seasonal, remainder); ``None`` when STL cannot run."""
    if period < 2 or len(y) < 2 * period + 1:
        return None
    try:
        from statsmodels.tsa.seasonal import STL

        res = STL(pd.Series(np.asarray(y, dtype=float)), period=period, robust=True).fit()
        return (
            np.asarray(res.trend, dtype=float),
            np.asarray(res.seasonal, dtype=float),
            np.asarray(res.resid, dtype=float),
        )
    except Exception:
        return None


def _out_of_sample_r2(y: np.ndarray, m: int) -> float:
    """How much better a *causal* simple predictor does than the running mean.

    This is the honest answer to "is there anything here to learn". The obvious
    alternative — reading predictability off an STL decomposition as
    ``1 - Var(remainder)/Var(y)`` — is measured **in sample**, and STL always
    decomposes something: its LOESS trend and seasonal components absorb about a
    third of the variance of pure white noise, which scores unforecastable noise
    at 0.32 and "moderate". A layer whose entire job is to stop the product
    presenting confident forecasts of noise cannot be fooled by noise.

    Predicting each point from only its own past, and scoring against the
    expanding mean, cannot be gamed that way: for white noise every candidate
    does *worse* than the running mean, so the score is negative and clips to 0.
    """
    y = np.asarray(y, dtype=float)
    n = len(y)
    # Warm-up long enough for the trailing predictors to mean something, but
    # never so long that it swallows the series: a weekly series has a seasonal
    # period of 52, and requiring 53 points of warm-up leaves two years of
    # weekly data with too few points left to score, silently reporting 0
    # predictability for a perfectly clean trend.
    start = max(min(m + 1, n // 3), n // 5, 4)
    if n - start < 8:
        return 0.0

    idx = np.arange(start, n)
    actual = y[idx]
    baseline = np.array([y[:i].mean() for i in idx])
    denom = float(np.sum((actual - baseline) ** 2))
    if denom <= 1e-12:
        return 0.0

    candidates = {
        "last": np.array([y[i - 1] for i in idx]),
        "trailing_m": np.array([y[max(0, i - m):i].mean() for i in idx]),
        "trailing_4m": np.array([y[max(0, i - 4 * m):i].mean() for i in idx]),
    }
    # Only when every index has a real prior season to look back at — a negative
    # index would wrap around to the end of the series and score the future.
    if 2 <= m <= start:
        candidates["seasonal"] = np.array([y[i - m] for i in idx])

    best = 0.0
    for pred in candidates.values():
        r2 = 1.0 - float(np.sum((actual - pred) ** 2)) / denom
        best = max(best, r2)
    return float(np.clip(best, 0.0, 1.0))


def _strength(component: np.ndarray, remainder: np.ndarray) -> float:
    """Wang/Hyndman feature: 1 - Var(remainder) / Var(component + remainder).

    Reads as "how much of the variation this component explains, once noise is
    accounted for" — 0 means the component is indistinguishable from noise, 1
    means the series is essentially that component.
    """
    denom = float(np.var(component + remainder))
    if denom <= 1e-12:
        return 0.0
    return float(np.clip(1.0 - float(np.var(remainder)) / denom, 0.0, 1.0))


def diagnose_series(y: np.ndarray, freq: str = "daily") -> SeriesDiagnostics:
    """Measure how much structure a series actually contains."""
    from agents.forecasting.tools.models import season_length

    y = np.asarray(y, dtype=float)
    n = len(y)
    diag = SeriesDiagnostics(n=n)
    if n < 8:
        diag.notes.append("Too short to characterise.")
        return diag

    mean = float(np.mean(y))
    diag.cv = round(float(np.std(y)) / abs(mean), 4) if abs(mean) > 1e-9 else 0.0

    m = season_length(freq, n)
    parts = _decompose(y, m)
    if parts is None:
        # No seasonal decomposition available — describe the trend only.
        x = np.arange(n, dtype=float)
        fit = np.polyval(np.polyfit(x, y, 1), x)
        total = float(np.var(y))
        share = float(np.var(y - fit)) / total if total > 1e-12 else 1.0
        diag.trend_strength = round(max(0.0, 1.0 - share), 4)
    else:
        # STL strengths stay as *descriptive* features — they say which kind of
        # structure dominates — but they are in-sample fits and so must not
        # decide whether the series is forecastable at all.
        trend, seasonal, remainder = parts
        diag.trend_strength = round(_strength(trend, remainder), 4)
        diag.seasonal_strength = round(_strength(seasonal, remainder), 4)

    # How much of the series a model could actually learn, measured out of
    # sample so no amount of in-sample flexibility can inflate it.
    diag.predictability = round(_out_of_sample_r2(y, m), 4)
    diag.noise_share = round(1.0 - diag.predictability, 4)

    if diag.predictability >= 0.5:
        diag.verdict = "strong"
    elif diag.predictability >= 0.2:
        diag.verdict = "moderate"
    elif diag.predictability >= 0.08:
        diag.verdict = "weak"
    else:
        diag.verdict = "noise-dominated"
        diag.notes.append(
            "Almost all variation at this granularity is period-to-period noise. "
            "Individual period forecasts will not be reliable regardless of model; "
            "use the horizon total, or forecast at a coarser granularity."
        )
    return diag


# Aggregating averages away noise, so a coarser bucket is nearly always more
# predictable. These floors stop that logic recommending a granularity that
# leaves too few points to fit or validate anything.
_MIN_PERIODS = {"daily": 30, "weekly": 16, "monthly": 12}
_COARSER = {"daily": "weekly", "weekly": "monthly", "monthly": None}


def recommend_granularity(
    series_by_granularity: dict[str, np.ndarray],
    current: str,
) -> tuple[str | None, str]:
    """Suggest a better granularity when the current one is mostly noise.

    Returns ``(recommended, reason)``; ``recommended`` is ``None`` when the
    current choice is already the right one.  Only ever recommends going
    *coarser*, and only when the coarser series both has enough periods to fit
    on and is materially more predictable — trading resolution for signal has
    to actually buy signal.
    """
    cur = series_by_granularity.get(current)
    if cur is None or len(cur) < 8:
        return None, ""

    cur_diag = diagnose_series(cur, current)
    if cur_diag.predictability >= 0.2:
        return None, ""

    candidate = _COARSER.get(current)
    while candidate:
        y = series_by_granularity.get(candidate)
        if y is not None and len(y) >= _MIN_PERIODS.get(candidate, 12):
            cand_diag = diagnose_series(y, candidate)
            if cand_diag.predictability >= max(0.2, cur_diag.predictability * 2):
                return candidate, (
                    f"At {current} granularity {cur_diag.noise_share:.0%} of the variation is "
                    f"noise; at {candidate} granularity that falls to "
                    f"{cand_diag.noise_share:.0%}. Forecasting {candidate} totals will be "
                    f"materially more reliable."
                )
        candidate = _COARSER.get(candidate)
    return None, ""


@dataclass
class Reliability:
    """Whether this forecast is fit to make decisions on."""

    level: str = "unknown"  # reliable | indicative | unreliable | unknown
    headline: str = ""
    reasons: list[str] = field(default_factory=list)
    confidence_cap: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def assess_reliability(
    skill: float | None,
    predictability: float | None,
    folds: int | None,
    coverage: float | None,
    nominal_coverage: float = 0.8,
    n_periods: int = 0,
    min_periods: int = 24,
) -> Reliability:
    """Grade a finished forecast on whether it should be acted on.

    Everything else in this package tries to make the number as good as the data
    allows. This decides whether "as good as the data allows" is good enough to
    put in front of someone — the difference between a tool that is accurate and
    a tool that is *safe*, because the failure mode users cannot detect is a
    confident-looking forecast of something unforecastable.

    A forecast is downgraded when it fails to beat a naive baseline out of
    sample, when the series is mostly noise at this granularity, when the
    back-test was too thin to mean much, or when the interval demonstrably does
    not cover what it claims.
    """
    reasons: list[str] = []
    unreliable = False
    indicative = False

    if skill is not None:
        if skill <= 0.0:
            unreliable = True
            reasons.append(
                "The selected model did not beat a naive baseline out of sample — "
                "there is no evidence it captures anything real in this series."
            )
        elif skill < 0.10:
            indicative = True
            reasons.append(
                f"Only {skill * 100:.0f}% more accurate than a naive baseline; the "
                "advantage over simply repeating recent values is small."
            )

    if predictability is not None:
        if predictability < 0.08:
            unreliable = True
            reasons.append(
                f"Only {predictability:.0%} of the variation at this granularity is "
                "structure — the rest is period-to-period noise that no model can predict."
            )
        elif predictability < 0.20:
            indicative = True
            reasons.append(
                f"{1 - predictability:.0%} of the variation is noise; expect individual "
                "periods to be well off even when the horizon total is close."
            )

    if folds is not None and folds < 2:
        unreliable = True
        reasons.append(
            "Validated on fewer than two rolling-origin folds — not enough to "
            "distinguish a working model from a lucky one."
        )
    elif folds is not None and folds < 3:
        indicative = True
        reasons.append(f"Validated on only {folds} back-test folds.")

    if n_periods and n_periods < min_periods:
        indicative = True
        reasons.append(
            f"Only {n_periods} periods of history at this granularity "
            f"(≥{min_periods} recommended)."
        )

    if coverage is not None and abs(coverage - nominal_coverage) > 0.15:
        indicative = True
        reasons.append(
            f"The {nominal_coverage:.0%} interval actually covered {coverage:.0%} of "
            "back-test outcomes — treat the range as approximate."
        )

    if unreliable:
        return Reliability(
            level="unreliable",
            headline="Not reliable enough to plan against.",
            reasons=reasons,
            confidence_cap=0.30,
        )
    if indicative:
        return Reliability(
            level="indicative",
            headline="Directionally useful, but treat the exact figures as approximate.",
            reasons=reasons,
            confidence_cap=0.65,
        )
    return Reliability(
        level="reliable",
        headline="Validated out of sample and beating the naive baseline.",
        reasons=reasons or ["Passed every back-test reliability check."],
        confidence_cap=None,
    )


def series_fingerprint(y: np.ndarray, dates) -> str:
    """Stable short hash of a series' shape and extent.

    Used to tell "the same data as last run" from "the data changed", which is
    what decides whether a stored model choice can be reused or has to be
    re-validated. Rounded so floating-point noise alone never invalidates it.
    """
    y = np.asarray(y, dtype=float)
    parts = [str(len(y))]
    if len(y):
        parts += [
            f"{float(np.round(np.nansum(y), 4))}",
            f"{float(np.round(np.nanmean(y), 6))}",
            f"{float(np.round(np.nanstd(y), 6))}",
        ]
    if dates is not None and len(dates):
        parts += [str(pd.Timestamp(dates[0])), str(pd.Timestamp(dates[-1]))]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


@dataclass
class DriftReport:
    """How a previously-issued forecast held up against what actually happened."""

    compared_points: int = 0
    live_mae: float | None = None
    expected_mae: float | None = None
    error_ratio: float | None = None
    coverage: float | None = None
    bias: float | None = None
    status: str = "unknown"
    message: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def drift_report(
    prior_forecast: pd.DataFrame,
    actual: pd.DataFrame,
    expected_mae: float | None,
    nominal_coverage: float = 0.8,
) -> DriftReport:
    """Score a stored forecast against the actuals that have since arrived.

    ``prior_forecast`` needs ``ds``/``yhat`` (optionally ``yhat_lower``/
    ``yhat_upper``); ``actual`` needs ``ds``/``y``.  ``expected_mae`` is the
    back-test error the model was accepted on — the yardstick for whether live
    performance is normal or has degraded.

    This is what makes the system safe to leave running: a model whose live
    error has doubled against its own back-test is not producing forecasts any
    more, and the run should say so rather than quietly carrying on.
    """
    report = DriftReport()
    if prior_forecast is None or actual is None or prior_forecast.empty or actual.empty:
        report.status = "no_history"
        report.message = "No prior forecast to compare against."
        return report

    fc = prior_forecast.copy()
    ac = actual.copy()
    fc["ds"] = pd.to_datetime(fc["ds"])
    ac["ds"] = pd.to_datetime(ac["ds"])
    merged = fc.merge(ac[["ds", "y"]], on="ds", how="inner", suffixes=("", "_actual"))
    if merged.empty:
        report.status = "no_overlap"
        report.message = "The prior forecast covers no periods that have actuals yet."
        return report

    err = merged["y"].to_numpy(dtype=float) - merged["yhat"].to_numpy(dtype=float)
    report.compared_points = int(len(merged))
    report.live_mae = round(float(np.mean(np.abs(err))), 4)
    report.bias = round(float(np.mean(err)), 4)

    if {"yhat_lower", "yhat_upper"} <= set(merged.columns):
        inside = (merged["y"] >= merged["yhat_lower"]) & (merged["y"] <= merged["yhat_upper"])
        report.coverage = round(float(inside.mean()), 3)

    if expected_mae and expected_mae > 0:
        report.expected_mae = round(float(expected_mae), 4)
        report.error_ratio = round(report.live_mae / float(expected_mae), 3)

    # Thresholds are deliberately wide: with a handful of compared points, a
    # ratio of 1.4 is ordinary luck, not evidence that anything changed.
    if report.error_ratio is None:
        report.status = "unscored"
        report.message = (
            f"Compared {report.compared_points} period(s); live MAE {report.live_mae}. "
            "No back-test error stored for this metric, so degradation cannot be judged."
        )
    elif report.compared_points < 3:
        report.status = "insufficient"
        report.message = (
            f"Only {report.compared_points} period(s) available to compare — too few to "
            "judge whether the model still holds."
        )
    elif report.error_ratio > 2.0:
        report.status = "degraded"
        report.message = (
            f"Live error is {report.error_ratio:.1f}x the back-tested error over "
            f"{report.compared_points} periods. The series has moved away from the "
            "behaviour this model was selected on — re-run the forecast, and treat "
            "the current numbers as unreliable."
        )
    elif report.error_ratio > 1.5:
        report.status = "watch"
        report.message = (
            f"Live error is {report.error_ratio:.1f}x the back-tested error over "
            f"{report.compared_points} periods — worth watching, not yet alarming."
        )
    else:
        report.status = "healthy"
        report.message = (
            f"Live error is {report.error_ratio:.1f}x the back-tested error over "
            f"{report.compared_points} periods — the model is performing as expected."
        )
    return report
