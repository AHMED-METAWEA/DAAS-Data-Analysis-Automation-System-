from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RootCauseOptionsResponse(BaseModel):
    """What this project's data supports — drives the UI's controls so a user
    can never request a measure or dimension the dataset cannot answer."""

    available: bool
    reason: str = ""
    time_column: str | None = None
    measures: list[dict[str, Any]] = Field(default_factory=list)
    dimensions: list[dict[str, Any]] = Field(default_factory=list)
    dimensions_skipped: list[dict[str, str]] = Field(default_factory=list)
    windows: list[dict[str, str]] = Field(default_factory=list)


class RootCauseRequest(BaseModel):
    measure: str = "revenue"
    # auto | last_month | rolling_30d | custom
    window_mode: str = "auto"
    current_start: str | None = None
    current_end: str | None = None
    prior_start: str | None = None
    prior_end: str | None = None
    # None = every detected dimension.
    dimensions: list[str] | None = None
    include_weekday: bool = False
    max_depth: int = Field(default=3, ge=1, le=4)
    beam_width: int = Field(default=48, ge=2, le=256)
    top_k: int = Field(default=6, ge=1, le=20)
    min_explanatory_power: float = Field(default=0.08, ge=0.0, le=1.0)
    min_signal_to_noise: float = Field(default=2.0, ge=0.0, le=10.0)
    with_narrative: bool = True
    language: str = "en"
    business_context: str = ""
    model: str | None = None


class WindowOut(BaseModel):
    label: str
    start: str
    end: str
    value: float
    rows: int


class ExplanationOut(BaseModel):
    rank: int
    slice: dict[str, str]
    slice_label: str
    slice_expression: str
    depth: int
    prior_value: float
    current_value: float
    delta: float
    change_pct: float | None = None
    explanatory_power: float
    explanatory_power_pct: float
    expected_current: float
    excess: float
    excess_share: float
    excess_share_pct: float
    prior_rows: int
    current_rows: int
    rows_share: float
    rows_share_pct: float
    concentration: float
    surprise: float
    signal_to_noise: float | None = None
    p_value: float | None = None
    robust: bool | None = None
    bridge: dict[str, Any] | None = None
    rest_prior: float
    rest_current: float
    rest_delta: float
    rest_change_pct: float | None = None
    direction: str
    score: float


class RootCauseResponse(BaseModel):
    available: bool
    reason: str = ""
    measure: str
    measure_label: str
    measure_unit: str
    measure_basis: str
    measure_additive: bool
    current: WindowOut | None = None
    prior: WindowOut | None = None
    total_delta: float
    total_change_pct: float | None = None
    window_basis: str = ""
    explanations: list[ExplanationOut] = Field(default_factory=list)
    per_dimension: list[dict[str, Any]] = Field(default_factory=list)
    drill_path: list[dict[str, Any]] = Field(default_factory=list)
    dimensions: list[dict[str, Any]] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    narrative: str = ""
    figures: list[dict[str, Any]] = Field(default_factory=list)
    verification: dict[str, Any] = Field(default_factory=dict)
