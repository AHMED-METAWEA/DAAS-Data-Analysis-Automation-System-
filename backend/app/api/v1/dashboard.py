from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from agents.analytics.kpi_engine import compute_kpis
from agents.constants import DEFAULT_DASHBOARD_MODEL, DEFAULT_VIZ_MODEL
from agents.visualization.builder import build_executive_dashboard
from agents.visualization.dashboard import generate_dashboard
from agents.visualization.export import build_dashboard_html
from agents.visualization.theme import human_int, human_money
from agents.visualization.viz_graph import build_viz_graph
from backend.app.api.deps import get_current_user, get_owned_project
from backend.app.schemas.visualization import (
    AutoChartsRequest,
    AutoChartsResponse,
    ChartAskRequest,
    ChartAskResponse,
    DashboardResponse,
    KpiCardOut,
    SnapshotResponse,
)
from backend.app.services.dashboard_service import build_dashboard_response, chart_out
from backend.app.services.views import resolve_view
from db.auth_models import User
from db.platform_models import Project
from tools.sandbox import sanitize_fig

router = APIRouter(prefix="/projects/{project_id}/dashboard", tags=["visualization"])


@router.get("", response_model=DashboardResponse)
def get_dashboard(project: Project = Depends(get_owned_project)) -> DashboardResponse:
    df = resolve_view(project.id, "analytics")
    return build_dashboard_response(df)


@router.get("/snapshot", response_model=SnapshotResponse)
def get_snapshot(project: Project = Depends(get_owned_project)) -> SnapshotResponse:
    """Lightweight KPI pull for the Command Center — a handful of headline
    numbers, not the full chart-building dashboard pipeline."""
    df = resolve_view(project.id, "analytics")
    kpi = compute_kpis(df)

    cards = [
        KpiCardOut(label="Revenue", value=human_money(kpi.get("revenue", 0.0))),
        KpiCardOut(label="Orders", value=human_int(kpi.get("orders", 0))),
        KpiCardOut(label="Avg Order Value", value=human_money(kpi.get("aov", 0.0))),
    ]
    if kpi.get("total_customers") is not None:
        cards.append(KpiCardOut(label="Customers", value=human_int(kpi["total_customers"])))
    growth = kpi.get("monthly_growth_avg")
    if growth is not None:
        cards.append(KpiCardOut(
            label="Avg MoM Growth", value=f"{growth * 100:+.1f}%",
            direction="up" if growth > 0 else "down" if growth < 0 else "flat",
        ))

    return SnapshotResponse(
        kpis=cards,
        row_count=len(df),
        column_count=len(df.columns),
    )


@router.get("/export-html", response_class=HTMLResponse)
def export_dashboard_html(project: Project = Depends(get_owned_project)) -> str:
    df = resolve_view(project.id, "analytics")
    result = build_executive_dashboard(df)
    for c in result["charts"]:
        c["fig"] = sanitize_fig(c["fig"])
    return build_dashboard_html(
        title=f"{project.name} — Executive Dashboard",
        kpis=result["kpis"],
        charts=result["charts"],
        meta={"Rows analyzed": str(result["row_count"]), "Revenue basis": result["revenue_label"]},
    )


@router.post("/charts/ask", response_model=ChartAskResponse)
def ask_for_chart(
    payload: ChartAskRequest,
    project: Project = Depends(get_owned_project),
    current_user: User = Depends(get_current_user),
) -> ChartAskResponse:
    df = resolve_view(project.id, "analytics")
    graph = build_viz_graph()
    result = graph.invoke({
        "user_query": payload.query,
        "schema_info": "",
        "table_name": "",
        "model": payload.model or current_user.model_preferences.get("viz") or DEFAULT_VIZ_MODEL,
        "existing_chart_count": 0,
        "generated_code": "",
        "execution_result": {},
        "retry_count": 0,
        "last_error": "",
        "df": df,
    })
    exec_result = result.get("execution_result", {}) or {}
    figures = exec_result.get("figures") or []
    return ChartAskResponse(
        figures=[json.loads(f.to_json()) for f in figures],
        output=exec_result.get("output", ""),
        error=exec_result.get("error") or None,
    )


@router.post("/charts/auto", response_model=AutoChartsResponse)
def auto_generate_charts(
    payload: AutoChartsRequest,
    project: Project = Depends(get_owned_project),
    current_user: User = Depends(get_current_user),
) -> AutoChartsResponse:
    df = resolve_view(project.id, "analytics")
    charts = generate_dashboard(
        df, table_name="", focus_context=payload.focus_context,
        model=payload.model or current_user.model_preferences.get("dashboard") or DEFAULT_DASHBOARD_MODEL,
    )
    return AutoChartsResponse(charts=[chart_out(c) for c in charts])
