from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from agents.forecasting.forecast_models import ForecastState
from agents.forecasting.metric_discovery import rank_metrics
from agents.forecasting.schema import ForecastEvaluation, ForecastOutput
from agents.forecasting.storage import load_prior_forecast
from agents.forecasting.tools.diagnostics import (
    drift_report,
    recommend_granularity,
    series_fingerprint,
)
from agents.forecasting.tools.engines import (
    INTERVAL_LEVEL,
    ForecastEngine,
    SeriesData,
)
from agents.forecasting.tools.preparation import build_series
from agents.forecasting.validation import (
    MIN_HISTORY,
    _detect_date_column,
    _detect_numeric_metrics,
    _determine_frequency,
)
from agents.visualization.theme import PRIMARY, SEQUENCE, style_figure, to_rgba


def _business_impact(change_pct: float) -> str:
    a = abs(change_pct)
    if a > 15:
        return "high"
    if a > 5:
        return "medium"
    return "low"


# granularity -> (pandas resample rule, freq label, days per period)
_GRANULARITY = {
    "daily": ("D", "daily", 1),
    "weekly": ("W", "weekly", 7),
    "monthly": ("MS", "monthly", 30),
}


def _resolve_granularity(granularity: str, detected_freq: str) -> tuple[str | None, str, int]:
    g = (granularity or "native").lower()
    if g in _GRANULARITY:
        return _GRANULARITY[g]
    # "native"/"auto" -> forecast at the detected frequency, no resampling.
    fl = detected_freq if detected_freq in ("daily", "weekly", "monthly") else "daily"
    return (None, fl, {"daily": 1, "weekly": 7, "monthly": 30}[fl])


def _confidence(
    mape: float | None,
    skill: float | None,
    folds: int | None,
    mase: float | None = None,
    coverage: float | None = None,
    predictability: float | None = None,
) -> float | None:
    """Principled, honest back-test reliability in [0.05, 0.95].

    Four independent things have to hold before a forecast deserves confidence,
    and each can veto the others:

    * **Error size** — MASE where available (scale-free, and unlike MAPE it does
      not explode when actuals pass near zero), MAPE only as a fallback.
    * **Skill** — beating a naive baseline. A model with small error on an easy
      series has demonstrated nothing.
    * **Calibration** — whether the 80% interval really covered 80%. A band that
      covers 40% means the uncertainty estimate is wrong, whatever the point
      error says.
    * **Predictability** — how much of the series is structure rather than
      noise. Confidently forecasting noise is the failure this guards against.

    ``None`` when the model could not be back-tested — the UI then says
    "not back-tested" instead of inventing a number.
    """
    if mase is not None:
        acc = 1.0 / (1.0 + max(float(mase), 0.0))
    elif mape is not None:
        acc = 1.0 / (1.0 + max(float(mape), 0.0) / 100.0)
    else:
        return None

    skill_adj = (
        0.85 if skill is None
        else float(np.clip(0.7 + 0.3 * np.clip(skill, 0.0, 1.0), 0.7, 1.0))
    )
    conf = acc * skill_adj

    if coverage is not None:
        # Penalise miscalibration in both directions: a band that covers 99%
        # when it claims 80% is uninformatively wide, not reassuringly safe.
        conf *= float(np.clip(1.0 - abs(float(coverage) - INTERVAL_LEVEL), 0.5, 1.0))
    if predictability is not None and predictability < 0.1:
        # Series is essentially noise at this granularity — cap the claim.
        conf = min(conf, 0.35)
    if folds is not None and folds < 3:
        conf *= 0.9
    return round(float(np.clip(conf, 0.05, 0.95)), 2)


# Rolling-average window per granularity (in periods) + human label.
_ROLL_WINDOW = {"daily": (7, "7-day avg"), "weekly": (4, "4-week avg"),
                "monthly": (3, "3-month avg")}


