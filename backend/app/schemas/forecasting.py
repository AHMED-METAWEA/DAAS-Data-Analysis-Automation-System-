from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ForecastMetricsResponse(BaseModel):
    date_column: str | None
    metrics: list[str]


class ForecastRunRequest(BaseModel):
    targets: list[str] | None = None
    horizon_days: int = 30
    standard_horizons: list[int] | None = None
    model_override: str = "Auto"
    granularity: str = "native"
    business_context: str = ""
    # LLM model id for the narrative-interpretation step only — distinct from
    # model_override, which selects the forecasting *engine* (Prophet/ETS/...).
    llm_model: str | None = None


class ForecastOutputOut(BaseModel):
    """Mirrors agents.forecasting.schema.ForecastOutput, but every field the
    error path (a per-target exception in forecast_node) doesn't populate
    gets a default here — a failed target still has to round-trip through
    this model instead of 500ing the whole run."""

    metric: str
    current_value: float = 0.0
    forecasted_value: float = 0.0
    change_percent: float = 0.0
    confidence_score: float | None = None
    forecast_horizon: str = ""
    granularity: str = "daily"
    training_periods: int = 0
    selected_model: str = ""
    model_selection_reason: str = ""
    evaluation: dict[str, Any] = Field(default_factory=dict)
    business_impact: str = "medium"
    business_summary: str = ""
    trend: str = "stable"
    risks: list[str] = Field(default_factory=list)
    opportunities: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    horizons: dict[str, float] = Field(default_factory=dict)
    cv_results: list[dict[str, Any]] = Field(default_factory=list)
    skill_score: float | None = None
    error: str | None = None

    # ── Headline aggregate ──────────────────────────────────────────────
    # "sum" for additive metrics, "mean" for rates — decides whether the
    # horizon figure reads as a total or an average.
    aggregation: str = "sum"
    horizon_total: float | None = None
    horizon_total_lower: float | None = None
    horizon_total_upper: float | None = None
    baseline_window_value: float | None = None
    transform: str = "none"

    # ── Measured uncertainty ────────────────────────────────────────────
    interval_level: float = 0.8
    interval_method: str = ""
    measured_coverage: float | None = None

    # ── What the data supports ──────────────────────────────────────────
    predictability: float | None = None
    trend_strength: float | None = None
    seasonal_strength: float | None = None
    signal_verdict: str = "unknown"

    # ── Data quality / change detection ─────────────────────────────────
    anomalies_detected: int = 0
    anomaly_periods: list[dict[str, Any]] = Field(default_factory=list)
    level_shift: dict[str, Any] | None = None
    data_quality: dict[str, Any] = Field(default_factory=dict)
    series_fingerprint: str = ""
    notes: list[str] = Field(default_factory=list)

    # ── Is it fit to act on? ────────────────────────────────────────────
    reliability: str = "unknown"
    reliability_headline: str = ""
    reliability_reasons: list[str] = Field(default_factory=list)
    recommended_granularity: str = ""
    granularity_reason: str = ""

    # How the metric has held up against forecasts issued on earlier runs.
    drift: dict[str, Any] | None = None


class ForecastRunResponse(BaseModel):
    run_id: str
    forecastable: bool
    validation_message: str
    date_column: str
    frequency: str
    history_length: int
    outputs: list[ForecastOutputOut]
    figures: dict[str, dict[str, Any]]
    report_md: str
