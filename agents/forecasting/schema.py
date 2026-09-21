"""
Structured forecast output schema for downstream Strategy / Marketing agents.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ForecastEvaluation(BaseModel):
    mae: float | None = None
    rmse: float | None = None
    mape: float | None = None
    # Mean Absolute Scaled Error — scale-free, immune to MAPE's blow-up when
    # actuals are near zero/negative. < 1.0 means more accurate than a naive
    # one-step forecast on this series; this is what actually drives model
    # selection (see agents/forecasting/tools/backtest.py's leaderboard sort).
    mase: float | None = None


class ForecastOutput(BaseModel):
    metric: str
    current_value: float
    forecasted_value: float
    change_percent: float
    confidence_score: float | None = None
    forecast_horizon: str
    granularity: str = "daily"
    # The number of *aggregated* periods at this granularity the model actually
    # trained on — distinct from the run's raw transaction-row count
    # (``history_length``), which can badly overstate how much history a
    # daily/weekly/monthly forecast really saw.
    training_periods: int = 0
    selected_model: str
    model_selection_reason: str = ""
    evaluation: ForecastEvaluation = Field(default_factory=ForecastEvaluation)
    key_drivers: list[str] = Field(default_factory=list)
    business_impact: str = "medium"
    business_summary: str = ""
    trend: str = "stable"
    risks: list[str] = Field(default_factory=list)
    opportunities: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    horizons: dict[str, float] = Field(default_factory=dict)
    # Back-test leaderboard ({model, mae, rmse, mape, ...}) and relative skill
    # vs. the best naive baseline (0.42 = 42% lower error). From the engine.
    cv_results: list[dict[str, Any]] = Field(default_factory=list)
    skill_score: float | None = None

    # ── How the headline numbers were formed ────────────────────────────────
    # "sum" for additive metrics, "mean" for rates. Decides whether a horizon
    # figure is a total or an average, and what `current_value` is compared to.
    aggregation: str = "sum"
    # The horizon aggregate — "revenue over the next 30 days" — with its own
    # interval. For a noisy series this is far more predictable than any single
    # period inside it, and it is the number a business actually plans against.
    horizon_total: float | None = None
    horizon_total_lower: float | None = None
    horizon_total_upper: float | None = None
    # The equivalent trailing window `current_value` was computed over, so the
    # comparison behind `change_percent` is like-for-like.
    baseline_window_value: float | None = None
    # Variance-stabilising transform the model was fitted on ("none"/"log1p"/"sqrt").
    transform: str = "none"

    # ── Uncertainty, measured rather than assumed ───────────────────────────
    interval_level: float = 0.8
    interval_method: str = ""
    # Leave-one-out coverage of the band on the back-test. When this is far from
    # `interval_level`, the interval is not trustworthy and the UI should say so.
    measured_coverage: float | None = None

    # ── What the data itself supports ───────────────────────────────────────
    # Share of variation that is structure rather than period-to-period noise.
    predictability: float | None = None
    trend_strength: float | None = None
    seasonal_strength: float | None = None
    signal_verdict: str = "unknown"

    # ── Data quality and change detection ───────────────────────────────────
    anomalies_detected: int = 0
    anomaly_periods: list[dict[str, Any]] = Field(default_factory=list)
    level_shift: dict[str, Any] | None = None
    data_quality: dict[str, Any] = Field(default_factory=dict)
    # Identifies the exact series this forecast was produced from, so a later
    # run can tell "same data" from "the data changed" without re-reading it.
    series_fingerprint: str = ""
    # Plain-language caveats worth surfacing next to the number.
    notes: list[str] = Field(default_factory=list)

    # ── Is this fit to act on? ──────────────────────────────────────────────
    # "reliable" | "indicative" | "unreliable". Derived from out-of-sample skill,
    # how much of the series is signal, back-test depth and interval coverage —
    # not from the size of the error alone. A forecast can have a small error
    # and still be worthless if a naive baseline matches it.
    reliability: str = "unknown"
    reliability_headline: str = ""
    reliability_reasons: list[str] = Field(default_factory=list)
    # Set when a coarser granularity would be materially more forecastable.
    recommended_granularity: str = ""
    granularity_reason: str = ""

    def to_agent_dict(self) -> dict[str, Any]:
        return self.model_dump()
