from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from agents.constants import (
    AVAILABLE_MODELS,
    DEFAULT_CHURN_MODEL,
    DEFAULT_CODER_MODEL,
    DEFAULT_COPILOT_MODEL,
    DEFAULT_DASHBOARD_MODEL,
    DEFAULT_EXPLAIN_CHART_MODEL,
    DEFAULT_FORECAST_MODEL,
    DEFAULT_INSIGHTS_MODEL,
    DEFAULT_MARKETING_MODEL,
    DEFAULT_PLANNER_MODEL,
    DEFAULT_SCHEMA_DISCOVERY_MODEL,
    DEFAULT_VIZ_MODEL,
)
from backend.app.api.deps import get_current_user, get_db
from backend.app.schemas.settings import (
    AgentPurpose,
    ModelPreferencesResponse,
    ProvidersStatusResponse,
    ProviderStatus,
    UpdateModelPreferencesRequest,
)
from db.auth_models import User
from tools.llm_client import provider_status

router = APIRouter(prefix="/settings", tags=["settings"])

_PROVIDER_NAMES = {
    "groq": "Groq",
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "openrouter": "OpenRouter",
}

# purpose -> (display label, default Groq model). Only Groq honors a runtime
# model override (tools/llm_client.py) — every other provider always serves
# its own fixed model for the purpose (tools/llm_provider_models.py).
_PURPOSES: list[tuple[str, str, str]] = [
    ("planner", "Data Cleaning · Planner", DEFAULT_PLANNER_MODEL),
    ("coder", "Data Cleaning · Coder", DEFAULT_CODER_MODEL),
    ("viz", "Visualization · AI Chart Studio", DEFAULT_VIZ_MODEL),
    ("dashboard", "Visualization · Auto Dashboard", DEFAULT_DASHBOARD_MODEL),
    ("insights", "Business Insights", DEFAULT_INSIGHTS_MODEL),
    ("forecast", "Forecasting narrative", DEFAULT_FORECAST_MODEL),
    ("marketing", "Marketing", DEFAULT_MARKETING_MODEL),
    ("churn", "Churn Prediction", DEFAULT_CHURN_MODEL),
    ("schema_discovery", "Schema Discovery", DEFAULT_SCHEMA_DISCOVERY_MODEL),
    ("copilot", "Analyst Copilot · Router & Narration", DEFAULT_COPILOT_MODEL),
    ("explain_chart", "Chart Explanations", DEFAULT_EXPLAIN_CHART_MODEL),
]


def default_model_for(purpose: str) -> str:
    for p, _, default in _PURPOSES:
        if p == purpose:
            return default
    return DEFAULT_PLANNER_MODEL


@router.get("/providers", response_model=ProvidersStatusResponse)
def get_providers_status() -> ProvidersStatusResponse:
    statuses = provider_status()
    fallback_order = [
        ProviderStatus(
            id=s["id"],
            name=_PROVIDER_NAMES.get(s["id"], s["id"]),
            configured=bool(s["configured"]),
            honors_model_override=s["id"] == "groq",
        )
        for s in statuses
    ]
    return ProvidersStatusResponse(
        fallback_order=fallback_order,
        any_configured=any(s.configured for s in fallback_order),
    )


@router.get("/models", response_model=ModelPreferencesResponse)
def get_model_preferences(current_user: User = Depends(get_current_user)) -> ModelPreferencesResponse:
    return ModelPreferencesResponse(
        purposes=[AgentPurpose(purpose=p, label=label, default_model=default) for p, label, default in _PURPOSES],
        available_models=AVAILABLE_MODELS,
        preferences=current_user.model_preferences or {},
    )


@router.patch("/models", response_model=ModelPreferencesResponse)
def update_model_preferences(
    payload: UpdateModelPreferencesRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ModelPreferencesResponse:
    valid_purposes = {p for p, _, _ in _PURPOSES}
    prefs = dict(current_user.model_preferences or {})
    for purpose, model in payload.preferences.items():
        if purpose not in valid_purposes:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Unknown purpose: {purpose}")
        if model is None:
            prefs.pop(purpose, None)
            continue
        if model not in AVAILABLE_MODELS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Unknown model: {model}")
        prefs[purpose] = model

    user = db.get(User, current_user.id)
    user.model_preferences = prefs
    db.commit()

    return ModelPreferencesResponse(
        purposes=[AgentPurpose(purpose=p, label=label, default_model=default) for p, label, default in _PURPOSES],
        available_models=AVAILABLE_MODELS,
        preferences=prefs,
    )
