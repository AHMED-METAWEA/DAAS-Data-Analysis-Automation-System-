"""Wire contracts for the CRM API.

The response models mirror ``agents.crm.contracts.CustomerRecord`` but are a
separate declaration on purpose: the domain record is what the engine computes,
these are what the browser is promised. Collapsing them would mean every
internal field rename became a breaking API change.

Identity fields (``display_name``, ``contact``) appear here because the browser
is exactly where they belong — inside the tenant's own session. They must not
travel any further; see ``agents/crm/contracts.py:strip_pii``.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field


# ── Refresh ─────────────────────────────────────────────────────────────────

class CrmRefreshRequest(BaseModel):
    # Fit a churn model as part of the refresh. Off gives a fast
    # observed-and-derived-only snapshot (RFM, lifecycle, spend).
    run_churn: bool = True
    horizon_days: int = 90


class CrmRefreshResponse(BaseModel):
    status: str  # success | partial | failed | skipped
    snapshot_id: str | None = None
    snapshot_date: date | None = None
    customers: int = 0
    # Per-component status: which of rfm / churn / clv produced values, and the
    # reason for any that did not. Lets the UI explain a missing column instead
    # of rendering dashes.
    components: dict[str, Any] = Field(default_factory=dict)
    grain: dict[str, Any] = Field(default_factory=dict)
    stage_rules: dict[str, Any] = Field(default_factory=dict)
    duration_ms: int | None = None
    reason: str | None = None


# ── Customers ───────────────────────────────────────────────────────────────

class CustomerDriver(BaseModel):
    feature: str
    shap_value: float | None = None
    direction: str | None = None


class CustomerSummary(BaseModel):
    """A row in the customer list / prioritised call list."""

    customer_id: str
    display_name: str | None = None
    snapshot_date: date

    recency_days: int | None = None
    frequency: int | None = None
    monetary: float | None = None
    avg_order_value: float | None = None
    last_order_date: date | None = None

    rfm_segment: str | None = None
    lifecycle_stage: str | None = None

    churn_probability: float | None = None
    risk_tier: str | None = None
    predicted_clv: float | None = None

    value_at_risk: float | None = None
    # "predicted_clv" | "historical_monetary". Always sent alongside the figure:
    # a prioritised list must be able to state what it prioritised on.
    value_basis: str | None = None


class CustomerListResponse(BaseModel):
    available: bool
    reason: str | None = None
    snapshot_date: date | None = None
    total: int = 0
    limit: int = 50
    offset: int = 0
    customers: list[CustomerSummary] = Field(default_factory=list)


class CustomerDetail(CustomerSummary):
    """The Customer 360 profile."""

    contact: dict[str, Any] = Field(default_factory=dict)
    tenure_days: int | None = None
    first_order_date: date | None = None
    r_score: int | None = None
    f_score: int | None = None
    m_score: int | None = None
    clv_horizon_days: int | None = None
    predicted_purchases: float | None = None
    drivers: list[CustomerDriver] = Field(default_factory=list)
    # Whether SHAP was computed for this customer. Churn explains only the
    # displayed top-N, so most customers legitimately have none — the page needs
    # to tell "no explanation computed" apart from "nothing drives their risk".
    explained: bool = False


class CustomerHistoryPoint(BaseModel):
    snapshot_date: date
    churn_probability: float | None = None
    risk_tier: str | None = None
    predicted_clv: float | None = None
    value_at_risk: float | None = None
    monetary: float | None = None
    frequency: int | None = None
    recency_days: int | None = None
    rfm_segment: str | None = None
    lifecycle_stage: str | None = None


class CustomerDetailResponse(BaseModel):
    available: bool
    reason: str | None = None
    customer: CustomerDetail | None = None
    # One point per snapshot the customer appears in — the answer to "was this
    # customer riskier last month?", which is the question persistence exists for.
    history: list[CustomerHistoryPoint] = Field(default_factory=list)


# ── Portfolio ───────────────────────────────────────────────────────────────

class SnapshotSummary(BaseModel):
    id: str
    snapshot_date: date
    status: str
    trigger: str
    customers: int
    components: dict[str, Any] = Field(default_factory=dict)
    row_count: int = 0
    history_days: int = 0
    duration_ms: int | None = None


class PortfolioResponse(BaseModel):
    available: bool
    reason: str | None = None
    snapshot_date: date | None = None
    customers: int = 0
    # Null, not zero, when the project's data carries no monetary column. A
    # measured EGP 0.00 and "there is nothing to measure" must not render the
    # same way — the UI shows an em-dash for null, never a currency figure.
    total_monetary: float | None = None
    total_value_at_risk: float | None = None
    avg_churn_probability: float | None = None
    customers_scored: int = 0
    customers_with_clv: int = 0
    customers_with_monetary: int = 0
    customers_with_value_at_risk: int = 0
    by_segment: dict[str, int] = Field(default_factory=dict)
    by_stage: dict[str, int] = Field(default_factory=dict)
    by_risk_tier: dict[str, int] = Field(default_factory=dict)
    value_basis: dict[str, int] = Field(default_factory=dict)
    latest_snapshot: SnapshotSummary | None = None


# ── Prioritisation comparison ───────────────────────────────────────────────

class RankingComparisonRow(BaseModel):
    rank: int
    customer_id: str
    display_name: str | None = None
    churn_probability: float | None = None
    monetary: float | None = None
    predicted_clv: float | None = None
    value_at_risk: float | None = None
    rfm_segment: str | None = None
    lifecycle_stage: str | None = None


class RankingComparisonResponse(BaseModel):
    """Ranking by risk alone vs. ranking by value at risk, side by side.

    The single most persuasive artifact in the pillar and nearly free to
    compute: both lists come from the same stored snapshot.
    """

    available: bool
    reason: str | None = None
    snapshot_date: date | None = None
    top_n: int = 10
    value_basis: str | None = None
    by_risk: list[RankingComparisonRow] = Field(default_factory=list)
    by_value_at_risk: list[RankingComparisonRow] = Field(default_factory=list)
    value_covered_by_risk: float = 0.0
    value_covered_by_value_at_risk: float = 0.0
    difference: float = 0.0
    # Customers appearing in the VaR list but not the risk-only list — the
    # people a risk-only ranking would have missed entirely.
    overlap: int = 0
