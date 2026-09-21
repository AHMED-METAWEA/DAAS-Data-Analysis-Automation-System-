"""CRM domain vocabulary, the customer record, and the PII boundary.

This module is deliberately dependency-free (stdlib only). Everything else in
the CRM package — the compute, the persistence, the API — agrees on the shapes
defined here, which is what stops the three layers from drifting into three
slightly different ideas of what a customer is.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import date

# ── Risk tiering ────────────────────────────────────────────────────────────
# Imported from the churn engine's thresholds rather than redefined, so a
# customer cannot be "High" risk on the churn page and "Medium" on the CRM page.
from agents.churn.engine import HIGH_RISK, MED_RISK

LIFECYCLE_STAGES = ("New", "Growing", "Established", "Declining", "Dormant", "Churned")

VALUE_BASIS_CLV = "predicted_clv"
VALUE_BASIS_MONETARY = "historical_monetary"


def risk_tier(probability: float | None) -> str | None:
    """Map a churn probability onto the platform-wide risk vocabulary."""
    if probability is None:
        return None
    if probability >= HIGH_RISK:
        return "High"
    if probability >= MED_RISK:
        return "Medium"
    return "Low"


# ── The PII boundary ────────────────────────────────────────────────────────
#
# The platform sends analysis context to third-party LLM providers. A customer
# page surfaces names, emails and phone numbers, so without an explicit boundary
# this pillar is the point at which personal data starts leaving the building.
#
# The existing convention (agents/marketing/segmentation.py) is that an
# underscore-prefixed key is internal and stripped before any prompt. That works
# for a dict assembled by hand; it does not survive a dataclass being serialised
# by three different call sites. So the boundary is named here instead, once,
# and `strip_pii` is the only sanctioned way across it.
#
# The rule: **prompts see customer IDs; humans see names.** Names are joined
# back on at render time in the browser, where the data never leaves the
# tenant's session.

PII_FIELDS = frozenset({"display_name", "contact"})


def strip_pii(payload: dict) -> dict:
    """Remove personally-identifying fields from a record before it reaches an LLM.

    Recurses into nested dicts and lists so a customer record wrapped in a
    report payload is cleaned just as thoroughly as a bare one. Also drops the
    ``_``-prefixed internal keys the rest of the codebase already uses for this
    purpose, so callers need to remember one function rather than two rules.
    """
    if isinstance(payload, dict):
        return {
            key: strip_pii(value)
            for key, value in payload.items()
            if key not in PII_FIELDS and not str(key).startswith("_")
        }
    if isinstance(payload, list):
        return [strip_pii(item) for item in payload]
    return payload


@dataclass(slots=True)
class CustomerRecord:
    """One customer as they stood on one snapshot date.

    Mirrors ``db.crm_models.CustomerState`` field for field. The duplication is
    intentional: the compute layer produces these without importing SQLAlchemy
    or opening a connection, which is what allows the snapshot builder to be
    tested against a DataFrame alone.

    The field grouping carries a contract of its own — **observed** values are
    arithmetic over the source data, **derived** values are deterministic rules
    over the observed ones, and **predicted** values come from a model. A model
    output never overwrites an observed fact.
    """

    customer_id: str
    snapshot_date: date

    # Identity — PII. See the module docstring.
    display_name: str | None = None
    contact: dict = field(default_factory=dict)

    # Observed
    recency_days: int | None = None
    frequency: int | None = None
    monetary: float | None = None
    tenure_days: int | None = None
    avg_order_value: float | None = None
    first_order_date: date | None = None
    last_order_date: date | None = None

    # Derived
    rfm_segment: str | None = None
    r_score: int | None = None
    f_score: int | None = None
    m_score: int | None = None
    lifecycle_stage: str | None = None

    # Predicted
    churn_probability: float | None = None
    risk_tier: str | None = None
    predicted_clv: float | None = None
    clv_horizon_days: int | None = None
    predicted_purchases: float | None = None

    # Prioritisation
    value_at_risk: float | None = None
    value_basis: str | None = None
    drivers: list = field(default_factory=list)

    def with_value_at_risk(self) -> CustomerRecord:
        """Return a copy with ``value_at_risk`` and its basis filled in.

        Prefers predicted CLV over historical spend, because they answer
        different questions: historical spend is what a customer *was* worth,
        forward CLV is what is actually lost if they leave. A four-year account
        that has saturated and a two-year account with rising frequency can have
        identical historical spend and very different futures.

        Until the CLV agent runs, this falls back to historical spend and says
        so in ``value_basis`` rather than silently presenting one as the other.
        """
        if self.churn_probability is None:
            return replace(self, value_at_risk=None, value_basis=None)

        if self.predicted_clv is not None:
            basis, value = VALUE_BASIS_CLV, self.predicted_clv
        elif self.monetary is not None:
            basis, value = VALUE_BASIS_MONETARY, self.monetary
        else:
            return replace(self, value_at_risk=None, value_basis=None)

        return replace(
            self,
            value_at_risk=round(self.churn_probability * float(value), 2),
            value_basis=basis,
        )

    def to_dict(self) -> dict:
        """Full record, including PII. For the browser and the database only."""
        return asdict(self)

    def for_prompt(self) -> dict:
        """LLM-safe projection: identifiers and numbers, no human identities."""
        return strip_pii(asdict(self))


@dataclass(slots=True)
class SnapshotResult:
    """The outcome of one refresh.

    Follows the platform's existing status convention
    (``{"status": "skipped", "reason": ...}``) rather than raising: a CRM
    refresh that cannot run must degrade to "no history yet" on the page, never
    to a 500. The caller decides what to do with a partial result; it is not
    this layer's job to decide that a missing CLV is fatal.
    """

    status: str  # "success" | "partial" | "failed" | "skipped"
    snapshot_id: str | None = None
    snapshot_date: date | None = None
    customers: int = 0
    components: dict = field(default_factory=dict)
    grain: dict = field(default_factory=dict)
    stage_rules: dict = field(default_factory=dict)
    duration_ms: int | None = None
    reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in ("success", "partial")

    def to_dict(self) -> dict:
        return asdict(self)
