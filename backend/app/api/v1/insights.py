from __future__ import annotations

from fastapi import APIRouter, Depends

from agents.constants import DEFAULT_INSIGHTS_MODEL
from agents.insights.template_selector import describe_template
from backend.app.api.deps import get_current_user, get_owned_project
from backend.app.schemas.insights import (
    FigureOut,
    GroundingOut,
    InsightsChatRequest,
    InsightsChatResponse,
    InsightsRequest,
    InsightsResponse,
    VerificationOut,
)
from backend.app.services.analysis_chat import run_grounded_chat
from backend.app.services.dashboard_service import build_dashboard_response
from backend.app.services.insights_service import generate_grounded_insights
from backend.app.services.json_safe import json_safe
from backend.app.services.views import resolve_view
from db.auth_models import User
from db.platform_models import Project

router = APIRouter(prefix="/projects/{project_id}/insights", tags=["insights"])


@router.post("", response_model=InsightsResponse)
def generate_insights_report(
    payload: InsightsRequest,
    project: Project = Depends(get_owned_project),
    current_user: User = Depends(get_current_user),
) -> InsightsResponse:
    df = resolve_view(project.id, "analytics")
    model = payload.model or current_user.model_preferences.get("insights") or DEFAULT_INSIGHTS_MODEL
    result = generate_grounded_insights(
        df, "",
        business_context=payload.business_context,
        model=model,
        template_override=payload.template_override,
    )
    # The badge reflects the strict citation check, not the cross-agent
    # heuristic — see GroundedInsights.user_facing_grounding for why.
    grounding = result.user_facing_grounding()
    audit = result.audit
    cert = audit["certificate"]
    return InsightsResponse(
        report_md=result.report_md,
        template=describe_template(result.template_stem),
        grounding=GroundingOut(
            status=grounding.status, label=result.strict.label,
            total=grounding.total, verified_count=grounding.verified_count,
            coverage=grounding.coverage,
            unverified=[c.raw for c in grounding.unverified],
            traces=grounding.traces,
        ),
        analytics=json_safe(result.analytics_payload),
        dashboard=build_dashboard_response(df),
        verification=VerificationOut(
            status=cert["status"],
            label=result.strict.label,
            figures_cited_from_engine=cert["figures_cited_from_engine"],
            figures_typed_by_model=cert["figures_typed_by_model"],
            typed_and_verified=cert["typed_and_verified"],
            citation_rate=cert["citation_rate"],
            unverified_figures=cert["unverified_figures"],
            unknown_citations=cert["unknown_citations"],
            unsupported_currency_claims=cert["unsupported_currency_claims"],
            misvalued_actions=cert["misvalued_actions"],
            ambiguous_citations=cert["ambiguous_citations"],
            corrected=audit["corrected"],
        ),
        figures=[FigureOut(**row) for row in audit["figures"] if row["unit"] != "text"],
        evidence=audit["evidence"],
        blind_spots=audit["blind_spots"],
        decision=json_safe(result.decision),
    )


@router.post("/chat", response_model=InsightsChatResponse)
def insights_chat(
    payload: InsightsChatRequest,
    project: Project = Depends(get_owned_project),
    current_user: User = Depends(get_current_user),
) -> InsightsChatResponse:
    df = resolve_view(project.id, "analytics")
    system_context = (
        "You are a senior BI analyst. The user just received a business report "
        "and is asking follow-up questions about it.\n\n"
        f"Report style: {payload.template_label}\n\n"
        f"## Full Report\n\n{payload.report_md}\n\n"
        "INSTRUCTIONS:\n"
        "1. Answer based ONLY on the report above and the analytics data.\n"
        "2. If the question is not covered by the report, say so honestly.\n"
        "3. If you can compute the answer, use Python code with `df` to do it.\n"
        "4. Always explain your reasoning in plain language.\n"
        "5. If you use a technical term, explain it briefly."
    )
    result = run_grounded_chat(
        system_context=system_context,
        history=[m.model_dump() for m in payload.history],
        message=payload.message,
        df=df,
        model=payload.model or current_user.model_preferences.get("insights") or DEFAULT_INSIGHTS_MODEL,
        purpose="insights",
    )
    return InsightsChatResponse(**result)
