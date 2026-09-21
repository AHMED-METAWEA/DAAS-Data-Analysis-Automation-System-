from __future__ import annotations

import json

from fastapi import APIRouter, Depends

from agents.forecasting.metric_discovery import rank_metrics
from agents.forecasting.pipeline import ForecastPipeline
from agents.forecasting.report import build_forecast_report_md
from agents.forecasting.validation import list_forecastable_metrics
from backend.app.api.deps import get_current_user, get_owned_project
from backend.app.schemas.forecasting import (
    ForecastMetricsResponse,
    ForecastOutputOut,
    ForecastRunRequest,
    ForecastRunResponse,
)
from backend.app.services.json_safe import json_safe
from backend.app.services.views import resolve_view
from db.auth_models import User
from db.platform_models import Project
from tools.sandbox import sanitize_fig

router = APIRouter(prefix="/projects/{project_id}/forecast", tags=["forecasting"])


@router.get("/metrics", response_model=ForecastMetricsResponse)
def get_forecastable_metrics(project: Project = Depends(get_owned_project)) -> ForecastMetricsResponse:
    df = resolve_view(project.id, "forecast")
    date_column, metrics = list_forecastable_metrics(df)
    return ForecastMetricsResponse(date_column=date_column, metrics=rank_metrics(metrics))


@router.post("/run", response_model=ForecastRunResponse)
def run_forecast(
    payload: ForecastRunRequest,
    project: Project = Depends(get_owned_project),
    current_user: User = Depends(get_current_user),
) -> ForecastRunResponse:
    df = resolve_view(project.id, "forecast")
    result = ForecastPipeline().run(
        df,
        targets=payload.targets,
        horizon_days=payload.horizon_days,
        standard_horizons=payload.standard_horizons,
        model_override=payload.model_override,
        granularity=payload.granularity,
        business_context=payload.business_context,
        llm_model=payload.llm_model or current_user.model_preferences.get("forecast") or "",
        project_id=str(project.id),
        # Persist every run: comparing a *previously issued* forecast against
        # what actually happened is the only way to detect that a model has
        # stopped working, and that comparison is impossible if runs are never
        # stored. Falls back to a no-op when Postgres is not configured.
        store=True,
    )

    figures: dict[str, dict] = {}
    for fig, out in zip(result.get("execution_figures", []), result.get("forecast_outputs", [])):
        figures[out.get("metric", "")] = json.loads(sanitize_fig(fig).to_json())

    outputs = [ForecastOutputOut(**json_safe(o)) for o in result.get("forecast_outputs", [])]

    return ForecastRunResponse(
        run_id=result.get("run_id", ""),
        forecastable=bool(result.get("forecastable")),
        validation_message=result.get("validation_message", ""),
        date_column=result.get("date_column", ""),
        frequency=result.get("frequency", ""),
        history_length=result.get("history_length", 0),
        outputs=outputs,
        figures=figures,
        report_md=build_forecast_report_md(result),
    )
