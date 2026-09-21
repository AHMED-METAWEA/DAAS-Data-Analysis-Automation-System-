from __future__ import annotations

from fastapi import APIRouter, Depends

from agents.constants import DEFAULT_INSIGHTS_MODEL
from backend.app.api.deps import get_current_user, get_owned_project
from backend.app.schemas.rootcause import (
    RootCauseOptionsResponse,
    RootCauseRequest,
    RootCauseResponse,
)
from backend.app.services import rootcause_service
from backend.app.services.views import resolve_view
from db.auth_models import User
from db.platform_models import Project

router = APIRouter(prefix="/projects/{project_id}/root-cause", tags=["root-cause"])


@router.get("/options", response_model=RootCauseOptionsResponse)
def root_cause_options(
    language: str = "en",
    project: Project = Depends(get_owned_project),
) -> RootCauseOptionsResponse:
    """Measures, dimensions and comparison windows this project's data supports.

    Takes a language because the measure and window labels it returns are what
    the picker renders — the run endpoint localises them, and a picker offering
    "Revenue" that produces a result about "الإيراد" is the same feature
    disagreeing with itself.
    """
    df = resolve_view(project.id, "analytics")
    return RootCauseOptionsResponse(**rootcause_service.options(df, language))


@router.post("", response_model=RootCauseResponse)
def run_root_cause_analysis(
    payload: RootCauseRequest,
    project: Project = Depends(get_owned_project),
    current_user: User = Depends(get_current_user),
) -> RootCauseResponse:
    df = resolve_view(project.id, "analytics")
    request = payload.model_dump()
    request["model"] = (
        payload.model
        or current_user.model_preferences.get("insights")
        or DEFAULT_INSIGHTS_MODEL
    )
    return RootCauseResponse(**rootcause_service.analyse(df, request))
