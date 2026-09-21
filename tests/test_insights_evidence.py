"""Ranking of findings — the editorial judgement that happens in code.

The model receives an already-ordered brief, so what the report leads with is
decided here. These tests lock in that a threat outranks a strength, that money
at stake breaks ties, and that the brief never contradicts itself.
"""

from __future__ import annotations

import pandas as pd

from agents.analytics.engine import run_analytics
from agents.insights.decision_metrics import compute_decision_metrics
from agents.insights.evidence import build_evidence, evidence_payload, render_evidence
from agents.insights.figures import TOKEN_RE, build_registry
from agents.insights.strict_verify import mask_non_claims


def _declining_df() -> pd.DataFrame:
    """Strong April, weak May — a real, material month-on-month decline."""
    rows = []
    oid = 0
    for month, days, price in (("04", 30, 100.0), ("05", 31, 40.0)):
        for day in range(1, days + 1):
            oid += 1
            rows.append({
                "order_id": f"O{oid}", "customer_id": f"C{oid % 12}",
                "order_date": f"2024-{month}-{day:02d}",
                "product": "Widget" if oid % 2 else "Gadget",
                "quantity": 1, "total_price": price,
            })
    return pd.DataFrame(rows)


def _context(df: pd.DataFrame):
    payload = run_analytics(df)
    decision = compute_decision_metrics(df)
    registry = build_registry(payload, decision)
    return payload, decision, registry


def test_threats_outrank_strengths() -> None:
    payload, decision, registry = _context(_declining_df())
    items = build_evidence(payload, decision, registry)

    assert items, "expected findings"
    priority = {"THREAT": 0, "LEAK": 1, "RISK": 2, "OPPORTUNITY": 3, "STRENGTH": 4, "CAVEAT": 5}
    ranks = [priority[i.tag] for i in items]
    assert ranks == sorted(ranks), "findings must be ordered by severity class"
    assert items[0].tag == "THREAT"


def test_leading_finding_decomposes_the_change() -> None:
    """'Revenue fell' is not a finding; 'revenue fell because order count fell'
    is, because the two possible causes demand different actions."""
    payload, decision, registry = _context(_declining_df())
    lead = build_evidence(payload, decision, registry)[0].text
    assert "order" in lead.lower()
    assert "{{mon_volume_effect}}" in lead or "{{mon_basket_effect}}" in lead
    assert "USE THIS COMPARISON WINDOW" in lead, "the brief must fix one window for the report"


def test_every_evidence_token_exists_in_the_registry() -> None:
    """A dangling token in the brief becomes a dangling citation in the report."""
    payload, decision, registry = _context(_declining_df())
    for item in build_evidence(payload, decision, registry):
        for match in TOKEN_RE.finditer(item.text):
            assert match.group(1) in registry, f"{item.tag} cites unknown {match.group(1)}"


def test_evidence_carries_no_bare_digits() -> None:
    """The brief is copied from, so a literal number in it becomes a typed
    number in the report — the one thing the citation contract forbids.

    Period labels ("May 2024") are exempt: the verifier masks dates, and naming
    the window is exactly what stops two comparisons being confused.
    """
    payload, decision, registry = _context(_declining_df())
    stripped = TOKEN_RE.sub(" ", render_evidence(build_evidence(payload, decision, registry)))
    stripped = mask_non_claims(stripped)
    # Drop the list numbering the renderer adds ("1. ", "2. ").
    body = "\n".join(line.split(". ", 1)[-1] for line in stripped.splitlines())
    assert not any(ch.isdigit() for ch in body), f"literal digits in the brief: {body}"


def test_stable_revenue_does_not_produce_a_threat() -> None:
    """Product churn under a flat total is a watch item, not a crisis — calling
    it a THREAT next to 'revenue is stable' reads as a contradiction."""
    rows = []
    oid = 0
    for month, days in (("04", 30), ("05", 31)):
        for day in range(1, days + 1):
            oid += 1
            rows.append({
                "order_id": f"O{oid}", "customer_id": f"C{oid % 12}",
                "order_date": f"2024-{month}-{day:02d}",
                "product": "Widget" if oid % 2 else "Gadget",
                "quantity": 1, "total_price": 100.0,
            })
    payload, decision, registry = _context(pd.DataFrame(rows))
    items = build_evidence(payload, decision, registry)
    assert not any(i.tag == "THREAT" for i in items)


def test_evidence_payload_is_serialisable_and_ranked() -> None:
    payload, decision, registry = _context(_declining_df())
    rows = evidence_payload(build_evidence(payload, decision, registry))
    assert [r["rank"] for r in rows] == list(range(1, len(rows) + 1))
    for row in rows:
        assert isinstance(row["money_at_stake"], float)
        assert row["tag"] and row["text"]
