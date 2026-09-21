"""Persistence for customer state. Reads and writes; computes nothing.

Every public function here is **total**: it returns a value for any input and
raises nothing at the caller. That is a deliberate contract rather than
defensive habit — a CRM refresh runs as a side effect of a churn analysis, and
a failure to persist history must never turn a successful analysis into a 500.
The platform's existing convention (``{"status": "skipped", "reason": ...}``)
is carried through :class:`SnapshotResult`.
"""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from db.crm_models import CrmSnapshot, CustomerState
from db.session import get_session

from .contracts import CustomerRecord, SnapshotResult

logger = logging.getLogger(__name__)

# Sort keys the customer list exposes, mapped to columns. Whitelisted rather
# than interpolated from the query string: `ORDER BY` cannot be parameterised,
# so accepting a raw column name here would be an injection point.
SORTABLE = {
    "value_at_risk": CustomerState.value_at_risk,
    "churn_probability": CustomerState.churn_probability,
    "predicted_clv": CustomerState.predicted_clv,
    "monetary": CustomerState.monetary,
    "frequency": CustomerState.frequency,
    "recency_days": CustomerState.recency_days,
    "customer_id": CustomerState.customer_id,
}


def _record_to_row(record: CustomerRecord, snapshot_id: str, project_id: str) -> dict:
    data = record.to_dict()
    data.update(snapshot_id=snapshot_id, project_id=project_id)
    return data


def save_snapshot(
    project_id: str,
    records: list[CustomerRecord],
    meta: dict,
    *,
    trigger: str = "manual",
    duration_ms: int | None = None,
) -> SnapshotResult:
    """Persist one refresh: a snapshot row plus one row per customer.

    **Idempotent by date.** Re-running a refresh for a snapshot date that has
    already been captured deletes that date's rows and rewrites them, inside a
    single transaction. The alternative — letting the unique constraint reject
    the second write — would mean a user who clicks refresh twice sees an error
    for doing something harmless, and a user whose data was corrected and
    re-ingested could never update the day's state.
    """
    if not records:
        return SnapshotResult(
            status="skipped",
            reason=meta.get("reason") or "No customers to store.",
            grain=meta.get("grain", {}),
            components=meta.get("components", {}),
        )

    snapshot_date = records[0].snapshot_date
    components = meta.get("components", {})
    # "partial" is a first-class outcome: a snapshot with segments but no churn
    # scores is genuinely useful and must not be filed next to a real failure.
    status = "success" if all(
        c.get("status") in ("ok", "pending") for c in components.values()
    ) else "partial"

    session: Session = get_session()
    try:
        with session.begin():
            session.execute(
                delete(CustomerState).where(
                    CustomerState.project_id == project_id,
                    CustomerState.snapshot_date == snapshot_date,
                )
            )
            session.execute(
                delete(CrmSnapshot).where(
                    CrmSnapshot.project_id == project_id,
                    CrmSnapshot.snapshot_date == snapshot_date,
                )
            )

            snapshot = CrmSnapshot(
                project_id=project_id,
                snapshot_date=snapshot_date,
                status=status,
                trigger=trigger,
                customers=len(records),
                components=components,
                grain=meta.get("grain", {}),
                row_count=meta.get("row_count", 0),
                history_days=meta.get("history_days", 0),
                stage_rules=meta.get("stage_rules", {}),
                duration_ms=duration_ms,
            )
            session.add(snapshot)
            session.flush()  # assign snapshot.id before the child rows reference it

            session.bulk_insert_mappings(
                CustomerState,
                [_record_to_row(r, snapshot.id, project_id) for r in records],
            )
            snapshot_id = snapshot.id

        return SnapshotResult(
            status=status,
            snapshot_id=snapshot_id,
            snapshot_date=snapshot_date,
            customers=len(records),
            components=components,
            grain=meta.get("grain", {}),
            stage_rules=meta.get("stage_rules", {}),
            duration_ms=duration_ms,
        )
    except Exception as exc:
        # Logged in full, returned as a reason. The caller is mid-analysis and
        # its job is to finish that analysis, not to re-raise a storage problem.
        logger.exception("Failed to persist CRM snapshot for project %s", project_id)
        return SnapshotResult(status="failed", reason=f"{type(exc).__name__}: {exc}")
    finally:
        session.close()


