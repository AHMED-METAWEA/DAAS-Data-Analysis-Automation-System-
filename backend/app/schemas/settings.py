from __future__ import annotations

from pydantic import BaseModel, Field


class ProviderStatus(BaseModel):
    id: str
    name: str
    configured: bool
    honors_model_override: bool


class ProvidersStatusResponse(BaseModel):
    fallback_order: list[ProviderStatus]
    any_configured: bool


class AgentPurpose(BaseModel):
    purpose: str
    label: str
    default_model: str


class ModelPreferencesResponse(BaseModel):
    purposes: list[AgentPurpose]
    available_models: list[str]
    preferences: dict[str, str] = Field(default_factory=dict)


class UpdateModelPreferencesRequest(BaseModel):
    preferences: dict[str, str | None]
