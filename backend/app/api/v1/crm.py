"""CRM HTTP surface — thin by design.

Every endpoint here does three things: authorise the project, call one function
in ``agents.crm``, and shape the result for the wire. No analysis, no SQL, no
business rules. When a rule needs changing it should be changeable in the
domain layer without an HTTP test telling you about it.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query

from agents.crm import repository, service
from backend.app.api.deps import get_owned_project
from backend.app.schemas.crm import (
    CrmRefreshRequest,
    CrmRefreshResponse,
    CustomerDetail,
    CustomerDetailResponse,
    CustomerHistoryPoint,
    CustomerListResponse,
    CustomerSummary,
    PortfolioResponse,
    RankingComparisonResponse,
    RankingComparisonRow,
    SnapshotSummary,
)
from db.crm_models import CustomerState
from db.platform_models import Project

router = APIRouter(prefix="/projects/{project_id}/crm", tags=["crm"])

_NO_SNAPSHOT = "No CRM snapshot yet — run a refresh to build customer state."


def _summary(row: CustomerState) -> CustomerSummary:
    return CustomerSummary(
        customer_id=row.customer_id,
        display_name=row.display_name,
        snapshot_date=row.snapshot_date,
        recency_days=row.recency_days,
        frequency=row.frequency,
        monetary=row.monetary,
        avg_order_value=row.avg_order_value,
        last_order_date=row.last_order_date,
        rfm_segment=row.rfm_segment,
        lifecycle_stage=row.lifecycle_stage,
        churn_probability=row.churn_probability,
        risk_tier=row.risk_tier,
        predicted_clv=row.predicted_clv,
        value_at_risk=row.value_at_risk,
        value_basis=row.value_basis,
    )


@router.post("/refresh", response_model=CrmRefreshResponse)
def refresh(
    payload: CrmRefreshRequest, project: Project = Depends(get_owned_project)
) -> CrmRefreshResponse:
    """Recompute and store customer state for the project.

    Returns 200 with a status rather than raising on failure: the caller is a
    page that must render either way, and "the refresh could not run because
    there is no saved data" is information, not an error condition.
    """
    result = service.refresh_customer_state(
        project.id, run_churn=payload.run_churn, horizon_days=payload.horizon_days
    )
    return CrmRefreshResponse(**result.to_dict())


@router.get("/portfolio", response_model=PortfolioResponse)
def portfolio(project: Project = Depends(get_owned_project)) -> PortfolioResponse:
    summary = repository.portfolio_summary(project.id)
    if not summary.get("available"):
        return PortfolioResponse(available=False, reason=summary.get("reason") or _NO_SNAPSHOT)

    snapshot = repository.latest_snapshot(project.id)
    latest = (
        SnapshotSummary(
            id=snapshot.id,
            snapshot_date=snapshot.snapshot_date,
            status=snapshot.status,
            trigger=snapshot.trigger,
            customers=snapshot.customers,
            components=snapshot.components,
            row_count=snapshot.row_count,
            history_days=snapshot.history_days,
            duration_ms=snapshot.duration_ms,
        )
        if snapshot is not None
        else None
    )
    return PortfolioResponse(**summary, latest_snapshot=latest)


@router.get("/customers", response_model=CustomerListResponse)
def list_customers(
    project: Project = Depends(get_owned_project),
    segment: str | None = None,
    stage: str | None = None,
    tier: str | None = None,
    search: str | None = None,
    sort_by: str = Query("value_at_risk"),
    descending: bool = True,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    snapshot_date: date | None = None,
) -> CustomerListResponse:
    rows, total = repository.list_customers(
        project.id,
        snapshot_date=snapshot_date,
        segment=segment,
        stage=stage,
        tier=tier,
        search=search,
        sort_by=sort_by,
        descending=descending,
        limit=limit,
        offset=offset,
    )
    if not rows and total == 0:
        # An empty page after a filter is a legitimate empty result; an empty
        # page with no snapshot at all is a different state and says so.
        if repository.latest_snapshot(project.id) is None:
            return CustomerListResponse(available=False, reason=_NO_SNAPSHOT)

    return CustomerListResponse(
        available=True,
        snapshot_date=rows[0].snapshot_date if rows else snapshot_date,
        total=total,
        limit=limit,
        offset=offset,
        customers=[_summary(row) for row in rows],
    )


@router.get("/customers/{customer_id}", response_model=CustomerDetailResponse)
def customer_detail(
    customer_id: str, project: Project = Depends(get_owned_project)
) -> CustomerDetailResponse:
    row = repository.get_customer(project.id, customer_id)
    if row is None:
        return CustomerDetailResponse(
            available=False, reason=f"No stored state for customer '{customer_id}'."
        )

    detail = CustomerDetail(
        **_summary(row).model_dump(),
        contact=row.contact or {},
        tenure_days=row.tenure_days,
        first_order_date=row.first_order_date,
        r_score=row.r_score,
        f_score=row.f_score,
        m_score=row.m_score,
        clv_horizon_days=row.clv_horizon_days,
        predicted_purchases=row.predicted_purchases,
        drivers=row.drivers or [],
        explained=bool(row.drivers),
    )

    history = [
        CustomerHistoryPoint(
            snapshot_date=point.snapshot_date,
            churn_probability=point.churn_probability,
            risk_tier=point.risk_tier,
            predicted_clv=point.predicted_clv,
            value_at_risk=point.value_at_risk,
            monetary=point.monetary,
            frequency=point.frequency,
            recency_days=point.recency_days,
            rfm_segment=point.rfm_segment,
            lifecycle_stage=point.lifecycle_stage,
        )
        for point in repository.get_customer_timeline(project.id, customer_id)
    ]
    return CustomerDetailResponse(available=True, customer=detail, history=history)


@router.get("/snapshots", response_model=list[SnapshotSummary])
def snapshots(
    project: Project = Depends(get_owned_project), limit: int = Query(30, ge=1, le=365)
) -> list[SnapshotSummary]:
    return [
        SnapshotSummary(
            id=s.id,
            snapshot_date=s.snapshot_date,
            status=s.status,
            trigger=s.trigger,
            customers=s.customers,
            components=s.components,
            row_count=s.row_count,
            history_days=s.history_days,
            duration_ms=s.duration_ms,
        )
        for s in repository.list_snapshots(project.id, limit=limit)
    ]


@router.get("/ranking-comparison", response_model=RankingComparisonResponse)
def ranking_comparison(
    project: Project = Depends(get_owned_project), top_n: int = Query(10, ge=1, le=100)
) -> RankingComparisonResponse:
    """Risk-only ranking vs. value-at-risk ranking, from the same snapshot.

    Both lists are read from stored state rather than recomputed, so the two
    rankings provably describe the same customers at the same moment — which is
    the entire claim the comparison makes.
    """
    by_risk_rows, _ = repository.list_customers(
        project.id, sort_by="churn_probability", descending=True, limit=top_n
    )
    if not by_risk_rows:
        return RankingComparisonResponse(available=False, reason=_NO_SNAPSHOT)

    by_var_rows, _ = repository.list_customers(
        project.id, sort_by="value_at_risk", descending=True, limit=top_n
    )

    def _rows(rows: list[CustomerState]) -> list[RankingComparisonRow]:
        return [
            RankingComparisonRow(
                rank=index,
                customer_id=row.customer_id,
                display_name=row.display_name,
                churn_probability=row.churn_probability,
                monetary=row.monetary,
                predicted_clv=row.predicted_clv,
                value_at_risk=row.value_at_risk,
                rfm_segment=row.rfm_segment,
                lifecycle_stage=row.lifecycle_stage,
            )
            for index, row in enumerate(rows, start=1)
        ]

    covered_by_risk = sum(row.value_at_risk or 0.0 for row in by_risk_rows)
    covered_by_var = sum(row.value_at_risk or 0.0 for row in by_var_rows)
    overlap = len({r.customer_id for r in by_risk_rows} & {r.customer_id for r in by_var_rows})

    return RankingComparisonResponse(
        available=True,
        snapshot_date=by_var_rows[0].snapshot_date,
        top_n=top_n,
        value_basis=next((r.value_basis for r in by_var_rows if r.value_basis), None),
        by_risk=_rows(by_risk_rows),
        by_value_at_risk=_rows(by_var_rows),
        value_covered_by_risk=round(covered_by_risk, 2),
        value_covered_by_value_at_risk=round(covered_by_var, 2),
        difference=round(covered_by_var - covered_by_risk, 2),
        overlap=overlap,
    )
