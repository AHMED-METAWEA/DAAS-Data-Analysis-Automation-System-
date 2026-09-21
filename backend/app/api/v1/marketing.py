from __future__ import annotations

import json

from fastapi import APIRouter, Depends

from agents.constants import DEFAULT_MARKETING_MODEL
from agents.marketing.agent import (
    generate_ad_copy,
    generate_campaign_plan,
    generate_marketing_strategy,
    slim_for_prompt,
)
from agents.marketing.engine import run_marketing_analytics
from agents.reporting.verify import verify_report
from backend.app.api.deps import get_current_user, get_owned_project
from backend.app.schemas.insights import GroundingOut
from backend.app.schemas.marketing import (
    AdCopyRequest,
    AdCopyResponse,
    CampaignPlanRequest,
    CampaignPlanResponse,
    MarketingChatRequest,
    MarketingChatResponse,
    MarketingRunRequest,
    MarketingRunResponse,
)
from backend.app.services.json_safe import json_safe
from backend.app.services.views import resolve_view
from db.auth_models import User
from db.platform_models import Project
from tools.llm_client import complete
from tools.sandbox import df_context

router = APIRouter(prefix="/projects/{project_id}/marketing", tags=["marketing"])


def _resolved_model(explicit: str | None, current_user: User) -> str:
    return explicit or current_user.model_preferences.get("marketing") or DEFAULT_MARKETING_MODEL


def _forecast_results(forecast_outputs: list[dict] | None) -> dict | None:
    return {"forecast_outputs": forecast_outputs} if forecast_outputs else None


def _churn_payload(churn_result: dict | None) -> dict | None:
    if not churn_result:
        return None
    cr = dict(churn_result)
    if "customer_scores" in cr:
        cr["_customer_scores"] = cr.pop("customer_scores")
    return cr


@router.post("/run", response_model=MarketingRunResponse)
def run_marketing(
    payload: MarketingRunRequest,
    project: Project = Depends(get_owned_project),
    current_user: User = Depends(get_current_user),
) -> MarketingRunResponse:
    df = resolve_view(project.id, "marketing")
    result = run_marketing_analytics(
        data_df=df, churn_payload=_churn_payload(payload.churn_result)
    )
    report_md = generate_marketing_strategy(
        df, "", result,
        business_context=payload.business_context,
        objective=payload.objective,
        budget=payload.budget,
        channels=payload.channels,
        brand_voice=payload.brand_voice,
        forecast_results=_forecast_results(payload.forecast_outputs),
        model=_resolved_model(payload.model, current_user),
    )
    # Same unified verifier as insights/forecast: checks the report's FACTUAL
    # figures (segment sizes, revenue, churn %, per-segment/product numbers)
    # against what was computed — by magnitude AND by the metric/entity they're
    # attributed to. Forward-looking recommendations (budget %, target lift)
    # have no ground truth and are transparently surfaced as unverified, never
    # branded correct.
    grounding = verify_report(report_md, result, _forecast_results(payload.forecast_outputs))
    safe = json_safe(result)
    return MarketingRunResponse(
        schema_summary=safe.get("schema", {}),
        kpi=safe.get("kpi", {}),
        rfm=safe.get("rfm", {}),
        marketing_kpis=safe.get("marketing_kpis", {}),
        channels=safe.get("channels", {}),
        churn=safe.get("churn", {}),
        metadata=safe.get("metadata", {}),
        report_md=report_md,
        grounding=GroundingOut(
            status=grounding.status, label=grounding.label,
            total=grounding.total, verified_count=grounding.verified_count,
            coverage=grounding.coverage,
            unverified=[c.raw for c in grounding.unverified],
            traces=grounding.traces,
        ),
    )


@router.post("/campaigns", response_model=CampaignPlanResponse)
def campaign_plan(
    payload: CampaignPlanRequest,
    project: Project = Depends(get_owned_project),
    current_user: User = Depends(get_current_user),
) -> CampaignPlanResponse:
    plan = generate_campaign_plan(
        payload.marketing_payload,
        business_context=payload.business_context,
        objective=payload.objective,
        budget=payload.budget,
        channels=payload.channels,
        brand_voice=payload.brand_voice,
        forecast_results=_forecast_results(payload.forecast_outputs),
        model=_resolved_model(payload.model, current_user),
    )
    return CampaignPlanResponse(**plan)


@router.post("/ad-copy", response_model=AdCopyResponse)
def ad_copy(
    payload: AdCopyRequest,
    project: Project = Depends(get_owned_project),
    current_user: User = Depends(get_current_user),
) -> AdCopyResponse:
    copy_out = generate_ad_copy(
        segment=payload.segment,
        channel=payload.channel,
        platform=payload.platform,
        offer=payload.offer,
        brand_voice=payload.brand_voice,
        segment_stats=payload.segment_stats,
        playbook=payload.playbook,
        model=_resolved_model(payload.model, current_user),
    )
    return AdCopyResponse(**copy_out)


@router.post("/chat", response_model=MarketingChatResponse)
def marketing_chat(
    payload: MarketingChatRequest,
    project: Project = Depends(get_owned_project),
    current_user: User = Depends(get_current_user),
) -> MarketingChatResponse:
    df = resolve_view(project.id, "marketing")
    schema = df_context(df) if df is not None else ""
    sys_prompt = (
        "You are a senior marketing strategist. The user received a marketing "
        "strategy grounded in RFM segmentation and KPIs.\n\n"
        f"## Strategy report\n\n{payload.report_md}\n\n"
        "## Marketing analytics (JSON)\n"
        f"```json\n{json.dumps(slim_for_prompt(payload.marketing_payload), indent=2, default=str)}\n```\n\n"
        "Answer follow-ups using ONLY the report and analytics above. Be concrete "
        "and reference the relevant numbers. If asked for copy, write it ready-to-ship."
    )
    user_msg = payload.message + (f"\n\nData context:\n{schema}" if schema else "")
    try:
        answer = complete(
            "marketing",
            [
                {"role": "system", "content": sys_prompt},
                *[{"role": m.role, "content": m.content} for m in payload.history[-6:]],
                {"role": "user", "content": user_msg},
            ],
            model=_resolved_model(payload.model, current_user),
            temperature=0.5,
            max_tokens=2048,
        )
    except Exception as exc:
        answer = f"Error: {exc}"
    return MarketingChatResponse(answer=answer)