def _build_chart(
    dates: np.ndarray,
    y: np.ndarray,
    forecast_dates: pd.DatetimeIndex,
    forecast: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
    target: str,
    model_name: str = "Forecast",
    freq_label: str = "daily",
) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=dates, y=y, mode="lines+markers", name="Historical",
        line=dict(color=PRIMARY, width=1.5), marker=dict(size=4),
    ))

    window, roll_label = _ROLL_WINDOW.get(freq_label, (7, "7-period avg"))
    if len(y) >= window * 2:
        roll = pd.Series(y).rolling(window, min_periods=1).mean()
        fig.add_trace(go.Scatter(
            x=dates, y=roll, mode="lines", name=roll_label,
            line=dict(width=2.5, color=SEQUENCE[2], dash="dot"),
        ))

    # Anchor the forecast (and its band) to the last actual point so the
    # dashed line continues from the history instead of floating in space —
    # essential when the horizon is a single period, which otherwise renders
    # as one disconnected dot with an invisible confidence band.
    if len(dates) and len(forecast):
        last_date = pd.Timestamp(dates[-1])
        last_val = float(y[-1])
        fc_x = pd.Series([last_date, *pd.to_datetime(forecast_dates)])
        fc_y = pd.Series([last_val, *np.asarray(forecast, dtype=float)])
        band_upper = pd.Series([last_val, *np.asarray(upper, dtype=float)])
        band_lower = pd.Series([last_val, *np.asarray(lower, dtype=float)])
    else:
        fc_x = pd.Series(pd.to_datetime(forecast_dates))
        fc_y = pd.Series(np.asarray(forecast, dtype=float))
        band_upper = pd.Series(np.asarray(upper, dtype=float))
        band_lower = pd.Series(np.asarray(lower, dtype=float))

    forecast_color = SEQUENCE[6]
    fig.add_trace(go.Scatter(
        x=pd.concat([fc_x, fc_x[::-1]]),
        y=pd.concat([band_upper, band_lower[::-1]]),
        fill="toself", fillcolor=to_rgba(forecast_color, 0.18),
        line=dict(color="rgba(0,0,0,0)"), name="Confidence (80%)",
        hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(
        x=fc_x, y=fc_y, mode="lines+markers", name="Forecast",
        line=dict(color=forecast_color, width=2.5, dash="dash"),
        marker=dict(size=7, symbol="diamond"),
    ))

    # Mark where history ends and the forecast begins.
    if len(dates):
        fig.add_vline(x=pd.Timestamp(dates[-1]), line_dash="dot",
                      line_color="gray", opacity=0.6)
        fig.add_annotation(x=pd.Timestamp(dates[-1]), yref="paper", y=1.02,
                           text="forecast →", showarrow=False,
                           font=dict(size=11, color="gray"))

    fig.update_layout(
        hovermode="x unified",
        xaxis_title="Date",
        yaxis_title=target,
        legend=dict(orientation="h", yanchor="bottom", y=1.05, xanchor="right", x=1),
    )
    style_figure(
        fig,
        title=f"{target.replace('_', ' ').title()} — {model_name} Forecast",
        subtitle=f"{freq_label.title()} granularity",
        height=380,
    )
    return fig


def _forecast_one_target(
    target: str,
    df: pd.DataFrame,
    date_col: str,
    freq: str,
    horizon: int,
    horizons: list[int],
    run_id: str,
    model: str = "Auto",
    granularity: str = "native",
    max_cost: str = "expensive",
) -> dict[str, Any]:
    rule, freq_label, period_days = _resolve_granularity(granularity, freq)

    prepared = build_series(df, date_col, target, freq_label)
    y = prepared.y
    dates = prepared.dates.values

    # Horizons arrive in days; convert to the number of periods at this granularity.
    def _periods(days: int) -> int:
        return max(1, int(round(days / period_days)))

    horizon_p = _periods(horizon)
    horizons_p = {h: _periods(h) for h in horizons}
    max_h = max([horizon_p, *horizons_p.values()])

    series = SeriesData(
        y=y, dates=dates, frequency=freq_label,
        aggregation=prepared.aggregation,
        intermittent=prepared.intermittent,
        notes=prepared.notes,
    )
    # Compute over `max_h` (the chart shows the longest requested horizon) but
    # report the aggregate for the primary horizon the user asked for.
    result = ForecastEngine.run(
        model, series, max_h, max_cost=max_cost, report_horizon=horizon_p
    )

    fv = np.asarray(result["forecast"], dtype=float)
    agg = prepared.aggregation
    reduce = (lambda a: float(np.sum(a))) if agg == "sum" else (lambda a: float(np.mean(a)))

    # Horizon values use the metric's own aggregation: a 30-day revenue figure
    # is the *total* over those 30 days, not the average day within them. The
    # average-of-days reading made "forecasted revenue" a number no one in the
    # business had ever asked for, and made change_percent incomparable to it.
    # Several requested horizons can collapse onto the same number of periods —
    # at monthly granularity both "7 days" and "30 days" are one month — and
    # listing the identical figure under two labels reads as a coincidence
    # rather than as the rounding it is. Keep one label per distinct period
    # count: the day count that best describes the span actually covered.
    best_label_for: dict[int, int] = {}
    for h, hp in horizons_p.items():
        incumbent = best_label_for.get(hp)
        if incumbent is None or abs(h - hp * period_days) < abs(incumbent - hp * period_days):
            best_label_for[hp] = h

    horizon_values: dict[str, float] = {}
    if len(fv):
        for hp, h in sorted(best_label_for.items()):
            horizon_values[f"{h}_days"] = round(reduce(fv[: min(hp, len(fv))]), 2)

    # Like-for-like baseline: the same aggregate over the *equivalent trailing
    # window*. Comparing a 30-day forecast total against a single last day (the
    # previous behaviour) produced a change_percent driven almost entirely by
    # which day the data happened to end on.
    window = min(horizon_p, len(y))
    current_val = reduce(y[-window:]) if window else 0.0
    forecast_val = horizon_values.get(
        f"{horizon}_days", reduce(fv[:horizon_p]) if len(fv) else current_val
    )
    change_pct = (
        round((forecast_val - current_val) / abs(current_val) * 100, 2) if current_val else 0.0
    )

    metrics = result.get("metrics", {})
    selected_model = result.get("selected_model", "Naive")
    cv_results = result.get("cv_results", [])
    skill_score = result.get("skill_score")
    sel = next((r for r in cv_results if r.get("model") == selected_model), None)
    folds = sel.get("folds") if sel else None
    interval = result.get("interval", {})
    diagnostics = result.get("diagnostics", {})
    reliability = result.get("reliability", {})
    confidence = _confidence(
        metrics.get("mape"), skill_score, folds,
        mase=metrics.get("mase"),
        coverage=interval.get("measured_coverage"),
        predictability=diagnostics.get("predictability"),
    )
    # A forecast that failed its reliability checks must not present a high
    # confidence number, whatever the error metrics happen to say.
    cap = reliability.get("confidence_cap")
    if confidence is not None and cap is not None:
        confidence = round(min(confidence, float(cap)), 2)

    # Would a coarser granularity be materially more forecastable? Answered by
    # actually building the alternatives and measuring them, not by a rule.
    recommended_granularity, granularity_reason = "", ""
    if reliability.get("level") != "reliable":
        alternatives = {freq_label: y}
        for other in ("weekly", "monthly"):
            if other != freq_label:
                try:
                    alternatives[other] = build_series(df, date_col, target, other).y
                except Exception:
                    pass
        rec, why = recommend_granularity(alternatives, freq_label)
        if rec:
            recommended_granularity, granularity_reason = rec, why

    trend = "growing" if change_pct > 5 else "declining" if change_pct < -5 else "stable"

    output = ForecastOutput(
        metric=target,
        current_value=round(current_val, 2),
        forecasted_value=round(forecast_val, 2),
        change_percent=change_pct,
        confidence_score=confidence,
        forecast_horizon=f"{horizon}_days",
        granularity=freq_label,
        training_periods=len(y),
        selected_model=selected_model,
        model_selection_reason=result.get("model_selection_reason", ""),
        evaluation=ForecastEvaluation(
            mae=metrics.get("mae"),
            rmse=metrics.get("rmse"),
            mape=metrics.get("mape"),
            mase=metrics.get("mase"),
        ),
        business_impact=_business_impact(change_pct),
        trend=trend,
        horizons=horizon_values,
        cv_results=cv_results,
        skill_score=skill_score,
        aggregation=agg,
        horizon_total=result.get("aggregate", {}).get("value"),
        horizon_total_lower=result.get("aggregate", {}).get("lower"),
        horizon_total_upper=result.get("aggregate", {}).get("upper"),
        baseline_window_value=round(current_val, 2),
        transform=result.get("transform", "none"),
        interval_level=interval.get("level", INTERVAL_LEVEL),
        interval_method=interval.get("method", ""),
        measured_coverage=interval.get("measured_coverage"),
        predictability=diagnostics.get("predictability"),
        trend_strength=diagnostics.get("trend_strength"),
        seasonal_strength=diagnostics.get("seasonal_strength"),
        signal_verdict=diagnostics.get("verdict", "unknown"),
        anomalies_detected=result.get("anomalies", {}).get("count", 0),
        anomaly_periods=result.get("anomalies", {}).get("periods", []),
        level_shift=result.get("level_shift"),
        data_quality=prepared.to_dict(),
        series_fingerprint=series_fingerprint(y, prepared.dates),
        notes=result.get("notes", []),
        reliability=reliability.get("level", "unknown"),
        reliability_headline=reliability.get("headline", ""),
        reliability_reasons=reliability.get("reasons", []),
        recommended_granularity=recommended_granularity,
        granularity_reason=granularity_reason,
    )

    forecast_dates = result["dates"][:horizon_p]
    forecast_values = fv[:horizon_p]
    lower = result["lower"][:horizon_p]
    upper = result["upper"][:horizon_p]

    # Chart the FULL computed horizon (max of all requested horizons), not just
    # the primary one: at monthly granularity a 30-day horizon is one period,
    # which is unreadable on its own. The numeric card still reports the
    # primary horizon.
    fig = _build_chart(
        dates, y,
        result["dates"], fv, result["lower"], result["upper"],
        target, selected_model, freq_label=freq_label,
    )

    forecast_df = pd.DataFrame({
        "ds": forecast_dates,
        "yhat": forecast_values,
        "yhat_lower": lower,
        "yhat_upper": upper,
    })

    history_rows = [
        {"run_id": run_id, "metric_name": target, "ds": str(d), "y": float(v), "kind": "actual"}
        for d, v in zip(dates, y)
    ]
    for d, v, lo, hi in zip(forecast_df["ds"], forecast_df["yhat"], forecast_df["yhat_lower"], forecast_df["yhat_upper"]):
        history_rows.append({
            "run_id": run_id,
            "metric_name": target,
            "ds": str(d),
            "y": float(v),
            "yhat_lower": float(lo),
            "yhat_upper": float(hi),
            "kind": "forecast",
        })

    return {
        "output": output.to_agent_dict(),
        "fig": fig,
        "forecast_df": forecast_df,
        "metrics": {"metric": target, **metrics},
        "history_rows": history_rows,
    }


def drift_node(state: ForecastState) -> dict:
    """Score each metric's *previous* forecast against what has since happened.

    This is the half of "what happens when the data changes" that a back-test
    cannot cover. A back-test proves the model worked on history; only a
    comparison against a forecast issued before the fact proves it is still
    working now. When live error runs far ahead of the back-tested error, the
    series has moved away from the behaviour the model was chosen on, and the
    run says so instead of quietly continuing to publish.

    Degrades silently to "no drift information" whenever there is no history
    store or no earlier run — drift reporting is an enhancement to a forecast,
    never a precondition for producing one.
    """
    outputs = state.get("forecast_outputs") or []
    if not outputs:
        return {}

    df = state.get("df")
    date_col = state.get("date_column", "")
    if df is None or not date_col:
        return {}

    project_id = state.get("project_id") or None
    run_id = state.get("run_id") or None

    enriched: list[dict] = []
    any_drift = False
    for record in outputs:
        record = dict(record)
        metric = record.get("metric", "")
        if not metric or record.get("error"):
            enriched.append(record)
            continue
        try:
            prior, expected_mae = load_prior_forecast(
                metric, project_id=project_id, exclude_run_id=run_id
            )
            if prior is None or prior.empty:
                enriched.append(record)
                continue

            actual = build_series(df, date_col, metric, record.get("granularity", "daily"))
            report = drift_report(
                prior,
                pd.DataFrame({"ds": actual.dates, "y": actual.y}),
                expected_mae=expected_mae or (record.get("evaluation") or {}).get("mae"),
                nominal_coverage=record.get("interval_level", INTERVAL_LEVEL),
            )
            record["drift"] = report.to_dict()
            if report.status in ("degraded", "watch"):
                any_drift = True
                record["notes"] = [*record.get("notes", []), report.message]
            if report.status == "degraded":
                # Live evidence that the model has stopped working outranks a
                # back-test run on data that predates the change.
                record["reliability"] = "unreliable"
                record["reliability_headline"] = (
                    "Live performance has degraded since the last run."
                )
                record["reliability_reasons"] = [
                    report.message, *record.get("reliability_reasons", []),
                ]
                if record.get("confidence_score") is not None:
                    record["confidence_score"] = min(record["confidence_score"], 0.30)
        except Exception as exc:  # never let drift reporting break a run
            record["drift"] = {"status": "error", "message": str(exc)}
        enriched.append(record)

    return {"forecast_outputs": enriched, "drift_detected": any_drift}


def validate_node(state: ForecastState) -> dict:
    df = state["df"]
    date_col = _detect_date_column(df)

    if date_col is None:
        return {
            "date_column": "",
            "frequency": "",
            "history_length": 0,
            "forecastable": False,
            "validation": {"frequency": "", "history_length": 0, "forecastable": False, "missing_dates_count": 0},
            "validation_message": "No date/time column detected.",
            "missing_dates_count": 0,
            "available_metrics": [],
        }

    dates = pd.to_datetime(df[date_col], errors="coerce").dropna()
    if dates.empty:
        return {
            "date_column": date_col,
            "frequency": "",
            "history_length": 0,
            "forecastable": False,
            "validation": {"frequency": "", "history_length": 0, "forecastable": False, "missing_dates_count": 0},
            "validation_message": f"Date column '{date_col}' has no valid dates.",
            "missing_dates_count": 0,
            "available_metrics": [],
        }

    freq = _determine_frequency(dates)
    rows = int(len(dates))
    metrics = _detect_numeric_metrics(df, date_col)
    min_req = MIN_HISTORY.get(freq, 30)

    # Forecastability is a question about *periods*, not rows. Counting raw
    # transaction rows as "history" made 500 orders spread over five days look
    # like 500 periods of daily history, so a series with nothing to learn from
    # sailed through validation and every model downstream was fitted on five
    # points. Build the real series and count what a model will actually see.
    periods, gaps = 0, 0
    if metrics:
        representative = rank_metrics(metrics)[0]
        try:
            probe = build_series(df, date_col, representative, freq)
            periods = probe.n
            gaps = probe.filled_periods
        except Exception:
            periods = 0

    if not periods:
        # Preparation failed (or no numeric metric): fall back to distinct dates,
        # which is still far closer to a period count than the row count is.
        periods = int(dates.dt.normalize().nunique())

    forecastable = periods >= min_req and len(metrics) > 0

    msg_parts = [
        f"Date column: **{date_col}**",
        f"Frequency: **{freq}**",
        f"Historical records: **{rows}** rows → **{periods}** {freq} periods",
        f"Forecastable: **{'yes' if forecastable else 'no'}** (minimum {min_req} periods)",
    ]
    if gaps:
        msg_parts.append(
            f"Periods with no records: **{gaps}** (filled so the calendar stays aligned)"
        )
    if metrics:
        msg_parts.append(f"Metrics: **{', '.join(metrics[:8])}**")

    return {
        "date_column": date_col,
        "frequency": freq,
        # Kept as the period count: every downstream consumer treats this as
        # "how much history the model had", which rows never answered.
        "history_length": periods,
        "row_count": rows,
        "forecastable": forecastable,
        "validation": {
            "frequency": freq,
            "history_length": periods,
            "row_count": rows,
            "forecastable": forecastable,
            "missing_dates_count": gaps,
        },
        "validation_message": "\n\n".join(msg_parts),
        "missing_dates_count": gaps,
        "available_metrics": metrics,
    }


def prepare_node(state: ForecastState) -> dict:
    return {}


def forecast_node(state: ForecastState) -> dict:
    targets = state.get("forecast_targets", [])
    if not targets:
        return {}

    df = state["df"]
    date_col = state["date_column"]
    freq = state.get("frequency", "daily")
    horizon = state.get("horizon_days", 30)
    horizons = state.get("standard_horizons") or [7, 30, 90]
    horizons = sorted({h for h in horizons if h > 0} | {horizon})
    run_id = state.get("run_id", "")
    model = state.get("model", "Auto")
    granularity = state.get("granularity", "native")
    max_cost = state.get("max_cost", "expensive")

    all_outputs: list[dict] = []
    all_figures: list[go.Figure] = []
    all_tables: list[pd.DataFrame] = []
    all_evaluations: list[dict] = []
    all_history: list[dict] = []
    model_selections: list[dict] = []

    with ThreadPoolExecutor(max_workers=min(len(targets), 4)) as pool:
        futures = {
            pool.submit(
                _forecast_one_target, t, df, date_col, freq, horizon, horizons,
                run_id, model, granularity, max_cost,
            ): t
            for t in targets
        }
        for future in as_completed(futures):
            try:
                r = future.result()
                all_outputs.append(r["output"])
                all_figures.append(r["fig"])
                all_tables.append(r["forecast_df"])
                all_evaluations.append(r["metrics"])
                all_history.extend(r["history_rows"])
                model_selections.append({
                    "target": r["output"]["metric"],
                    "model": r["output"].get("selected_model", "Prophet"),
                    "reason": r["output"].get("model_selection_reason", ""),
                })
            except Exception as exc:
                t = futures[future]
                all_outputs.append({
                    "metric": t, "error": str(exc),
                    "current_value": 0, "forecasted_value": 0,
                    "change_percent": 0, "confidence_score": 0,
                })
                all_evaluations.append({"metric": t, "error": str(exc)})

    return {
        "forecast_outputs": all_outputs,
        "model_selections": model_selections,
        "execution_figures": all_figures,
        "execution_tables": all_tables,
        "forecast_evaluations": all_evaluations,
        "forecast_history_rows": all_history,
    }


__all__ = [
    "validate_node",
    "prepare_node",
    "forecast_node",
    "drift_node",
]
