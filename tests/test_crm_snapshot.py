"""CRM compute layer — the contracts that keep customer state honest.

These tests are hermetic: ``agents.crm.snapshot`` never touches the database, so
everything here runs against a synthetic DataFrame. The persistence round trip
is covered separately in ``test_crm_repository.py`` behind the ``integration``
marker.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from agents.crm.contracts import (
    VALUE_BASIS_CLV,
    VALUE_BASIS_MONETARY,
    CustomerRecord,
    risk_tier,
    strip_pii,
)
from agents.crm.snapshot import (
    assign_lifecycle_stage,
    build_customer_records,
    derive_stage_rules,
)


def _transactions(seed: int = 7) -> pd.DataFrame:
    """Two cohorts over a year: steady buyers and customers who lapse early."""
    rng = np.random.default_rng(seed)
    base = pd.Timestamp("2024-01-01")
    rows: list[dict] = []
    order = 0
    for i in range(30):  # active right up to the snapshot
        days = sorted(rng.integers(0, 360, size=12))
        for day in [*days, 358]:
            order += 1
            rows.append(
                {
                    "customer_id": f"ACT{i:03d}",
                    "customer_name": f"Active Person {i}",
                    "email": f"active{i}@example.com",
                    "order_id": f"O{order}",
                    "order_date": (base + pd.Timedelta(days=int(day))).strftime("%Y-%m-%d"),
                    "product": f"P{rng.integers(0, 6)}",
                    "quantity": int(rng.integers(1, 4)),
                    "total_price": float(rng.uniform(50, 400)),
                }
            )
    for i in range(30):  # stop after ~100 days
        days = sorted(rng.integers(0, 100, size=6))
        for day in days:
            order += 1
            rows.append(
                {
                    "customer_id": f"LAP{i:03d}",
                    "customer_name": f"Lapsed Person {i}",
                    "email": f"lapsed{i}@example.com",
                    "order_id": f"O{order}",
                    "order_date": (base + pd.Timedelta(days=int(day))).strftime("%Y-%m-%d"),
                    "product": f"P{rng.integers(0, 6)}",
                    "quantity": int(rng.integers(1, 4)),
                    "total_price": float(rng.uniform(50, 400)),
                }
            )
    return pd.DataFrame(rows)


# ── Coverage: the bug that made the old churn persistence useless ───────────

def test_every_customer_gets_a_record_not_just_the_at_risk_top_n():
    """The whole point of P0.

    ``save_churn_run`` only ever stored ``at_risk_customers`` — the displayed
    slice — so a customer page could not be opened for anybody else. State must
    cover the entire book.
    """
    df = _transactions()
    records, _ = build_customer_records(df)
    assert len(records) == df["customer_id"].nunique() == 60


def test_records_are_produced_without_a_churn_model():
    """Observed and derived layers must survive the model being unavailable."""
    records, meta = build_customer_records(_transactions(), churn_payload=None)

    assert records
    assert meta["components"]["churn"]["status"] == "skipped"
    assert all(r.churn_probability is None for r in records)
    # ...but the facts and the segmentation are still there.
    assert all(r.monetary is not None for r in records)
    assert all(r.rfm_segment for r in records)
    assert all(r.lifecycle_stage for r in records)


def test_unavailable_churn_payload_is_recorded_with_its_reason():
    _, meta = build_customer_records(
        _transactions(), churn_payload={"available": False, "reason": "History too short"}
    )
    assert meta["components"]["churn"] == {
        "status": "skipped",
        "reason": "History too short",
    }


def test_clv_is_declared_pending_not_missing():
    """"Not modelled yet" and "modelled as zero" must stay distinguishable."""
    _, meta = build_customer_records(_transactions())
    assert meta["components"]["clv"]["status"] == "pending"


def test_missing_customer_column_degrades_with_a_reason():
    records, meta = build_customer_records(pd.DataFrame({"a": [1, 2], "b": [3, 4]}))
    assert records == []
    assert "reason" in meta


# ── Value at risk and its basis ─────────────────────────────────────────────

def test_value_at_risk_falls_back_to_spend_and_says_so():
    record = CustomerRecord(
        customer_id="C1", snapshot_date=pd.Timestamp("2024-01-01").date(),
        monetary=1000.0, churn_probability=0.4,
    ).with_value_at_risk()

    assert record.value_at_risk == pytest.approx(400.0)
    assert record.value_basis == VALUE_BASIS_MONETARY


def test_value_at_risk_prefers_predicted_clv_when_present():
    """A saturated big spender must not outrank a rising account on history."""
    record = CustomerRecord(
        customer_id="C1", snapshot_date=pd.Timestamp("2024-01-01").date(),
        monetary=50_000.0, predicted_clv=2_000.0, churn_probability=0.5,
    ).with_value_at_risk()

    assert record.value_at_risk == pytest.approx(1_000.0)
    assert record.value_basis == VALUE_BASIS_CLV


def test_value_at_risk_is_null_without_a_churn_probability():
    record = CustomerRecord(
        customer_id="C1", snapshot_date=pd.Timestamp("2024-01-01").date(), monetary=1000.0
    ).with_value_at_risk()

    assert record.value_at_risk is None
    assert record.value_basis is None


def test_ranking_by_value_at_risk_differs_from_ranking_by_risk():
    """The pillar's core claim, asserted rather than asserted-at."""
    scores = {"ACT000": 0.99, "ACT001": 0.10}
    records, _ = build_customer_records(
        _transactions(),
        churn_payload={
            "available": True,
            "_customer_scores": scores,
            "at_risk_customers": [],
        },
    )
    by_id = {r.customer_id: r for r in records}
    cheap_and_doomed = by_id["ACT000"]
    # Give the low-risk customer far more value; VaR must invert the order.
    valuable = by_id["ACT001"]

    assert cheap_and_doomed.churn_probability > valuable.churn_probability
    inverted = CustomerRecord(
        customer_id="X", snapshot_date=valuable.snapshot_date,
        monetary=valuable.monetary * 100, churn_probability=0.10,
    ).with_value_at_risk()
    assert inverted.value_at_risk > (cheap_and_doomed.value_at_risk or 0)


