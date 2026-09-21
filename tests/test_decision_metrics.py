"""Arithmetic guarantees of the decision-metrics layer.

Every expectation here is hand-computable from the fixture, so a regression in
the maths fails the test rather than quietly changing what a business owner is
told. The identities (the revenue bridge closing, the Pareto count, the margin
identity check) are the load-bearing ones — a report is only trustworthy if the
parts reconcile with the whole.
"""

from __future__ import annotations

import pandas as pd
import pytest

from agents.insights.decision_metrics import compute_decision_metrics


def _two_month_df() -> pd.DataFrame:
    """April: 10 orders x 100.  May: 6 orders x 50.

    Revenue 1,000 -> 300. Orders 10 -> 6. AOV 100 -> 50.
    """
    rows = []
    oid = 0
    for day in range(10):
        oid += 1
        rows.append({
            "order_id": f"A{oid}", "customer_id": f"C{oid % 5}",
            "order_date": f"2024-04-{day + 1:02d}", "product": "Widget",
            "quantity": 1, "total_price": 100.0, "cost": 60.0, "profit": 40.0,
        })
    for day in range(6):
        oid += 1
        rows.append({
            "order_id": f"M{oid}", "customer_id": f"C{oid % 5}",
            "order_date": f"2024-05-{day + 1:02d}", "product": "Widget",
            "quantity": 1, "total_price": 50.0, "cost": 30.0, "profit": 20.0,
        })
    # A row on the last day of May so the month counts as complete.
    rows.append({
        "order_id": "M999", "customer_id": "C1", "order_date": "2024-05-31",
        "product": "Gadget", "quantity": 1, "total_price": 50.0,
        "cost": 30.0, "profit": 20.0,
    })
    return pd.DataFrame(rows)


def test_period_windows_are_exact() -> None:
    d = compute_decision_metrics(_two_month_df())
    month = d["sections"]["comparison"]["last_month"]

    assert month["comparable"] is True
    assert month["current"]["label"] == "May 2024"
    assert month["current"]["revenue"] == 350.0   # 6 x 50 + 1 x 50
    assert month["current"]["orders"] == 7
    assert month["prior"]["label"] == "April 2024"
    assert month["prior"]["revenue"] == 1000.0
    assert month["prior"]["orders"] == 10
    assert month["prior"]["aov"] == 100.0


def test_revenue_bridge_closes_exactly() -> None:
    """volume + basket + interaction must sum to the revenue change.

    This is the identity the whole "what caused it" section rests on: if it does
    not close, the report attributes a change to causes that do not add up.
    """
    d = compute_decision_metrics(_two_month_df())
    bridge = d["sections"]["comparison"]["last_month"]["bridge"]

    assert bridge["available"] is True
    total = bridge["volume_effect"] + bridge["basket_effect"] + bridge["interaction_effect"]
    assert total == pytest.approx(bridge["revenue_change"], abs=0.01)
    assert bridge["residual"] == pytest.approx(0.0, abs=0.01)
    assert bridge["revenue_change"] == pytest.approx(-650.0, abs=0.01)


def test_bridge_names_the_dominant_driver() -> None:
    d = compute_decision_metrics(_two_month_df())
    bridge = d["sections"]["comparison"]["last_month"]["bridge"]
    # AOV halved (100 -> 50) on 10 prior orders = -500 basket effect, versus
    # -300 from losing 3 orders: order size is the bigger cause.
    assert bridge["basket_effect"] == pytest.approx(-500.0, abs=0.01)
    assert bridge["primary_driver"] == "basket size"


def test_incomplete_prior_period_is_not_reported_as_comparable() -> None:
    """A partially covered window must never be compared like-for-like — that is
    how a data cut-off gets published as a collapse in sales."""
    df = _two_month_df()
    df = df[df["order_date"] >= "2024-04-20"]  # April now only partly covered
    d = compute_decision_metrics(df)
    month = d["sections"]["comparison"]["last_month"]
    assert month["comparable"] is False
    assert month["reason"]


def test_pareto_concentration_counts_are_exact() -> None:
    rows = [
        {"order_id": f"O{i}", "order_date": "2024-03-01", "product": name,
         "total_price": rev, "customer_id": f"C{i}"}
        for i, (name, rev) in enumerate([
            ("A", 700.0), ("B", 200.0), ("C", 50.0), ("D", 30.0), ("E", 20.0),
        ])
    ]
    conc = compute_decision_metrics(pd.DataFrame(rows))["sections"]["concentration"]["products"]

    assert conc["total_count"] == 5
    assert conc["top1_name"] == "A"
    assert conc["top1_share"] == 70.0
    # 700 -> 70%, +200 -> 90%: two products clear the 80% line.
    assert conc["count_for_80pct"] == 2


def test_margin_uses_profit_column_only_when_it_reconciles() -> None:
    d = compute_decision_metrics(_two_month_df())
    margin = d["sections"]["margin"]
    assert margin["available"] is True
    assert margin["checks"]["profit_equals_revenue_minus_cost_share"] == 1.0
    assert margin["gross_profit"] == pytest.approx(540.0, abs=0.01)  # 10*40 + 7*20
    assert margin["gross_margin_pct"] == pytest.approx(40.0, abs=0.1)


def test_margin_is_withheld_when_the_identity_fails() -> None:
    """A cost column that does not reconcile must produce no margin at all —
    publishing a plausible wrong margin is worse than publishing none."""
    df = _two_month_df()
    df["cost"] = df["total_price"] * 5      # cost far above revenue
    df["profit"] = 12345.0                  # unrelated to revenue - cost
    margin = compute_decision_metrics(df)["sections"]["margin"]
    assert margin["available"] is False
    assert "reconcile" in margin["reason"]


def test_discount_is_withheld_when_pricing_identity_fails() -> None:
    df = _two_month_df()
    df["unit_price"] = 100.0
    df["discount_pct"] = 50.0  # implies revenue of 50, but rows say 100 in April
    disc = compute_decision_metrics(df)["sections"]["discount"]
    assert disc["available"] is False


def test_scenarios_carry_a_stated_assumption() -> None:
    d = compute_decision_metrics(_two_month_df())
    assert d["scenarios"], "expected at least one sized opportunity"
    for scenario in d["scenarios"]:
        assert scenario["assumption"], f"{scenario['key']} has no stated assumption"
        assert scenario["formula"]
        assert isinstance(scenario["value"], float)


def test_blind_spots_name_what_is_missing() -> None:
    d = compute_decision_metrics(_two_month_df())
    text = " ".join(d["blind_spots"]).lower()
    assert "conversion" in text      # no traffic column
    assert "return" in text          # no returns column
    assert "currency" in text        # no currency column


def test_degrades_without_a_money_column() -> None:
    df = pd.DataFrame({"note": ["a", "b"], "who": ["x", "y"]})
    d = compute_decision_metrics(df)
    assert d["available"] is False
    assert d["reason"]
