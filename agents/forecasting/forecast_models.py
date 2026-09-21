from __future__ import annotations

from typing import Any, TypedDict


class ForecastState(TypedDict, total=False):
    df: Any
    run_id: str
    horizon_days: int
    standard_horizons: list[int]
    model: str            # "Auto" (back-test selector) or a forced model name
    llm_model: str        # LLM model id used ONLY for the narrative interpretation step
    granularity: str      # "native" | "daily" | "weekly" | "monthly"
    max_cost: str         # "cheap" | "medium" | "expensive" — back-test budget
    table_name: str
    business_context: str
    project_id: str

    date_column: str
    frequency: str
    history_length: int
    row_count: int
    forecastable: bool
    validation: dict[str, Any]
    validation_message: str
    missing_dates_count: int

    available_metrics: list[str]
    forecast_targets: list[str]

    current_target_index: int
    forecast_outputs: list[dict[str, Any]]
    model_selections: list[dict[str, str]]
    execution_figures: list[Any]
    execution_tables: list[Any]
    forecast_evaluations: list[dict[str, Any]]
    forecast_history_rows: list[dict[str, Any]]

    interpretations: list[dict[str, Any]]
    drift_detected: bool

    errors: list[str]
    error: str | None
    completed_at: str | None
    storage_result: dict[str, Any] | None
