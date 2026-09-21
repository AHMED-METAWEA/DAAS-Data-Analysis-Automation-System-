from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from backend.app.schemas.insights import GroundingOut


class MarketingRunRequest(BaseModel):
    objective: str = ""
    budget: str = ""
    channels: list[str] = Field(default_factory=list)
    brand_voice: str = ""
    business_context: str = ""
    # Plain dicts from a prior /forecast/run's `outputs`, and a prior
    # /churn/run's full response — passed through by the client so this
    # endpoint stays a stateless request/response call, like the rest of
    # the API (no server-side cross-agent session state).
    forecast_outputs: list[dict[str, Any]] | None = None
    churn_result: dict[str, Any] | None = None
    model: str | None = None


class MarketingRunResponse(BaseModel):
    schema_summary: dict[str, Any] = Field(default_factory=dict)
    kpi: dict[str, Any] = Field(default_factory=dict)
    rfm: dict[str, Any] = Field(default_factory=dict)
    marketing_kpis: dict[str, Any] = Field(default_factory=dict)
    channels: dict[str, Any] = Field(default_factory=dict)
    churn: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    report_md: str
    # Post-hoc check: which of the report's factual figures trace back to
    # the computed payload above (mirrors the Insights report's grounding
    # check). Forward-looking recommendation numbers the model is asked to
    # originate (e.g. suggested budget %) are expected to show as unverified
    # — this flags misstated FACTS, not the model's actual recommendations.
    grounding: GroundingOut = Field(
        default_factory=lambda: GroundingOut(status="none", label="", total=0, verified_count=0, coverage=1.0)
    )


class CampaignPlanRequest(BaseModel):
    marketing_payload: dict[str, Any]
    objective: str = ""
    budget: str = ""
    channels: list[str] = Field(default_factory=list)
    brand_voice: str = ""
    business_context: str = ""
    forecast_outputs: list[dict[str, Any]] | None = None
    model: str | None = None


class Campaign(BaseModel):
    name: str = ""
    target_segment: str = ""
    objective: str = ""
    channel: str = ""
    offer: str = ""
    message_angle: str = ""
    budget_allocation_pct: float | None = None
    priority: str = ""
    primary_kpi: str = ""
    expected_impact: str = ""


class CampaignPlanResponse(BaseModel):
    campaigns: list[Campaign] = Field(default_factory=list)
    summary: str = ""
    error: str | None = None


class AdCopyRequest(BaseModel):
    segment: str
    channel: str
    platform: str = "Generic"
    offer: str = ""
    brand_voice: str = ""
    segment_stats: dict[str, Any] | None = None
    playbook: str = ""
    model: str | None = None


class AdCopyResponse(BaseModel):
    headlines: list[str] = Field(default_factory=list)
    primary_text: list[str] = Field(default_factory=list)
    cta: list[str] = Field(default_factory=list)
    email_subject: str = ""
    email_preview: str = ""
    sms: str = ""
    hashtags: list[str] = Field(default_factory=list)
    notes: str = ""
    error: str | None = None


class MarketingChatMessage(BaseModel):
    role: str
    content: str


class MarketingChatRequest(BaseModel):
    message: str
    report_md: str
    marketing_payload: dict[str, Any]
    history: list[MarketingChatMessage] = Field(default_factory=list)
    model: str | None = None


class MarketingChatResponse(BaseModel):
    answer: str
