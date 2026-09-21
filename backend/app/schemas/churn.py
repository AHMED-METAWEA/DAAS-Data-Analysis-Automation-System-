from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChurnRunRequest(BaseModel):
    horizon_days: int = 90
    top_n: int = 200


class TopDriver(BaseModel):
    feature: str
    shap_value: float
    direction: str


class AtRiskCustomer(BaseModel):
    customer: str
    name: str = ""
    churn_probability: float
    risk_tier: str
    recency_days: int
    frequency: int
    monetary: float | None = None
    expected_loss: float | None = None
    top_drivers: list[TopDriver] = Field(default_factory=list)


class FeatureImportance(BaseModel):
    feature: str
    importance: float


class ChurnRunResponse(BaseModel):
    available: bool
    reason: str | None = None
    horizon_days: int | None = None
    requested_horizon_days: int | None = None
    snapshot_date: str | None = None
    cutoff_date: str | None = None
    training_cutoffs: list[str] = Field(default_factory=list)
    model: dict[str, Any] = Field(default_factory=dict)
    feature_importance: list[FeatureImportance] = Field(default_factory=list)
    shap_global_importance: list[FeatureImportance] = Field(default_factory=list)
    risk_distribution: dict[str, int] = Field(default_factory=dict)
    customers_scored: int = 0
    revenue_at_risk: float | None = None
    expected_revenue_at_risk: float | None = None
    at_risk_customers: list[AtRiskCustomer] = Field(default_factory=list)
    at_risk_ranked_by: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Full per-customer churn-probability map, used only to cross-link into
    # the Marketing agent's RFM-segment churn breakdown — not for display.
    customer_scores: dict[str, float] | None = None


class RetentionPlanRequest(BaseModel):
    churn_result: dict[str, Any]
    business_context: str = ""
    model: str | None = None


class RetentionPlanResponse(BaseModel):
    report_md: str
