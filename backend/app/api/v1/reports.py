from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.api.deps import get_current_user, get_db, get_owned_project
from backend.app.schemas.reports import CreateReportRequest, ReportOut, ReportSummaryOut
from db.auth_models import User
from db.platform_models import Project
from db.reports import get_report, list_reports_for_owner, list_reports_for_project, save_report

router = APIRouter(tags=["reports"])


@router.get("/reports", response_model=list[ReportSummaryOut])
def list_my_reports(current_user: User = Depends(get_current_user)) -> list[ReportSummaryOut]:
    rows = list_reports_for_owner(current_user.id)
    return [
        ReportSummaryOut(
            id=r.id, project_id=r.project_id, project_name=p.name,
            type=r.type, title=r.title, created_at=r.created_at,
        )
        for r, p in rows
    ]


@router.get("/reports/{report_id}", response_model=ReportOut)
def get_one_report(
    report_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> ReportOut:
    report = get_report(report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Report not found")
    project = db.get(Project, report.project_id)
    if project is None or (project.owner_id is not None and project.owner_id != current_user.id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not your report")
    return ReportOut(
        id=report.id, project_id=report.project_id, project_name=project.name,
        type=report.type, title=report.title, created_at=report.created_at,
        markdown=report.markdown, grounding=report.grounding,
    )


@router.get("/projects/{project_id}/reports", response_model=list[ReportSummaryOut])
def list_project_reports(project: Project = Depends(get_owned_project)) -> list[ReportSummaryOut]:
    rows = list_reports_for_project(project.id)
    return [
        ReportSummaryOut(
            id=r.id, project_id=r.project_id, project_name=project.name,
            type=r.type, title=r.title, created_at=r.created_at,
        )
        for r in rows
    ]


@router.post("/projects/{project_id}/reports", response_model=ReportOut)
def create_report(
    payload: CreateReportRequest, project: Project = Depends(get_owned_project)
) -> ReportOut:
    """Persist an already-generated report (from Insights/Forecasting/Marketing/
    Churn) without re-running the pipeline that produced it."""
    saved = save_report(
        project.id, type=payload.type, title=payload.title,
        markdown=payload.markdown, grounding=payload.grounding,
    )
    return ReportOut(
        id=saved.id, project_id=saved.project_id, project_name=project.name,
        type=saved.type, title=saved.title, created_at=saved.created_at,
        markdown=saved.markdown, grounding=saved.grounding,
    )
