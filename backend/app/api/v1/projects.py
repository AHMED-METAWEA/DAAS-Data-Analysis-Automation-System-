from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from backend.app.api.deps import get_current_user, get_owned_project
from backend.app.schemas.projects import ProjectCreate, ProjectOut
from db import projects as projects_repo
from db.auth_models import User
from db.platform_models import Project

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=list[ProjectOut])
def list_projects(current_user: User = Depends(get_current_user)) -> list[Project]:
    return projects_repo.list_projects(owner_id=current_user.id)


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreate, current_user: User = Depends(get_current_user)
) -> Project:
    return projects_repo.create_project(
        name=payload.name,
        data_source_mode=payload.data_source_mode,
        owner_id=current_user.id,
    )


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project: Project = Depends(get_owned_project)) -> Project:
    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project: Project = Depends(get_owned_project)) -> None:
    if not projects_repo.delete_project(project.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
