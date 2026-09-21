"""
The forecasting engine: choose a representation, choose a model, quantify the
uncertainty, and report honestly on all three.

``ForecastEngine.run`` is the single entry point.  For one series it:

1. repairs anomalous periods **causally**, so no fold ever sees the future;
2. chooses a variance-stabilising transform by back-test rather than by
   assumption;
3. back-tests every candidate model that the series can support, ranking on
   consistency across folds rather than pooled error;
4. applies a one-standard-error rule so a model only wins when it is
   *distinguishably* better, not luckier;
5. builds **conformal** prediction intervals from the observed out-of-sample
   errors, and reports their measured coverage rather than their label;
6. produces the horizon aggregate — the total over the horizon — which for a
   noisy business series is the quantity that is actually predictable.
"""

from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from agents.forecasting.tools.anomalies import (
    detect_anomalies,
    detect_level_shift,
)
from agents.forecasting.tools.backtest import (
    backtest_ensemble,
    backtest_model,
    objective_for,
    pick_winner,
    select_model,
    select_transform,
    skill_vs_naive,
)
from agents.forecasting.tools.conformal import (
    ResidualBank,
    conformal_intervals,
    measured_coverage,
)
from agents.forecasting.tools.diagnostics import assess_reliability, diagnose_series
from agents.forecasting.tools.models import (
    MODELS,
    candidate_models,
    freq_to_pandas,
    get_model_fn,
    is_available,
    season_length,
)
from agents.forecasting.tools.preparation import (  # re-exported for callers/tests
    aggregation_for,
    prepare_data,
)
from agents.forecasting.tools.transform import IDENTITY, get_transform, wrap

logger = logging.getLogger(__name__)

__all__ = [
    "ForecastEngine",
    "SeriesData",
    "aggregation_for",
    "prepare_data",
    "run_prophet",
]

# Nominal level for every published band.
INTERVAL_LEVEL = 0.8


@dataclass
class SeriesData:
    """A prepared series plus the context the engine needs to model it well."""

    y: np.ndarray
    dates: np.ndarray
    frequency: str = "daily"
    # "sum" for additive metrics (revenue, units), "mean" for rates (AOV, %).
    # Decides how the horizon is aggregated into a headline number.
    aggregation: str = "sum"
    intermittent: bool = False
    notes: list[str] = field(default_factory=list)


def _infer_frequency(dates: pd.Series) -> str:
    deltas = pd.Series(dates.sort_values()).diff().dropna()
    if deltas.empty:
        return "D"
    median = deltas.median()
    try:
        days = median.total_seconds() / 86400
    except Exception:
        return "D"
    if days < 1.5:
        return "H" if days < 0.08 else "D"
    if days < 4:
        return "D"
    if days < 20:
        return "W"
    return "MS"


