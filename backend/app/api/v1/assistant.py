from __future__ import annotations

from fastapi import APIRouter, Depends

from agents.constants import DEFAULT_INSIGHTS_MODEL
from backend.app.api.deps import get_current_user, get_owned_project
from backend.app.schemas.assistant import AskRequest, AskResponse
from backend.app.services.analysis_chat import run_grounded_chat
from backend.app.services.views import resolve_view
from db.auth_models import User
from db.platform_models import Project

router = APIRouter(prefix="/projects/{project_id}", tags=["assistant"])

_SYSTEM_PROMPT = (
    "You are DAAS, a business-intelligence assistant grounded in this "
    "project's saved data. Answer the user's question honestly:\n"
    "1. Use ONLY the data described below — never invent numbers.\n"
    "2. If you can compute the answer, write a ```python``` block using the "
    "DataFrame `df` to do it (it's already loaded).\n"
    "3. If the question can't be answered from this data, say so plainly.\n"
    "4. Keep answers concise and reference concrete figures."
)


@router.post("/ask", response_model=AskResponse)
def ask_daas(
    payload: AskRequest,
    project: Project = Depends(get_owned_project),
    current_user: User = Depends(get_current_user),
) -> AskResponse:
    df = resolve_view(project.id, "analytics")
    result = run_grounded_chat(
        system_context=_SYSTEM_PROMPT,
        history=[m.model_dump() for m in payload.history],
        message=payload.message,
        df=df,
        model=payload.model or current_user.model_preferences.get("insights") or DEFAULT_INSIGHTS_MODEL,
        purpose="insights",
    )
    return AskResponse(**result)