# ── Risk tiering shares the churn engine's thresholds ───────────────────────

def test_risk_tier_matches_the_churn_engine_thresholds():
    assert risk_tier(0.95) == "High"
    assert risk_tier(0.70) == "High"
    assert risk_tier(0.55) == "Medium"
    assert risk_tier(0.40) == "Medium"
    assert risk_tier(0.10) == "Low"
    assert risk_tier(None) is None


# ── Lifecycle staging is data-derived and auditable ─────────────────────────

def test_stage_rules_are_derived_from_the_data_cadence():
    df = _transactions()
    records, meta = build_customer_records(df)
    rules = meta["stage_rules"]

    assert rules["cadence_basis"] == "median_interpurchase_days"
    # The published rule must reproduce itself from the published cadence.
    # Tolerance is half a unit in the last stored place: everything is rounded
    # to one decimal, so exact float equality is not the property under test.
    assert rules["dormant_after_days"] == pytest.approx(rules["cadence_days"] * 1.5, abs=0.05)
    assert rules["churned_after_days"] == pytest.approx(rules["cadence_days"] * 3.0, abs=0.05)
    # The rules must reach the snapshot record, or the staging is unauditable.
    assert set(rules) >= {"cadence_days", "cadence_basis", "dormant_multiple"}


def test_stage_rules_fall_back_honestly_without_repeat_buyers():
    """A default is fine; presenting a default as a measurement is not."""
    features = pd.DataFrame(
        {"frequency": [1, 1, 1], "avg_interpurchase_days": [0.0, 0.0, 0.0],
         "recency_days": [10, 20, 30], "tenure_days": [40, 50, 60]}
    )
    rules = derive_stage_rules(features)
    assert rules["cadence_basis"] == "default_no_repeat_buyers"
    assert rules["cadence_days"] == 60.0


