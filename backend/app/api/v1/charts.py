from __future__ import annotations

from fastapi import APIRouter, Depends

from agents.visualization.explain import explain_chart
from backend.app.api.deps import get_current_user, get_owned_project
from backend.app.schemas.charts import ExplainChartRequest, ExplainChartResponse
from db.auth_models import User
from db.platform_models import Project

router = APIRouter(prefix="/projects/{project_id}/charts", tags=["charts"])


@router.post("/explain", response_model=ExplainChartResponse)
def explain_chart_endpoint(
    payload: ExplainChartRequest,
    project: Project = Depends(get_owned_project),
    current_user: User = Depends(get_current_user),
) -> ExplainChartResponse:
    try:
        explanation = explain_chart(
            payload.figure,
            title=payload.title,
            context=payload.context,
            model=payload.model or current_user.model_preferences.get("explain_chart"),
        )
    except Exception as exc:
        explanation = f"I couldn't generate an explanation for this chart ({exc})."
    return ExplainChartResponse(explanation=explanation)
