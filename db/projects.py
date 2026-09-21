"""CRUD helpers for the Project platform table (backs the sidebar switcher)."""

from __future__ import annotations

import re

from sqlalchemy import select

from db.platform_models import Project
from db.session import get_session


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return slug or "project"


def list_projects(owner_id: str | None = None) -> list[Project]:
    with get_session() as session:
        stmt = select(Project).order_by(Project.created_at.desc())
        if owner_id is not None:
            stmt = stmt.where(Project.owner_id == owner_id)
        return list(session.scalars(stmt))


def create_project(
    name: str, data_source_mode: str = "files", owner_id: str | None = None
) -> Project:
    with get_session() as session:
        base_slug = _slugify(name)
        slug = base_slug
        suffix = 1
        while session.scalar(select(Project).where(Project.slug == slug)) is not None:
            suffix += 1
            slug = f"{base_slug}_{suffix}"
        project = Project(
            name=name, slug=slug, data_source_mode=data_source_mode, owner_id=owner_id
        )
        session.add(project)
        session.commit()
        session.refresh(project)
        return project


def get_project(project_id: str) -> Project | None:
    with get_session() as session:
        return session.get(Project, project_id)


def delete_project(project_id: str) -> bool:
    with get_session() as session:
        project = session.get(Project, project_id)
        if project is None:
            return False
        session.delete(project)
        session.commit()
        return True
