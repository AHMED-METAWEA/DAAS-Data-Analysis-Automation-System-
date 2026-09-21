from __future__ import annotations

from fastapi import APIRouter, Depends

from agents.churn.agent import generate_retention_plan
from agents.churn.engine import run_churn_analysis
from agents.constants import DEFAULT_CHURN_MODEL
from agents.crm import service as crm_service
from backend.app.api.deps import get_current_user, get_owned_project
from backend.app.schemas.churn import (
    ChurnRunRequest,
    ChurnRunResponse,
    RetentionPlanRequest,
    RetentionPlanResponse,
)
from backend.app.services.json_safe import json_safe
from backend.app.services.views import resolve_view
from db.auth_models import User
from db.platform_models import Project

router = APIRouter(prefix="/projects/{project_id}/churn", tags=["churn"])


@router.post("/run", response_model=ChurnRunResponse)
def run_churn(
    payload: ChurnRunRequest, project: Project = Depends(get_owned_project)
) -> ChurnRunResponse:
    df = resolve_view(project.id, "customer_360")
    result = run_churn_analysis(
        data_df=df, horizon_days=payload.horizon_days, top_n=payload.top_n
    )
    if not result.get("available"):
        return ChurnRunResponse(available=False, reason=result.get("reason"))

    # Persist customer state as a side effect, reusing the result already in
    # hand rather than refitting. Running the analysis is what builds the
    # customer history — nobody should have to remember a separate "save"
    # step for the timeline to exist. `record_churn_run` never raises: a
    # storage problem must not turn a successful analysis into a failure.
    crm_service.record_churn_run(project.id, result, data_df=df)

    safe = json_safe(result)
    safe["customer_scores"] = safe.pop("_customer_scores", None)
    return ChurnRunResponse(**safe)


@router.post("/retention-plan", response_model=RetentionPlanResponse)
def retention_plan(
    payload: RetentionPlanRequest,
    project: Project = Depends(get_owned_project),
    current_user: User = Depends(get_current_user),
) -> RetentionPlanResponse:
    churn_result = dict(payload.churn_result)
    if "customer_scores" in churn_result:
        churn_result["_customer_scores"] = churn_result.pop("customer_scores")

    df = resolve_view(project.id, "customer_360")
    report_md = generate_retention_plan(
        churn_result,
        data_df=df,
        business_context=payload.business_context,
        model=payload.model or current_user.model_preferences.get("churn") or DEFAULT_CHURN_MODEL,
    )
    return RetentionPlanResponse(report_md=report_md)
