"""CRM platform tables — the stateful customer layer.

Everything else in DAAS is a *computation*: you ask a question, an engine runs,
a number reaches the browser, and the number is gone. A CRM cannot work that
way. "Was this customer riskier last month than they are today?" is not a
question you can answer by recomputing — recomputing tells you what today's
model thinks about today's data, which is a different question that happens to
produce a similar-looking number.

So this module introduces the first genuinely *stateful* object in the platform:
a per-customer, per-snapshot record that is written once and never recomputed.

Two tables, and the split matters:

:class:`CrmSnapshot` is one refresh — when it ran, over what data, which
components succeeded. :class:`CustomerState` is one customer as they stood in
that refresh.  The reason for the split is the same reason ``monitor_runs``
exists separately from ``monitor_alerts``: without a run record, "this project
has no at-risk customers" and "the refresh has been failing for six days" are
indistinguishable in the UI, and only one of them is good news.

Lives in the shared ``public`` schema alongside the other platform tables and is
managed by the same Alembic environment (see alembic/env.py). Per-project
*data* still lives in ``project_<slug>`` schemas; this is metadata about
customers, not the customer data itself.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

# Imported for its side effect, not its name: ``CrmSnapshot.project`` declares
# its target as the *string* "Project", which SQLAlchemy resolves at
# mapper-configuration time by looking the class up in the registry. Importing
# this module on its own — as a test that only needs the repository does —
# would otherwise configure the mapper with no Project class registered and
# fail with "expression 'Project' failed to locate a name". The same reasoning
# is why db/platform_models.py imports db.auth_models at *its* top.
from db import platform_models  # noqa: F401  (registers Project on Base.metadata)
from db.base import Base

if TYPE_CHECKING:
    from db.platform_models import Project


def _new_id() -> str:
    return uuid.uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(UTC)


# ── Vocabulary, defined once so the engine, the API and the UI cannot drift ──

SNAPSHOT_STATUSES = ("running", "success", "partial", "failed")

# Which intelligence components a snapshot managed to compute. A snapshot with
# RFM but no churn is useful and must not be recorded as a failure — it is
# recorded as "partial" with the reason attached, so the UI can say *what* is
# missing rather than showing an empty page.
SNAPSHOT_COMPONENTS = ("rfm", "churn", "clv")

RISK_TIERS = ("High", "Medium", "Low")

# Deterministic, rule-based, and explainable — deliberately not a 0-100 composite
# score. A weighted health score collapses well-founded numbers into an arbitrary
# one, and "why 0.3 on recency?" has no good answer. A stage has a rule you can
# print on the screen next to it.
LIFECYCLE_STAGES = ("New", "Growing", "Established", "Declining", "Dormant", "Churned")

# What `CustomerState.value_at_risk` was actually multiplied by. The platform
# should never show a prioritised list without being able to say which number
# drove the ranking — this is the same discipline as the churn engine's
# `at_risk_ranked_by` and the insights evidence trail.
VALUE_BASES = ("predicted_clv", "historical_monetary")


class CrmSnapshot(Base):
    """One CRM refresh for one project.

    Snapshots are the unit of history. Re-running a refresh for a date that has
    already been captured *replaces* that date's rows rather than adding a
    second version of the same day (see ``agents/crm/repository.py``), so the
    customer timeline stays one record per customer per day no matter how many
    times someone clicks refresh.
    """

    __tablename__ = "crm_snapshots"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    project_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("projects.id"), nullable=False, index=True
    )
    # The business date the state describes — the last date present in the data,
    # NOT the wall-clock date the refresh ran. Loading three months of backdated
    # transactions should produce a snapshot dated to the data, otherwise the
    # customer timeline records when someone happened to click a button.
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    # running | success | partial | failed
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    # "manual" | "churn_run" | "schedule" — a refresh triggered as a side effect
    # of a churn analysis must be distinguishable from one a user asked for.
    trigger: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")

    customers: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Which of SNAPSHOT_COMPONENTS actually produced values, and for the ones
    # that did not, why. This is what lets the UI say "CLV unavailable: fewer
    # than 30 repeat buyers" instead of rendering a column of dashes.
    components: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # Which columns the schema-intelligence layer resolved as the customer /
    # date / order / money grain for this snapshot. Stored because a later
    # re-ingest can change the detected grain, and a customer count that halves
    # between two snapshots is otherwise an unexplainable mystery.
    grain: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # Row count and date span of the source view, for the same reason
    # MetricSnapshot carries them: a jump caused by an import must be tellable
    # apart from a jump in the business.
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    history_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # The rule thresholds used for lifecycle staging in THIS snapshot. They are
    # derived from the data's own purchase cadence rather than hardcoded, so
    # they must be recorded or the stage assignments become unauditable.
    stage_rules: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )

    project: Mapped["Project"] = relationship()
    states: Mapped[list[CustomerState]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("project_id", "snapshot_date", name="uq_crm_snapshot_project_date"),
    )


class CustomerState(Base):
    """One customer, as they stood on one snapshot date.

    Written for **every** customer, not just the at-risk ones. The existing
    churn persistence stores only the displayed top-N, which is why it cannot
    answer "show me this specific customer" for the other 95% of the book — and
    a CRM whose customer page works for 5% of customers is not a CRM.

    **PII boundary.** ``display_name`` and ``contact`` are the only fields here
    that identify a human being, and they are deliberately isolated in this one
    place. Nothing that reaches an LLM prompt may carry them; see
    ``agents/crm/contracts.py:strip_pii``, which is the single enforcement point.
    """

    __tablename__ = "customer_state"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    snapshot_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("crm_snapshots.id"), nullable=False, index=True
    )
    project_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("projects.id"), nullable=False, index=True
    )
    # The customer's identifier *in the source data*, as text. Not a foreign key
    # to anything: the platform does not own the customer, the tenant's data
    # does, and the id may be an int, a UUID, an email or an Arabic name.
    customer_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)

    # ── Identity (PII — see class docstring) ────────────────────────────────
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # ── Observed facts: measured from transactions, never modelled ──────────
    # These are arithmetic over the source data. They are the ground the models
    # stand on, so they are stored separately from anything predicted and are
    # never overwritten by a model output.
    recency_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    frequency: Mapped[int | None] = mapped_column(Integer, nullable=True)
    monetary: Mapped[float | None] = mapped_column(Float, nullable=True)
    tenure_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    avg_order_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    first_order_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_order_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # ── Derived: RFM segmentation ───────────────────────────────────────────
    rfm_segment: Mapped[str | None] = mapped_column(String(64), nullable=True)
    r_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    f_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    m_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lifecycle_stage: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # ── Predicted: model outputs ────────────────────────────────────────────
    churn_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_tier: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # Populated by the CLV agent (Stage 1). Null here does not mean "zero value"
    # — it means "not modelled yet", and the API must keep those distinct.
    predicted_clv: Mapped[float | None] = mapped_column(Float, nullable=True)
    clv_horizon_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    predicted_purchases: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ── Prioritisation ──────────────────────────────────────────────────────
    # churn_probability x (predicted_clv or monetary). `value_basis` records
    # which, so a ranked call list can always state what it ranked on.
    value_at_risk: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_basis: Mapped[str | None] = mapped_column(String(24), nullable=True)

    # Per-customer SHAP attribution, when it was computed for this customer.
    # Shape: [{"feature": str, "impact": float, "direction": "increases_risk"|...}]
    drivers: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    snapshot: Mapped[CrmSnapshot] = relationship(back_populates="states")

    __table_args__ = (
        # One row per customer per day. This is what makes a refresh idempotent
        # and what stops a double-click from forking the customer's timeline.
        UniqueConstraint(
            "project_id", "customer_id", "snapshot_date", name="uq_customer_state_identity"
        ),
    )


# The three access patterns this table has, made explicit. Without these the
# customer list page degrades into a sequential scan as soon as a project has a
# few hundred thousand customer-days.
Index("ix_customer_state_project_date", CustomerState.project_id, CustomerState.snapshot_date)
Index(
    "ix_customer_state_value_at_risk",
    CustomerState.project_id,
    CustomerState.snapshot_date,
    CustomerState.value_at_risk.desc(),
)
# The Customer 360 timeline: one customer's whole history, oldest to newest.
Index(
    "ix_customer_state_timeline",
    CustomerState.project_id,
    CustomerState.customer_id,
    CustomerState.snapshot_date.desc(),
)
