"""Churn ↔ Marketing integration: the marketing payload must carry the churn
model's per-customer risk so campaigns can target model-scored customers."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from agents.churn.engine import HIGH_RISK, run_churn_analysis
from agents.marketing.agent import slim_for_prompt
from agents.marketing.engine import build_churn_section, run_marketing_analytics
from agents.marketing.segmentation import compute_rfm


def _tx(seed: int = 0) -> pd.DataFrame:
    """Active + lapsed customers over ~200 days (clear churn signal)."""
    rng = np.random.default_rng(seed)
    base = pd.Timestamp("2024-01-01")
    rows, oid = [], 0
    for i in range(40):        # active
        days = list(rng.integers(0, 201, size=8)) + [200]
        for d in days:
            oid += 1
            rows.append({
                "customer_id": f"A{i}", "order_id": f"O{oid}",
                "order_date": (base + pd.Timedelta(days=int(d))).strftime("%Y-%m-%d"),
                "total_price": round(float(rng.uniform(10, 120)), 2),
            })
    for i in range(40):        # lapsed
        for d in rng.integers(0, 100, size=5):
            oid += 1
            rows.append({
                "customer_id": f"L{i}", "order_id": f"O{oid}",
                "order_date": (base + pd.Timedelta(days=int(d))).strftime("%Y-%m-%d"),
                "total_price": round(float(rng.uniform(10, 120)), 2),
            })
    return pd.DataFrame(rows)


def test_marketing_payload_gains_churn_section() -> None:
    df = _tx()
    churn = run_churn_analysis(data_df=df, horizon_days=30)
    assert churn["available"]

    payload = run_marketing_analytics(data_df=df, churn_payload=churn)
    section = payload["churn"]

    assert section["available"] is True
    assert section["risk_distribution"] == churn["risk_distribution"]
    assert section["model_name"] == churn["model"]["name"]
    # Per-RFM-segment churn risk was computed from the model scores.
    by_seg = section["churn_risk_by_rfm_segment"]
    assert by_seg, "expected per-segment churn aggregation"
    for seg, v in by_seg.items():
        assert 0.0 <= v["avg_churn_probability"] <= 1.0
        assert v["high_risk_count"] <= v["customers_scored"]
    # Model KPIs surfaced next to the RFM proxies.
    assert "model_churn_high_risk_pct" in payload["marketing_kpis"]


def test_target_lists_match_model_scores() -> None:
    df = _tx(seed=1)
    churn = run_churn_analysis(data_df=df, horizon_days=30)
    payload = run_marketing_analytics(data_df=df, churn_payload=churn)

    targets = payload["churn"]["_target_lists"]
    scores = churn["_customer_scores"]
    assert targets["high_risk"] == sorted(
        [c for c, p in scores.items() if p >= HIGH_RISK],
        key=lambda c: -scores[c],
    )
    # Lapsed customers should dominate the high-risk audience.
    lapsed_share = sum(1 for c in targets["high_risk"] if c.startswith("L"))
    assert lapsed_share >= len(targets["high_risk"]) * 0.6


def test_no_churn_payload_degrades_gracefully() -> None:
    payload = run_marketing_analytics(data_df=_tx(seed=2), churn_payload=None)
    assert payload["churn"]["available"] is False
    assert "reason" in payload["churn"]


def test_slim_for_prompt_strips_internal_keys() -> None:
    df = _tx(seed=3)
    churn = run_churn_analysis(data_df=df, horizon_days=30)
    payload = run_marketing_analytics(data_df=df, churn_payload=churn)

    slim = slim_for_prompt(payload)
    dumped = json.dumps(slim, default=str)
    assert "_target_lists" not in dumped
    assert "_segment_by_customer" not in dumped
    assert "_customer_scores" not in dumped
    # But the prompt-facing churn numbers survive.
    assert slim["churn"]["risk_distribution"] == churn["risk_distribution"]


def test_churn_section_direct_with_rfm() -> None:
    df = _tx(seed=4)
    churn = run_churn_analysis(data_df=df, horizon_days=30)
    rfm = compute_rfm(df)
    section = build_churn_section(churn, rfm)
    total_scored = sum(
        v["customers_scored"] for v in section["churn_risk_by_rfm_segment"].values()
    )
    # Every scored customer that has an RFM segment is aggregated exactly once.
    assert total_scored == churn["customers_scored"]