def latest_snapshot(project_id: str) -> CrmSnapshot | None:
    """The most recent snapshot for a project, or ``None`` if never refreshed."""
    session = get_session()
    try:
        return session.execute(
            select(CrmSnapshot)
            .where(CrmSnapshot.project_id == project_id)
            .order_by(CrmSnapshot.snapshot_date.desc(), CrmSnapshot.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
    except Exception:
        logger.exception("Could not read latest CRM snapshot for project %s", project_id)
        return None
    finally:
        session.close()


def list_snapshots(project_id: str, limit: int = 30) -> list[CrmSnapshot]:
    session = get_session()
    try:
        return list(
            session.execute(
                select(CrmSnapshot)
                .where(CrmSnapshot.project_id == project_id)
                .order_by(CrmSnapshot.snapshot_date.desc())
                .limit(limit)
            ).scalars()
        )
    except Exception:
        logger.exception("Could not list CRM snapshots for project %s", project_id)
        return []
    finally:
        session.close()


def list_customers(
    project_id: str,
    *,
    snapshot_date: date | None = None,
    segment: str | None = None,
    stage: str | None = None,
    tier: str | None = None,
    search: str | None = None,
    sort_by: str = "value_at_risk",
    descending: bool = True,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[CustomerState], int]:
    """A page of customers from one snapshot, plus the unfiltered-by-page total.

    Defaults to the latest snapshot. Returns ``([], 0)`` when the project has
    never been refreshed, which the API turns into an explicit "no snapshot yet"
    rather than an empty customer list — the two mean very different things to
    someone looking at the page.
    """
    session = get_session()
    try:
        if snapshot_date is None:
            snapshot_date = session.execute(
                select(func.max(CrmSnapshot.snapshot_date)).where(
                    CrmSnapshot.project_id == project_id
                )
            ).scalar()
            if snapshot_date is None:
                return [], 0

        conditions = [
            CustomerState.project_id == project_id,
            CustomerState.snapshot_date == snapshot_date,
        ]
        if segment:
            conditions.append(CustomerState.rfm_segment == segment)
        if stage:
            conditions.append(CustomerState.lifecycle_stage == stage)
        if tier:
            conditions.append(CustomerState.risk_tier == tier)
        if search:
            pattern = f"%{search}%"
            conditions.append(
                CustomerState.customer_id.ilike(pattern)
                | CustomerState.display_name.ilike(pattern)
            )

        total = session.execute(
            select(func.count()).select_from(CustomerState).where(*conditions)
        ).scalar_one()

        column = SORTABLE.get(sort_by, CustomerState.value_at_risk)
        # NULLs last in both directions. Customers with no CLV or no churn score
        # are "not yet modelled", and letting Postgres float them to the top of a
        # descending sort would put the least-known customers at the head of a
        # list whose entire purpose is to rank by what is known.
        order = column.desc().nullslast() if descending else column.asc().nullslast()

        rows = list(
            session.execute(
                select(CustomerState)
                .where(*conditions)
                .order_by(order, CustomerState.customer_id.asc())
                .limit(limit)
                .offset(offset)
            ).scalars()
        )
        return rows, total
    except Exception:
        logger.exception("Could not list CRM customers for project %s", project_id)
        return [], 0
    finally:
        session.close()


def get_customer(
    project_id: str, customer_id: str, *, snapshot_date: date | None = None
) -> CustomerState | None:
    """One customer's state at one snapshot (latest by default)."""
    session = get_session()
    try:
        query = select(CustomerState).where(
            CustomerState.project_id == project_id,
            CustomerState.customer_id == customer_id,
        )
        if snapshot_date is not None:
            query = query.where(CustomerState.snapshot_date == snapshot_date)
        return session.execute(
            query.order_by(CustomerState.snapshot_date.desc()).limit(1)
        ).scalar_one_or_none()
    except Exception:
        logger.exception("Could not read customer %s in project %s", customer_id, project_id)
        return None
    finally:
        session.close()


def get_customer_timeline(
    project_id: str, customer_id: str, *, limit: int = 90
) -> list[CustomerState]:
    """One customer across every snapshot, oldest first.

    This is the answer to "was this customer riskier last month?" — the single
    question that made persistence necessary in the first place.
    """
    session = get_session()
    try:
        rows = list(
            session.execute(
                select(CustomerState)
                .where(
                    CustomerState.project_id == project_id,
                    CustomerState.customer_id == customer_id,
                )
                .order_by(CustomerState.snapshot_date.desc())
                .limit(limit)
            ).scalars()
        )
        return list(reversed(rows))
    except Exception:
        logger.exception("Could not read timeline for customer %s", customer_id)
        return []
    finally:
        session.close()


def portfolio_summary(project_id: str, snapshot_date: date | None = None) -> dict:
    """Book-level aggregates for the CRM header, computed in the database.

    Deliberately SQL rather than a pandas pass over a fetched customer list:
    the header must stay constant-cost as the book grows, and pulling every
    customer into Python to sum one column is how a page that is fast on the
    demo dataset becomes unusable on a real one.
    """
    session = get_session()
    try:
        if snapshot_date is None:
            snapshot_date = session.execute(
                select(func.max(CrmSnapshot.snapshot_date)).where(
                    CrmSnapshot.project_id == project_id
                )
            ).scalar()
            if snapshot_date is None:
                return {"available": False, "reason": "No CRM snapshot yet."}

        conditions = (
            CustomerState.project_id == project_id,
            CustomerState.snapshot_date == snapshot_date,
        )

        totals = session.execute(
            select(
                func.count().label("customers"),
                func.sum(CustomerState.monetary).label("total_monetary"),
                func.sum(CustomerState.value_at_risk).label("total_value_at_risk"),
                func.avg(CustomerState.churn_probability).label("avg_churn"),
                func.count(CustomerState.churn_probability).label("scored"),
                func.count(CustomerState.predicted_clv).label("with_clv"),
                # COUNT over a nullable column counts non-nulls, which is how
                # "nobody has a spend figure" is told apart from "everyone spent
                # zero". Both make SUM return 0; only one of them means the
                # headline figure is meaningful.
                func.count(CustomerState.monetary).label("with_monetary"),
                func.count(CustomerState.value_at_risk).label("with_var"),
            ).where(*conditions)
        ).one()

        # A currency headline of "EGP 0.00" on a dataset with no monetary column
        # is worse than no headline: it reads as a measured result. Null means
        # "not measurable here" and the UI is expected to render it as such.
        def _money(value, coverage: int) -> float | None:
            return round(float(value or 0.0), 2) if coverage else None

        def _breakdown(column):
            return {
                str(key): int(count)
                for key, count in session.execute(
                    select(column, func.count())
                    .where(*conditions, column.isnot(None))
                    .group_by(column)
                ).all()
            }

        basis = session.execute(
            select(CustomerState.value_basis, func.count())
            .where(*conditions, CustomerState.value_basis.isnot(None))
            .group_by(CustomerState.value_basis)
        ).all()

        return {
            "available": True,
            "snapshot_date": snapshot_date,
            "customers": int(totals.customers or 0),
            "total_monetary": _money(totals.total_monetary, totals.with_monetary),
            "total_value_at_risk": _money(totals.total_value_at_risk, totals.with_var),
            "avg_churn_probability": (
                round(float(totals.avg_churn), 4) if totals.avg_churn is not None else None
            ),
            "customers_scored": int(totals.scored or 0),
            "customers_with_clv": int(totals.with_clv or 0),
            "customers_with_monetary": int(totals.with_monetary or 0),
            "customers_with_value_at_risk": int(totals.with_var or 0),
            "by_segment": _breakdown(CustomerState.rfm_segment),
            "by_stage": _breakdown(CustomerState.lifecycle_stage),
            "by_risk_tier": _breakdown(CustomerState.risk_tier),
            # What the value-at-risk total was actually built from. A headline
            # currency figure with no stated basis is the kind of number that
            # gets quoted in a meeting and cannot be defended afterwards.
            "value_basis": {str(k): int(v) for k, v in basis},
        }
    except Exception as exc:
        logger.exception("Could not summarise CRM portfolio for project %s", project_id)
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}
    finally:
        session.close()
