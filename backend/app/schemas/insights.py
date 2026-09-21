from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from backend.app.schemas.visualization import DashboardResponse


class InsightsRequest(BaseModel):
    business_context: str = ""
    template_override: str | None = None
    model: str | None = None


class GroundingOut(BaseModel):
    status: str
    label: str
    total: int
    verified_count: int
    coverage: float
    # Figures the checker could not trace to a computed value (surfaced so the
    # reader can scrutinise them directly).
    unverified: list[str] = []
    # Per-figure citation: each verified number and the exact source value it
    # was matched against — the machine-checkable audit trail behind the badge.
    traces: list[dict[str, Any]] = []


class FigureOut(BaseModel):
    """One computed figure the report was allowed to cite, with its provenance.

    Surfaced so a reader can check any number in the prose against the formula
    that produced it — the audit trail is part of the product, not a debug aid.
    """

    key: str
    label: str
    value: float
    unit: str
    display: str
    formula: str
    period: str = ""
    quality: str = "exact"
    assumption: str = ""


class VerificationOut(BaseModel):
    """Verdict on how every number in the report got there."""

    status: str
    label: str
    # Figures inserted verbatim by the calculation engine (impossible to fabricate).
    figures_cited_from_engine: int = 0
    # Figures the model typed itself, and how many of those checked out.
    figures_typed_by_model: int = 0
    typed_and_verified: int = 0
    citation_rate: float = 1.0
    unverified_figures: list[dict[str, Any]] = []
    unknown_citations: list[str] = []
    # Currency symbols the dataset cannot support.
    unsupported_currency_claims: list[str] = []
    # Recommendations whose stated value cites a level rather than a change.
    misvalued_actions: list[str] = []
    # Figures written without naming the metric they belong to.
    ambiguous_citations: list[str] = []
    corrected: bool = False


class InsightsResponse(BaseModel):
    report_md: str
    template: str
    grounding: GroundingOut
    analytics: dict[str, Any]
    dashboard: DashboardResponse
    # ── Audit surface ──────────────────────────────────────────────────────
    verification: VerificationOut | None = None
    figures: list[FigureOut] = []
    # Findings ranked by money at stake — the computed priority order the
    # report was written against.
    evidence: list[dict[str, Any]] = []
    # Questions this dataset provably cannot answer.
    blind_spots: list[str] = []
    # The full deterministic decision layer (period bridges, concentration,
    # margin, discount, scenarios) for the UI to render directly.
    decision: dict[str, Any] = {}


class InsightsChatMessage(BaseModel):
    role: str
    content: str


class InsightsChatRequest(BaseModel):
    message: str
    report_md: str
    template_label: str = ""
    history: list[InsightsChatMessage] = []
    model: str | None = None


class InsightsChatResponse(BaseModel):
    answer: str
    output: str = ""
    error: str | None = None
    figure: dict[str, Any] | None = None
    preview: list[dict[str, Any]] | None = None
    grounded: bool = True
