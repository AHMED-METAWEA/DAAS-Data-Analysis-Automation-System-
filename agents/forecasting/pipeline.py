from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from agents.forecasting.forecast_models import ForecastState
from agents.forecasting.graph import forecast_graph
from agents.forecasting.metric_discovery import discover_metrics
from agents.forecasting.storage import save_forecast_run

logger = logging.getLogger(__name__)


def _default_output(df: pd.DataFrame, error: str, run_id: str) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "completed_at": datetime.now(UTC).isoformat(),
        "forecast_outputs": [],
        "execution_figures": [],
        "execution_tables": [],
        "forecast_evaluations": [],
        "forecast_history_rows": [],
        "model_selections": [],
        "validation": {"forecastable": False, "errors": error},
        "validation_message": error,
        "forecastable": False,
        "date_column": "",
        "frequency": "",
        "history_length": 0,
        "missing_dates_count": 0,
        "available_metrics": [],
        "storage_result": None,
        "error": error,
        "errors": [error],
    }


DEFAULT_HORIZONS = [7, 30, 90]


class ForecastPipeline:
    def __init__(self) -> None:
        pass

    def run(
        self,
        df: pd.DataFrame,
        targets: list[str] | None = None,
        horizon_days: int = 30,
        standard_horizons: list[int] | None = None,
        run_id: str | None = None,
        store: bool = True,
        model_override: str = "Auto",
        granularity: str = "native",
        # Back-test budget. "expensive" evaluates every model the series can
        # support; "medium"/"cheap" drop the slow families (SARIMA, STL-ARIMA,
        # Prophet) when latency matters more than the last few percent.
        max_cost: str = "expensive",
        **kwargs: Any,
    ) -> dict[str, Any]:
        if run_id is None:
            run_id = str(uuid.uuid4())

        if df is None or df.empty:
            return _default_output(df, "No data provided.", run_id)

        # Accept `metrics` as an alias for `targets` (UI compatibility)
        if (targets is None or len(targets) == 0) and "metrics" in kwargs:
            targets = kwargs["metrics"]

        table_name = kwargs.get("table_name", "")
        business_context = kwargs.get("business_context", "")
        llm_model = kwargs.get("llm_model", "")
        project_id = kwargs.get("project_id", "") or ""

        if targets is None or len(targets) == 0:
            metrics = discover_metrics(df)
            targets = [m["metric"] for m in metrics[:10]]
            if not targets:
                return _default_output(df, "No forecastable numeric metrics detected.", run_id)

        if standard_horizons is None:
            standard_horizons = [7, 30, 90]

        state: ForecastState = {
            "run_id": run_id,
            "df": df,
            "forecast_targets": targets,
            "horizon_days": horizon_days,
            "standard_horizons": standard_horizons,
            "model": model_override or "Auto",
            "llm_model": llm_model,
            "granularity": granularity or "native",
            "max_cost": max_cost or "expensive",
            "table_name": table_name,
            "business_context": business_context,
            "project_id": project_id,
            "current_target_index": 0,
            "date_column": "",
            "frequency": "",
            "history_length": 0,
            "forecastable": True,
            "validation": {},
            "validation_message": "",
            "missing_dates_count": 0,
            "available_metrics": targets,
            "forecast_outputs": [],
            "execution_figures": [],
            "execution_tables": [],
            "forecast_evaluations": [],
            "forecast_history_rows": [],
            "model_selections": [],
            "errors": [],
            "error": None,
            "completed_at": None,
            "storage_result": None,
        }

        try:
            result = forecast_graph.invoke(state)
            result["completed_at"] = datetime.now(UTC).isoformat()
            result["forecastable"] = result.get("validation", {}).get("forecastable", False)

            if store and result.get("forecastable"):
                try:
                    sr = save_forecast_run(result, project_id=project_id or None)
                    result["storage_result"] = sr
                except Exception as e:
                    logger.warning(f"Storage save failed: {e}")
                    result["storage_result"] = None

            return result
        except Exception as e:
            logger.exception("Forecast pipeline failed")
            return _default_output(df, f"Pipeline execution error: {e}", run_id)
