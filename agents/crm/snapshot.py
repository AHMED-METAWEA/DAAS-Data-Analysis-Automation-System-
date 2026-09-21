"""Builds a complete set of :class:`CustomerRecord`s from a project's data.

Pure computation: takes a DataFrame in, returns records out, touches no
database and opens no connection. That is what makes it testable against a
CSV, and it is the reason the CRM package does not extend
``agents/churn/storage.py``, where DDL, computation and transaction share one
function.

The design rule this module enforces is the *provenance of every number*:

  observed   measured arithmetically from the transactions   (recency, spend)
  derived    a deterministic, printable rule over observed   (RFM, lifecycle)
  predicted  a model output                                  (churn, CLV)

They are computed in that order and a later stage never overwrites an earlier
one. When the churn model is unavailable the observed and derived layers are
still written — a CRM with segments but no risk scores is useful; one that
fails entirely because a model could not fit is not.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from agents.analytics.schema_intel import build_schema_summary
from agents.churn.features import compute_customer_features
from agents.churn.features import parse_time
from agents.marketing.segmentation import compute_rfm

from .contracts import CustomerRecord, risk_tier

# Columns whose name suggests they carry contact details. Matched case- and
# separator-insensitively; used only to populate the PII-isolated `contact`
# field, never to feed a model.
_CONTACT_HINTS = {
    "email": ("email", "e_mail", "mail", "بريد"),
    "phone": ("phone", "mobile", "tel", "whatsapp", "هاتف", "جوال"),
    "city": ("city", "town", "مدينة"),
    "country": ("country", "دولة"),
}


def _find_name_column(df: pd.DataFrame, customer_col: str) -> str | None:
    """The column holding a human-readable customer name, if there is one.

    Mirrors ``agents/churn/engine._find_name_column`` but also accepts a bare
    ``name``/``full_name`` column, which is common once a customers dimension
    table has been joined into the fact view and its columns are no longer
    prefixed with "customer".
    """
    exact = {"name", "full_name", "customer", "client", "الاسم", "اسم_العميل"}
    for col in df.columns:
        if col == customer_col:
            continue
        low = col.lower().replace(" ", "_")
        if low in exact:
            return col
        if "name" in low and any(k in low for k in ("customer", "client", "contact")):
            return col
    return None


def _find_contact_columns(df: pd.DataFrame) -> dict[str, str]:
    found: dict[str, str] = {}
    for kind, hints in _CONTACT_HINTS.items():
        for col in df.columns:
            low = col.lower().replace(" ", "_")
            if any(h in low for h in hints):
                found[kind] = col
                break
    return found


def derive_stage_rules(features: pd.DataFrame) -> dict:
    """Lifecycle thresholds derived from the data's own purchase cadence.

    Hardcoding "dormant after 90 days" is wrong in both directions at once: it
    is far too patient for a daily coffee shop and far too aggressive for an
    annual insurance renewal. So the thresholds are expressed as multiples of
    the population's median inter-purchase interval, which is measured from the
    data itself.

    The multiples (1.5x, 3x) are still a judgement, but a defensible one: a
    customer who has gone half again as long as their peers' typical gap has
    plausibly slipped, and one who has gone three times as long has almost
    certainly stopped. Both the multiples and the resulting day-counts are
    returned so they can be stored with the snapshot and printed in the UI —
    a rule nobody can inspect is indistinguishable from a guess.
    """
    repeat = features[features["frequency"] > 1]
    if len(repeat) >= 10 and "avg_interpurchase_days" in repeat.columns:
        cadence = float(repeat["avg_interpurchase_days"].median())
    else:
        cadence = float("nan")

    # Fall back to a neutral 60-day cadence only when there are too few repeat
    # buyers to measure one, and say so in `basis` rather than pretending the
    # number was derived.
    if not np.isfinite(cadence) or cadence <= 0:
        cadence, basis = 60.0, "default_no_repeat_buyers"
    else:
        basis = "median_interpurchase_days"

    # Round the cadence *before* deriving the thresholds, so the published rule
    # reproduces itself: someone checking "55.0 x 1.5" against a stored 82.4
    # has found a discrepancy in the one artifact whose entire purpose is to be
    # checkable. Derive from the same number the UI prints.
    cadence = round(cadence, 1)

    return {
        "cadence_days": cadence,
        "cadence_basis": basis,
        "dormant_after_days": round(cadence * 1.5, 1),
        "churned_after_days": round(cadence * 3.0, 1),
        "new_within_days": 90,
        "dormant_multiple": 1.5,
        "churned_multiple": 3.0,
    }


def assign_lifecycle_stage(row: pd.Series, rules: dict) -> str:
    """Deterministic lifecycle stage for one customer.

    Evaluated in priority order — a customer who has been silent past the
    churn threshold is Churned regardless of how good their history looks,
    because the history is exactly what makes losing them matter.
    """
    recency = float(row.get("recency_days", 0) or 0)
    tenure = float(row.get("tenure_days", 0) or 0)
    frequency = float(row.get("frequency", 0) or 0)

    if recency > rules["churned_after_days"]:
        return "Churned"
    if recency > rules["dormant_after_days"]:
        return "Dormant"
    # A single-purchase customer inside the new-customer window has not yet had
    # the chance to establish a pattern; calling them "Declining" would be an
    # artifact of them being new, not a finding about them.
    if tenure <= rules["new_within_days"] and frequency <= 1:
        return "New"

    # Momentum: purchases in the last 90 days against the rate implied by their
    # own whole history. Above 1.2 the customer is accelerating, below 0.6 they
    # are decaying — measured against themselves, so a twice-a-year buyer is not
    # penalised for not behaving like a weekly one.
    recent = float(row.get("orders_last_90", 0) or 0)
    expected = frequency * (90.0 / tenure) if tenure > 0 else 0.0
    if expected > 0:
        momentum = recent / expected
        if momentum >= 1.2:
            return "Growing"
        if momentum <= 0.6:
            return "Declining"
    return "Established"


def build_customer_records(
    df: pd.DataFrame,
    *,
    churn_payload: dict | None = None,
) -> tuple[list[CustomerRecord], dict]:
    """Compute the full customer state for a project's data.

    Parameters
    ----------
    df
        The project's customer-360 view (transaction grain).
    churn_payload
        An existing ``run_churn_analysis`` result to fold in. Optional: when it
        is absent or unavailable, records are still produced with the observed
        and derived layers populated and ``churn_probability`` left ``None``.

    Returns
    -------
    (records, meta)
        ``meta`` carries the resolved grain, the stage rules, per-component
        status and the source-data span — everything needed to write a
        :class:`~db.crm_models.CrmSnapshot` row that explains itself.
    """
    schema = build_schema_summary(df)
    customer_col = schema["customer_column"]
    time_col = schema["time_column"]

    meta: dict = {
        "grain": {
            "customer_column": customer_col,
            "time_column": time_col,
            "order_column": schema.get("order_column"),
            "monetary_column": (schema["monetary_columns"] or [None])[0],
        },
        "components": {},
        "row_count": int(len(df)),
        "history_days": 0,
        "stage_rules": {},
    }

    if not customer_col:
        meta["reason"] = "No customer identifier column detected."
        return [], meta
    if not time_col:
        meta["reason"] = "No date column detected (needed for recency and tenure)."
        return [], meta

    tx = parse_time(df, time_col)
    if tx.empty:
        meta["reason"] = f"Date column '{time_col}' has no parseable dates."
        return [], meta

    snapshot_ts = tx["_dt"].max()
    meta["history_days"] = int((snapshot_ts - tx["_dt"].min()).days)
    snapshot_date = snapshot_ts.date()

    # ── Observed ────────────────────────────────────────────────────────────
    # Reuses the churn agent's feature builder rather than reimplementing RFM
    # arithmetic. Cutoff is the snapshot itself: this describes the customer as
    # they stand *now*, not as they stood before a held-out window.
    features = compute_customer_features(tx, snapshot_ts, schema)
    if features.empty:
        meta["reason"] = "No customers could be featurised from this data."
        return [], meta

    first_order = tx.groupby(customer_col)["_dt"].min()
    last_order = tx.groupby(customer_col)["_dt"].max()

    # ── Derived: RFM ────────────────────────────────────────────────────────
    rfm_by_customer: dict[str, dict] = {}
    rfm = compute_rfm(df, snapshot_date=snapshot_ts)
    if rfm.get("available"):
        rfm_by_customer = rfm.get("_rfm_by_customer", {})
        meta["components"]["rfm"] = {"status": "ok", "customers": len(rfm_by_customer)}
    else:
        meta["components"]["rfm"] = {"status": "skipped", "reason": rfm.get("reason")}

    # ── Derived: lifecycle ──────────────────────────────────────────────────
    stage_rules = derive_stage_rules(features)
    meta["stage_rules"] = stage_rules

    # ── Predicted: churn ────────────────────────────────────────────────────
    churn_scores: dict[str, float] = {}
    drivers_by_customer: dict[str, list] = {}
    if churn_payload and churn_payload.get("available"):
        raw_scores = churn_payload.get("_customer_scores") or churn_payload.get(
            "customer_scores"
        ) or {}
        churn_scores = {str(k): float(v) for k, v in raw_scores.items()}
        # SHAP is computed only for the customers the churn page displays, so
        # most customers legitimately have no drivers. That is a coverage fact
        # worth recording, not an error: the Customer 360 page needs to know
        # whether to offer an explanation or an "explain this customer" button.
        for entry in churn_payload.get("at_risk_customers", []) or []:
            if entry.get("top_drivers"):
                drivers_by_customer[str(entry["customer"])] = entry["top_drivers"]
        meta["components"]["churn"] = {
            "status": "ok",
            "customers": len(churn_scores),
            "explained": len(drivers_by_customer),
            "model": (churn_payload.get("model") or {}).get("name"),
        }
    else:
        reason = (churn_payload or {}).get("reason") or "churn analysis not run"
        meta["components"]["churn"] = {"status": "skipped", "reason": reason}

    # CLV is Stage 1. Declared here as explicitly pending so the API and the UI
    # can distinguish "not modelled yet" from "modelled as zero".
    meta["components"]["clv"] = {"status": "pending", "reason": "CLV agent not yet built"}

    # ── Identity (PII) ──────────────────────────────────────────────────────
    name_col = _find_name_column(df, customer_col)
    name_map: dict = {}
    if name_col:
        name_map = df.dropna(subset=[name_col]).groupby(customer_col)[name_col].first().to_dict()

    contact_cols = _find_contact_columns(df)
    contact_map: dict[str, dict] = {}
    if contact_cols:
        for kind, col in contact_cols.items():
            values = df.dropna(subset=[col]).groupby(customer_col)[col].first()
            for cid, value in values.items():
                contact_map.setdefault(str(cid), {})[kind] = str(value)

    # ── Assemble ────────────────────────────────────────────────────────────
    records: list[CustomerRecord] = []
    for customer_id, row in features.iterrows():
        key = str(customer_id)
        rfm_entry = rfm_by_customer.get(key, {})
        probability = churn_scores.get(key)

        monetary = row.get("monetary")
        record = CustomerRecord(
            customer_id=key,
            snapshot_date=snapshot_date,
            display_name=str(name_map[customer_id]) if customer_id in name_map else None,
            contact=contact_map.get(key, {}),
            recency_days=int(row.get("recency_days", 0)),
            frequency=int(row.get("frequency", 0)),
            monetary=round(float(monetary), 2) if monetary is not None else None,
            tenure_days=int(row.get("tenure_days", 0)),
            avg_order_value=(
                round(float(row["avg_order_value"]), 2)
                if "avg_order_value" in row and pd.notna(row["avg_order_value"])
                else None
            ),
            first_order_date=(
                first_order[customer_id].date() if customer_id in first_order.index else None
            ),
            last_order_date=(
                last_order[customer_id].date() if customer_id in last_order.index else None
            ),
            rfm_segment=rfm_entry.get("segment"),
            r_score=rfm_entry.get("r"),
            f_score=rfm_entry.get("f"),
            m_score=rfm_entry.get("m"),
            lifecycle_stage=assign_lifecycle_stage(row, stage_rules),
            churn_probability=round(probability, 4) if probability is not None else None,
            risk_tier=risk_tier(probability),
            drivers=drivers_by_customer.get(key, []),
        )
        records.append(record.with_value_at_risk())

    return records, meta