def prophet_params(df_p: pd.DataFrame) -> dict[str, Any]:
    y = df_p["y"].values
    ds = df_p["ds"]
    n = len(df_p)
    freq = _infer_frequency(ds)

    cv = float(y.std() / y.mean()) if float(y.mean()) != 0 else 0
    if cv > 0.3:
        params: dict[str, Any] = {"seasonality_mode": "multiplicative"}
        sps = 20.0
    else:
        params = {"seasonality_mode": "additive"}
        sps = 15.0

    params["changepoint_prior_scale"] = 0.3
    params["changepoint_range"] = 0.9
    params["seasonality_prior_scale"] = sps
    params["uncertainty_samples"] = 300
    params["n_changepoints"] = min(25, max(10, n // 3))
    params["interval_width"] = INTERVAL_LEVEL
    params["daily_seasonality"] = False

    if freq == "D":
        params["weekly_seasonality"] = 12 if n >= 10 else False
        params["yearly_seasonality"] = 6 if n >= 180 else False
    elif freq in ("W", "MS"):
        params["weekly_seasonality"] = False
        params["yearly_seasonality"] = 5 if n >= (52 if freq == "W" else 12) else False
    else:
        params["weekly_seasonality"] = n >= 10
        params["yearly_seasonality"] = n >= 180

    if n < 20:
        params["changepoint_prior_scale"] = 0.5
        params["n_changepoints"] = max(5, n // 2)

    return params


def run_prophet(
    y: np.ndarray, dates: np.ndarray, horizon: int, freq_str: str = "D"
) -> dict[str, Any]:
    """Full Prophet fit with its own uncertainty samples.

    Retained for callers that specifically want Prophet's Bayesian interval.
    The engine itself uses :func:`conformal_intervals` for *every* model,
    including Prophet, so that one calibration method — measured against real
    out-of-sample errors — covers the whole model library.
    """
    from prophet import Prophet
    from sklearn.metrics import mean_absolute_error, mean_squared_error

    df_p = pd.DataFrame({"ds": pd.to_datetime(dates), "y": y})
    df_p = df_p.groupby("ds", as_index=False)["y"].mean().sort_values("ds").reset_index(drop=True)
    ds = df_p["ds"]

    params = prophet_params(df_p)
    safe_keys = {
        "growth", "changepoints", "n_changepoints", "changepoint_range",
        "changepoint_prior_scale", "seasonality_prior_scale",
        "yearly_seasonality", "weekly_seasonality", "daily_seasonality",
        "holidays", "seasonality_mode", "uncertainty_samples",
        "mcmc_samples", "interval_width",
    }
    model = Prophet(**{k: v for k, v in params.items() if k in safe_keys})

    span = (ds.max() - ds.min()).days
    if span >= 30:
        model.add_seasonality(name="monthly", period=30.5, fourier_order=8)
        model.add_seasonality(name="biweekly", period=14, fourier_order=5)
    if _infer_frequency(ds) == "D" and span >= 90:
        model.add_seasonality(name="quarterly", period=91.25, fourier_order=4)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model.fit(df_p)

    future = model.make_future_dataframe(periods=horizon, freq=freq_str)
    tail = model.predict(future).tail(horizon)
    fitted = model.predict(df_p[["ds"]])["yhat"].to_numpy(dtype=float)

    y_actual = df_p["y"].to_numpy(dtype=float)
    rmse = float(np.sqrt(mean_squared_error(y_actual, fitted[: len(df_p)])))
    mae = float(mean_absolute_error(y_actual, fitted[: len(df_p)]))

    last_date = pd.to_datetime(dates[-1])
    return {
        "forecast": tail["yhat"].to_numpy(dtype=float),
        "fitted": fitted,
        "dates": pd.date_range(start=last_date, periods=horizon + 1, freq=freq_str)[1:],
        "lower": tail["yhat_lower"].to_numpy(dtype=float),
        "upper": tail["yhat_upper"].to_numpy(dtype=float),
        "method": "Prophet",
        "metrics": {"rmse": round(rmse, 4), "mae": round(mae, 4), "mape": None},
    }


def _future_dates(dates: np.ndarray, horizon: int, freq: str) -> pd.DatetimeIndex:
    """Forward calendar stamps on the *same* grid the history sits on."""
    alias = freq_to_pandas(freq)
    last = pd.to_datetime(dates[-1])
    return pd.date_range(start=last, periods=horizon + 1, freq=alias)[1:]


def _point_forecast(
    selected: str,
    y: np.ndarray,
    dates: np.ndarray,
    horizon: int,
    freq: str,
    transform_name: str,
    ensemble_spec: dict[str, Any] | None = None,
) -> np.ndarray:
    """Refit the chosen model on the full (repaired) history."""
    tr = get_transform(transform_name)

    if selected == "Ensemble" and ensemble_spec and ensemble_spec.get("components"):
        names = ensemble_spec["components"]
        weights = np.asarray(ensemble_spec["weights"], dtype=float)
        subs = []
        for name in names:
            fn = wrap(get_model_fn(name) or get_model_fn("Naive"), tr)
            subs.append(np.asarray(fn(y, horizon, freq, dates), dtype=float))
        return np.average(np.stack(subs, axis=0), axis=0, weights=weights)

    fn = wrap(get_model_fn(selected) or get_model_fn("Naive"), tr)
    return np.asarray(fn(y, horizon, freq, dates), dtype=float)


def _aggregate_forecast(
    forecast: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
    fold_totals: list[float],
    aggregation: str,
    horizon: int,
    cv_h: int,
) -> dict[str, Any]:
    # ``forecast`` may run further than the horizon being reported (the chart
    # shows the longest requested horizon), so trim first: summing 90 periods
    # and labelling the result a 30-day total overstates it three-fold.
    forecast = np.asarray(forecast, dtype=float)[:horizon]
    lower = np.asarray(lower, dtype=float)[:horizon]
    upper = np.asarray(upper, dtype=float)[:horizon]
    """The headline number: what the metric totals (or averages) over the horizon.

    This is the quantity a business actually asks for — "how much revenue next
    month" — and it is far more predictable than any single period within it,
    because independent period-to-period noise partly cancels in the sum.  On
    this project's data the coefficient of variation falls from 0.76 for single
    days to 0.27 for 30-day blocks.

    Its uncertainty is taken from the back-test's own *aggregate* errors where
    enough folds exist.  Otherwise it falls back to summing the per-period
    bounds, which assumes errors move together — conservative, and the right
    direction to err in when the evidence is thin.
    """
    point = float(np.sum(forecast)) if aggregation == "sum" else float(np.mean(forecast))
    result: dict[str, Any] = {"value": point, "basis": aggregation}

    usable = [t for t in fold_totals if t is not None and np.isfinite(t)]
    if len(usable) >= 3:
        arr = np.asarray(usable, dtype=float)
        if aggregation != "sum":
            arr = arr / max(cv_h, 1)
        # Back-tested at cv_h steps; the requested horizon may be longer, and
        # aggregate error grows roughly in proportion to the periods summed.
        if aggregation == "sum" and cv_h > 0:
            arr = arr * (horizon / cv_h)
        alpha = (1.0 - INTERVAL_LEVEL) / 2.0
        result["lower"] = point + float(np.quantile(arr, alpha))
        result["upper"] = point + float(np.quantile(arr, 1.0 - alpha))
        result["basis_note"] = f"empirical, {len(usable)} back-test folds"
    else:
        if aggregation == "sum":
            result["lower"] = float(np.sum(lower))
            result["upper"] = float(np.sum(upper))
        else:
            result["lower"] = float(np.mean(lower))
            result["upper"] = float(np.mean(upper))
        result["basis_note"] = "summed period bounds (conservative)"

    # A band must contain the point it describes. When the back-test shows a
    # one-sided aggregate error the empirical quantiles can both land on the
    # same side of the forecast; widening to include it keeps the published
    # range interpretable instead of reporting a lower bound above the estimate.
    result["lower"] = min(result["lower"], point)
    result["upper"] = max(result["upper"], point)
    return result


# Guards on the back-test bias correction below. Deliberately strict: a
# correction estimated from a handful of folds is itself an estimate, and an
# unguarded one would simply transfer back-test noise into the forecast.
_BIAS_MIN_FOLDS = 4
_BIAS_SHRINK = 0.5      # apply only half of the measured bias
_BIAS_MAX = 0.15        # never move the forecast by more than 15%


def _bias_correction(fold_totals: list[float], predicted_total: float) -> tuple[float, str]:
    """Multiplicative correction for a model that misses the total one way.

    If every back-test fold under-shot the horizon total, that is not noise —
    it is a property of the model on this series, and shipping the uncorrected
    number means knowingly publishing a figure we have measured to be low.
    (This is what produced a "lower bound" sitting exactly on the point
    estimate: the band was carrying a bias the forecast should have absorbed.)

    Only a consistent, well-evidenced bias is corrected, only half of it is
    applied, and the total move is capped — so the correction can help a
    systematically skewed model without letting a noisy back-test steer a
    sound one.
    """
    usable = [t for t in fold_totals if t is not None and np.isfinite(t)]
    if len(usable) < _BIAS_MIN_FOLDS or abs(predicted_total) < 1e-9:
        return 1.0, ""
    signs = {np.sign(t) for t in usable if t != 0}
    if len(signs) != 1:
        return 1.0, ""  # folds disagree on direction — not a bias

    # fold totals hold (actual - predicted), so a positive median means the
    # model under-predicted and the forecast should move up.
    median_err = float(np.median(usable))
    raw = median_err / abs(predicted_total)
    factor = 1.0 + float(np.clip(raw * _BIAS_SHRINK, -_BIAS_MAX, _BIAS_MAX))
    if abs(factor - 1.0) < 0.01:
        return 1.0, ""
    direction = "under" if median_err > 0 else "over"
    return factor, (
        f"Back-test showed this model {direction}-predicts the horizon total in all "
        f"{len(usable)} folds; the forecast is scaled by {factor:.2f} to correct half "
        f"of that measured bias."
    )


def _error_phrase(entry: dict) -> str:
    """MASE-led error phrase — MASE is what actually drives selection (it
    doesn't blow up on near-zero/negative actuals the way MAPE can), so lead
    with it when available and keep MAPE alongside as the familiar % figure."""
    mase = entry.get("mase")
    mape = entry.get("mape")
    if mase is not None:
        return (
            f"back-test MASE {mase} (MAPE {mape}%)" if mape is not None
            else f"back-test MASE {mase}"
        )
    return f"back-test MAPE {mape}%"


def _auto_reason(
    selected: str, leaderboard: list[dict], cv_h: int, transform_name: str, was_tie: bool
) -> str:
    entry = next((r for r in leaderboard if r["model"] == selected), None)
    if not entry:
        return f"Auto-selected {selected} (insufficient history to back-test alternatives)."

    if selected == "Ensemble":
        blend = ", ".join(
            f"{c} {w * 100:.0f}%"
            for c, w in zip(entry.get("components", []), entry.get("weights", []))
        )
        head = f"Auto-selected an error-weighted Ensemble ({blend})"
    else:
        head = f"Auto-selected {selected}"

    parts = [
        f"{head}: {_error_phrase(entry)} across {entry['folds']} rolling-origin folds "
        f"(~{cv_h}-step horizon)."
    ]
    if transform_name and transform_name != "none":
        parts.append(
            f"Fitted on a {transform_name} scale, which the back-test showed stabilises "
            f"this series' variance."
        )
    if was_tie:
        parts.append(
            "Chosen over a nominally lower-error model that was within one standard "
            "error — the simpler model generalises more reliably."
        )

    skill = skill_vs_naive(leaderboard, selected)
    if skill is not None and selected not in ("Naive", "Seasonal Naive"):
        if skill > 0.02:
            parts.append(f"{skill * 100:.0f}% more accurate than the best naive baseline.")
        elif skill > -0.02:
            parts.append("Statistically level with the naive baseline on this series.")
        else:
            parts.append(
                f"Note: {abs(skill) * 100:.0f}% *worse* than the naive baseline out of sample."
            )
    elif selected in ("Naive", "Seasonal Naive"):
        parts.append("No candidate reliably beat this baseline on out-of-sample error.")
    return " ".join(parts)


def _adaptive_folds(n: int, h: int) -> int:
    """More back-test folds as history grows, for a more robust model ranking.

    Capped so folds never consume more than roughly half the series — a fold
    count that leaves almost no training window measures nothing useful.
    """
    if n >= 180:
        base = 6
    elif n >= 90:
        base = 5
    elif n >= 60:
        base = 4
    else:
        base = 3
    return max(2, min(base, max(1, n // (2 * max(h, 1)))))


def _strip_private(leaderboard: list[dict]) -> list[dict]:
    """Drop non-serialisable internals before the leaderboard leaves the engine."""
    return [{k: v for k, v in r.items() if not k.startswith("_")} for r in leaderboard]


class ForecastEngine:
    # "Auto" runs the back-test selector; the rest force a specific model
    # (including "Ensemble", the error-weighted blend of top performers).
    SUPPORTED_MODELS = ["Auto", "Ensemble", *MODELS.keys()]

    @staticmethod
    def run(
        model: str,
        series: SeriesData,
        horizon: int,
        max_cost: str = "expensive",
        report_horizon: int | None = None,
    ) -> dict[str, Any]:
        raw_y = np.asarray(series.y, dtype=float)
        dates = series.dates
        freq = series.frequency
        n = len(raw_y)
        aggregation = getattr(series, "aggregation", "sum")

        if n < 2:
            flat = np.full(max(horizon, 1), float(raw_y[-1]) if n else 0.0)
            return {
                "forecast": flat, "fitted": np.asarray([], dtype=float),
                "dates": _future_dates(dates, len(flat), freq) if n else pd.DatetimeIndex([]),
                "lower": flat, "upper": flat, "method": "Naive",
                "metrics": {"mae": None, "rmse": None, "mape": None, "mase": None},
                "selected_model": "Naive",
                "model_selection_reason": "Not enough history to fit or validate any model.",
                "cv_results": [], "skill_score": None, "diagnostics": {},
                "reliability": {
                    "level": "unreliable",
                    "headline": "Not reliable enough to plan against.",
                    "reasons": ["Not enough history to fit or validate any model."],
                    "confidence_cap": 0.3,
                },
                "anomalies": {"count": 0, "periods": []}, "transform": "none",
                "interval": {"level": INTERVAL_LEVEL, "method": "none", "measured_coverage": None},
                "aggregate": {"value": float(np.sum(flat)), "basis": aggregation},
            }

        # ── Structure and data-quality assessment ────────────────────────────
        diagnostics = diagnose_series(raw_y, freq)
        anomaly_report = detect_anomalies(raw_y, freq=freq)
        shift_idx, shift_score = detect_level_shift(raw_y)
        # The final fit trains on repaired history; every back-test fold repairs
        # its own training window independently so nothing leaks backwards.
        fit_y = anomaly_report.repaired

        # ── Back-test configuration ──────────────────────────────────────────
        # Validate at the horizon actually being asked for wherever the history
        # allows it, so the conformal residuals cover the steps we publish.
        cv_h = max(1, min(horizon, max(1, n // 4)))
        folds = _adaptive_folds(n, cv_h)
        candidates = candidate_models(n, freq, series.intermittent, max_cost=max_cost)

        # ── What "better" means for this metric ──────────────────────────────
        # An additive metric is published as a horizon total, so it is ranked on
        # squared error (whose optimum is the mean); a rate is ranked on
        # absolute error. Chosen once, then used consistently for the transform
        # screen, the leaderboard, the tie test and the skill score.
        objective = objective_for(aggregation)

        # ── Representation ───────────────────────────────────────────────────
        transform = (
            select_transform(
                raw_y, dates, cv_h, freq, folds=min(folds, 3), objective=objective
            )
            if n >= 24 else IDENTITY
        )

        leaderboard = select_model(
            raw_y, dates, cv_h, freq, candidates, folds=folds,
            transform=transform, objective=objective,
        )

        # ── Choose ───────────────────────────────────────────────────────────
        was_tie = False
        if model and model != "Auto":
            if model == "Ensemble":
                if any(r["model"] == "Ensemble" for r in leaderboard):
                    selected, reason = "Ensemble", "User-selected Ensemble (forced)."
                else:
                    selected = leaderboard[0]["model"] if leaderboard else "Naive"
                    reason = (
                        "Ensemble unavailable (fewer than 2 back-testable candidate models); "
                        f"used {selected} instead."
                    )
            elif is_available(model, n):
                selected, reason = model, f"User-selected {model} (forced)."
            else:
                selected = leaderboard[0]["model"] if leaderboard else "Naive"
                reason = (
                    f"{model} unavailable for this dataset "
                    f"(needs more history or a missing dependency); used {selected} instead."
                )
        else:
            winner = pick_winner(leaderboard)
            selected = winner["model"] if winner else "Naive"
            was_tie = bool(winner and leaderboard and winner is not leaderboard[0])
            reason = _auto_reason(selected, leaderboard, cv_h, transform.name, was_tie)

        sel_entry = next((r for r in leaderboard if r["model"] == selected), None)
        ensemble_spec = None
        if selected == "Ensemble" and sel_entry:
            ensemble_spec = {
                "components": sel_entry.get("components", []),
                "weights": sel_entry.get("weights", []),
            }

        # ── Calibration: residuals for the winner, at every published step ────
        from agents.forecasting.tools.evaluation import naive_scale, squared_naive_scale

        m = season_length(freq, n)
        scale, sq_scale = naive_scale(raw_y, m=m), squared_naive_scale(raw_y, m=m)
        if selected == "Ensemble" and ensemble_spec:
            cal = backtest_ensemble(
                ensemble_spec["components"], ensemble_spec["weights"], raw_y, dates,
                cv_h, freq, folds, scale=scale, sq_scale=sq_scale,
                transform=transform, collect_residuals=True,
            )
        else:
            fn = get_model_fn(selected) or get_model_fn("Naive")
            cal = backtest_model(
                wrap(fn, transform), raw_y, dates, cv_h, freq, folds,
                scale=scale, sq_scale=sq_scale, collect_residuals=True,
            )

        bank: ResidualBank = (cal or {}).get("_bank") or ResidualBank()
        fold_totals = [
            float(np.sum(v)) for v in
            _fold_total_errors(bank, cv_h)
        ]

        # ── Produce the forecast ─────────────────────────────────────────────
        forecast = _point_forecast(
            selected, fit_y, dates, horizon, freq, transform.name, ensemble_spec
        )

        # Correct a measured, one-directional miss on the horizon total. Applied
        # to the per-period path (not just the total) so the chart, the table and
        # the headline figure all remain consistent with one another.
        bias_factor, bias_note = _bias_correction(
            fold_totals, float(np.sum(forecast[: max(cv_h, 1)]))
        )
        if bias_factor != 1.0:
            forecast = forecast * bias_factor

        non_negative = bool(np.all(raw_y >= 0))
        fallback_sigma = (cal or sel_entry or {}).get("residual_std")
        lower, upper = conformal_intervals(
            forecast, bank, level=INTERVAL_LEVEL,
            non_negative=non_negative, fallback_sigma=fallback_sigma,
        )
        coverage = measured_coverage(bank, level=INTERVAL_LEVEL)

        skill = skill_vs_naive(leaderboard, selected)
        reliability = assess_reliability(
            skill=skill,
            predictability=diagnostics.predictability,
            folds=(cal or sel_entry or {}).get("folds"),
            coverage=coverage,
            nominal_coverage=INTERVAL_LEVEL,
            n_periods=n,
        )

        metrics = {
            "mae": (cal or sel_entry or {}).get("mae"),
            "rmse": (cal or sel_entry or {}).get("rmse"),
            "mape": (cal or sel_entry or {}).get("mape"),
            "mase": (cal or sel_entry or {}).get("mase"),
            "rmsse": (cal or sel_entry or {}).get("rmsse"),
        }

        notes = list(series.notes)
        notes.extend(diagnostics.notes)
        if bias_note:
            notes.append(bias_note)
        if reliability.level != "reliable":
            notes.extend(reliability.reasons)
        if anomaly_report.count:
            notes.append(
                f"{anomaly_report.count} anomalous period(s) detected and repaired for "
                f"model fitting (scored against the raw values, never the repaired ones)."
            )
        if shift_idx is not None and shift_idx < n:
            notes.append(
                f"A sustained level shift was detected around "
                f"{pd.Timestamp(dates[shift_idx]).date() if dates is not None else f'index {shift_idx}'} "
                f"({shift_score:.1f} pooled SD). History before it describes a different regime."
            )

        return {
            "forecast": forecast,
            "fitted": np.asarray([], dtype=float),
            "dates": _future_dates(dates, horizon, freq),
            "lower": lower,
            "upper": upper,
            "method": selected,
            "metrics": metrics,
            "selected_model": selected,
            "model_selection_reason": reason,
            "cv_results": _strip_private(leaderboard),
            "skill_score": skill,
            "reliability": reliability.to_dict(),
            "transform": transform.name,
            "bias_correction": round(float(bias_factor), 4),
            "diagnostics": diagnostics.to_dict(),
            "anomalies": {
                "count": anomaly_report.count,
                "periods": anomaly_report.summary(
                    pd.DatetimeIndex(pd.to_datetime(dates)) if dates is not None else None
                ),
            },
            "level_shift": (
                {"index": int(shift_idx), "score": shift_score}
                if shift_idx is not None else None
            ),
            "interval": {
                "level": INTERVAL_LEVEL,
                "method": "conformal" if bank.total else "gaussian-fallback",
                "measured_coverage": coverage,
                "residual_samples": bank.total,
            },
            "aggregate": _aggregate_forecast(
                forecast, lower, upper, fold_totals, aggregation,
                min(report_horizon or horizon, horizon), cv_h,
            ),
            "notes": notes,
        }


def _fold_total_errors(bank: ResidualBank, cv_h: int) -> list[np.ndarray]:
    """Regroup the per-step residual bank back into one array per fold.

    ``ResidualBank`` stores residuals keyed by horizon step, with folds appended
    in order — so the k-th entry at every step belongs to the k-th fold, and the
    aggregate error of a fold is the sum across its steps.
    """
    if not bank.by_step:
        return []
    n_folds = min(len(v) for v in bank.by_step.values())
    out = []
    for k in range(n_folds):
        out.append(
            np.asarray(
                [bank.by_step[s][k] for s in sorted(bank.by_step) if s <= cv_h],
                dtype=float,
            )
        )
    return out
