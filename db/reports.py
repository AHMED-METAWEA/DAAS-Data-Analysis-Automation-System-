"""CRUD helpers for the Report platform table (the Reports library)."""

from __future__ import annotations

from sqlalchemy import select

from db.platform_models import Project
from db.report_models import Report
from db.session import get_session


def save_report(
    project_id: str, type: str, title: str, markdown: str, grounding: dict | None = None
) -> Report:
    with get_session() as session:
        report = Report(
            project_id=project_id, type=type, title=title, markdown=markdown,
            grounding=grounding or {},
        )
        session.add(report)
        session.commit()
        session.refresh(report)
        return report


def list_reports_for_project(project_id: str) -> list[Report]:
    with get_session() as session:
        stmt = (
            select(Report)
            .where(Report.project_id == project_id)
            .order_by(Report.created_at.desc())
        )
        return list(session.scalars(stmt))


def list_reports_for_owner(owner_id: str) -> list[tuple[Report, Project]]:
    with get_session() as session:
        stmt = (
            select(Report, Project)
            .join(Project, Report.project_id == Project.id)
            .where(Project.owner_id == owner_id)
            .order_by(Report.created_at.desc())
        )
        return [(r, p) for r, p in session.execute(stmt)]


def get_report(report_id: str) -> Report | None:
    with get_session() as session:
        return session.get(Report, report_id)