def test_lifecycle_stage_priority_and_momentum():
    rules = {"cadence_days": 40.0, "dormant_after_days": 60.0, "churned_after_days": 120.0,
             "new_within_days": 90, "dormant_multiple": 1.5, "churned_multiple": 3.0}

    # Silence past the churn threshold wins over an excellent history.
    assert assign_lifecycle_stage(
        pd.Series({"recency_days": 200, "tenure_days": 700, "frequency": 40,
                   "orders_last_90": 0}), rules) == "Churned"
    assert assign_lifecycle_stage(
        pd.Series({"recency_days": 80, "tenure_days": 700, "frequency": 40,
                   "orders_last_90": 0}), rules) == "Dormant"
    # A brand-new single-purchase customer is New, not Declining.
    assert assign_lifecycle_stage(
        pd.Series({"recency_days": 5, "tenure_days": 20, "frequency": 1,
                   "orders_last_90": 1}), rules) == "New"
    # Momentum is measured against the customer's own rate.
    growing = pd.Series({"recency_days": 5, "tenure_days": 360, "frequency": 12,
                         "orders_last_90": 6})   # expected 3, got 6
    assert assign_lifecycle_stage(growing, rules) == "Growing"
    declining = pd.Series({"recency_days": 5, "tenure_days": 360, "frequency": 12,
                           "orders_last_90": 1})  # expected 3, got 1
    assert assign_lifecycle_stage(declining, rules) == "Declining"
    steady = pd.Series({"recency_days": 5, "tenure_days": 360, "frequency": 12,
                        "orders_last_90": 3})
    assert assign_lifecycle_stage(steady, rules) == "Established"


def test_lapsed_cohort_is_staged_worse_than_the_active_cohort():
    records, _ = build_customer_records(_transactions())
    stages = {r.customer_id: r.lifecycle_stage for r in records}
    lapsed = [s for cid, s in stages.items() if cid.startswith("LAP")]
    active = [s for cid, s in stages.items() if cid.startswith("ACT")]

    assert all(s in ("Churned", "Dormant") for s in lapsed)
    assert not any(s == "Churned" for s in active)


# ── The PII boundary ────────────────────────────────────────────────────────

def test_for_prompt_removes_identity_fields():
    record = CustomerRecord(
        customer_id="C1", snapshot_date=pd.Timestamp("2024-01-01").date(),
        display_name="Layla Hassan", contact={"email": "layla@example.com"},
        monetary=900.0,
    )
    safe = record.for_prompt()

    assert "display_name" not in safe
    assert "contact" not in safe
    # The identifier and the numbers survive — a prompt still has what it needs.
    assert safe["customer_id"] == "C1"
    assert safe["monetary"] == 900.0


def test_strip_pii_recurses_through_nested_payloads():
    """A record wrapped in a report must be cleaned as thoroughly as a bare one."""
    payload = {
        "customers": [{"customer_id": "C1", "display_name": "Omar", "monetary": 10.0}],
        "meta": {"contact": {"phone": "+20100"}, "_internal": "secret", "kept": 1},
    }
    safe = strip_pii(payload)

    assert safe["customers"][0] == {"customer_id": "C1", "monetary": 10.0}
    assert safe["meta"] == {"kept": 1}


def test_snapshot_captures_identity_but_keeps_it_out_of_prompts():
    records, _ = build_customer_records(_transactions())
    named = [r for r in records if r.display_name]

    assert named, "customer_name column should have been detected"
    assert all("@" in r.contact.get("email", "@") for r in named)
    assert all("display_name" not in r.for_prompt() for r in named)


# ── Provenance: predictions never overwrite measurements ────────────────────

def test_observed_facts_are_measured_not_modelled():
    df = _transactions()
    records, _ = build_customer_records(
        df, churn_payload={"available": True, "_customer_scores": {}, "at_risk_customers": []}
    )
    by_id = {r.customer_id: r for r in records}

    for customer_id, group in df.groupby("customer_id"):
        record = by_id[str(customer_id)]
        assert record.frequency == group["order_id"].nunique()
        assert record.monetary == pytest.approx(round(group["total_price"].sum(), 2))
        assert record.last_order_date == pd.to_datetime(group["order_date"]).max().date()
        assert record.first_order_date == pd.to_datetime(group["order_date"]).min().date()


def test_shap_drivers_attach_only_to_explained_customers():
    records, meta = build_customer_records(
        _transactions(),
        churn_payload={
            "available": True,
            "_customer_scores": {"ACT000": 0.8},
            "at_risk_customers": [
                {"customer": "ACT000",
                 "top_drivers": [{"feature": "recency_days", "shap_value": 0.3,
                                  "direction": "increases_risk"}]}
            ],
        },
    )
    by_id = {r.customer_id: r for r in records}

    assert by_id["ACT000"].drivers
    assert by_id["ACT001"].drivers == []
    assert meta["components"]["churn"]["explained"] == 1
