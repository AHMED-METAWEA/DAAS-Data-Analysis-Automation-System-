from __future__ import annotations

import numpy as np
import pandas as pd

from agents.analytics.schema_intel import build_schema_summary
from agents.churn.engine import run_churn_analysis
from agents.churn.features import compute_customer_features, parse_time
from agents.churn.model import _label_churn, explain_predictions, heuristic_predict


def _build_tx(seed: int = 0) -> pd.DataFrame:
    """Synthetic transactions with a clear churn signal.

    'Active' customers keep buying right up to the snapshot; 'lapsed' customers
    stop after the first ~120 days. Over a 200-day span with a 30-day horizon,
    recency cleanly separates the two groups.
    """
    rng = np.random.default_rng(seed)
    base = pd.Timestamp("2024-01-01")
    rows = []
    oid = 0
    for i in range(45):  # active → not churned
        days = sorted(rng.integers(0, 201, size=int(rng.integers(6, 12))))
        days = list(days) + [int(rng.integers(175, 201))]  # ensure a recent buy
        for d in days:
            oid += 1
            rows.append(_row(f"A{i}", base + pd.Timedelta(days=int(d)), oid, rng))
    for i in range(45):  # lapsed → churned
        days = sorted(rng.integers(0, 121, size=int(rng.integers(5, 10))))
        for d in days:
            oid += 1
            rows.append(_row(f"L{i}", base + pd.Timedelta(days=int(d)), oid, rng))
    return pd.DataFrame(rows)


def _row(cust: str, dt: pd.Timestamp, oid: int, rng) -> dict:
    cats = ["Coffee", "Equipment", "Gifts"]
    return {
        "customer_id": cust,
        "order_id": f"ORD-{oid}",
        "order_date": dt.strftime("%Y-%m-%d"),
        "product": f"P{int(rng.integers(0, 8))}",
        "category": cats[int(rng.integers(0, len(cats)))],
        "quantity": int(rng.integers(1, 5)),
        "total_price": round(float(rng.uniform(10, 120)), 2),
    }


# ── Feature engineering ──────────────────────────────────────────────────────


def test_compute_features_basic() -> None:
    df = pd.DataFrame({
        "customer_id": ["C1", "C1", "C2"],
        "order_id": ["O1", "O2", "O3"],
        "order_date": ["2024-01-01", "2024-01-11", "2024-01-05"],
        "total_price": [100.0, 50.0, 30.0],
    })
    schema = build_schema_summary(df)
    tx = parse_time(df, schema["time_column"])
    cutoff = pd.Timestamp("2024-01-21")
    feats = compute_customer_features(tx, cutoff, schema)

    assert feats.loc["C1", "frequency"] == 2          # two distinct orders
    assert feats.loc["C1", "recency_days"] == 10       # last buy 2024-01-11
    assert feats.loc["C1", "monetary"] == 150.0
    assert feats.loc["C2", "frequency"] == 1
    assert not feats.isna().any().any()


def test_label_churn_window() -> None:
    df = pd.DataFrame({
        "customer_id": ["stay", "stay", "gone"],
        "order_id": ["1", "2", "3"],
        "order_date": ["2024-01-01", "2024-03-01", "2024-01-02"],
        "total_price": [20.0, 20.0, 20.0],
    })
    schema = build_schema_summary(df)
    tx = parse_time(df, schema["time_column"])
    snapshot = tx["_dt"].max()                 # 2024-03-01
    cutoff = snapshot - pd.Timedelta(days=30)  # 2024-01-31
    feats = _label_churn(tx, cutoff, snapshot, schema)
    # 'gone' only bought before the cutoff → churned; 'stay' bought after → not.
    assert feats.loc["gone", "churned"] == 1
    assert feats.loc["stay", "churned"] == 0


# ── End-to-end engine ────────────────────────────────────────────────────────


def test_run_churn_analysis_trains_model() -> None:
    df = _build_tx(seed=1)
    payload = run_churn_analysis(data_df=df, horizon_days=30, top_n=50)

    assert payload["available"] is True
    assert payload["model"]["name"] == "HistGradientBoosting"
    # Recency is a clean signal here, so the model should be clearly skilful.
    assert payload["model"]["auc"] is not None and payload["model"]["auc"] > 0.75
    # Production pipeline: multiple historical cutoffs, out-of-time validation.
    assert payload["model"]["n_cutoffs"] >= 2
    assert payload["model"]["validation"] == "out_of_time"
    assert payload["training_cutoffs"]

    rd = payload["risk_distribution"]
    assert rd["high"] + rd["medium"] + rd["low"] == payload["customers_scored"]
    assert payload["revenue_at_risk"] is not None
    assert len(payload["at_risk_customers"]) <= 50

    # Lapsed customers should dominate the top of the at-risk ranking.
    top10 = [c["customer"] for c in payload["at_risk_customers"][:10]]
    assert sum(1 for c in top10 if c.startswith("L")) >= 7
    # The list is now ranked by expected value at risk (prob × spend), descending.
    assert payload["at_risk_ranked_by"] == "expected_value_at_risk"
    losses = [c["expected_loss"] for c in payload["at_risk_customers"]]
    assert losses == sorted(losses, reverse=True)
    assert all("expected_loss" in c and "monetary" in c for c in payload["at_risk_customers"])


def test_run_churn_analysis_requires_customer_and_date() -> None:
    no_cust = pd.DataFrame({"order_date": ["2024-01-01"], "total_price": [10.0]})
    assert run_churn_analysis(data_df=no_cust)["available"] is False

    no_date = pd.DataFrame({"customer_id": ["C1"], "total_price": [10.0]})
    assert run_churn_analysis(data_df=no_date)["available"] is False


def test_run_churn_analysis_short_history() -> None:
    df = pd.DataFrame({
        "customer_id": ["C1", "C2"],
        "order_date": ["2024-01-01", "2024-01-03"],
        "total_price": [10.0, 20.0],
    })
    out = run_churn_analysis(data_df=df, horizon_days=90)
    assert out["available"] is False


def test_payload_exposes_full_customer_scores() -> None:
    """Marketing needs per-customer probabilities, not just the top-N table."""
    df = _build_tx(seed=2)
    payload = run_churn_analysis(data_df=df, horizon_days=30, top_n=10)
    scores = payload["_customer_scores"]
    assert len(scores) == payload["customers_scored"]
    assert all(0.0 <= p <= 1.0 for p in scores.values())
    # Top-N trimming must not affect the full score map.
    assert len(payload["at_risk_customers"]) <= 10 < len(scores)


def test_recent_activity_features_present() -> None:
    df = _build_tx(seed=3)
    schema = build_schema_summary(df)
    tx = parse_time(df, schema["time_column"])
    feats = compute_customer_features(tx, tx["_dt"].max(), schema)
    for col in ("orders_last_30", "orders_last_60", "orders_last_90",
                "spend_last_90", "spend_trend"):
        assert col in feats.columns, col
    assert not feats.isna().any().any()
    # Active customers bought recently → non-zero recent orders somewhere.
    assert feats["orders_last_90"].sum() > 0


# ── SHAP explainability ──────────────────────────────────────────────────────


def test_shap_top_drivers_and_global_importance() -> None:
    df = _build_tx(seed=1)
    payload = run_churn_analysis(data_df=df, horizon_days=30, top_n=50)

    assert payload["model"]["name"] == "HistGradientBoosting"
    assert payload["shap_global_importance"], "SHAP importance should populate for a trained model"
    # Same underlying feature set as permutation importance, different technique.
    shap_features = {d["feature"] for d in payload["shap_global_importance"]}
    perm_features = {d["feature"] for d in payload["feature_importance"]}
    assert shap_features == perm_features

    for c in payload["at_risk_customers"]:
        assert "top_drivers" in c and len(c["top_drivers"]) <= 3
        for d in c["top_drivers"]:
            assert d["feature"] in perm_features
            assert d["direction"] in ("increases_risk", "decreases_risk")
            # Sign of the SHAP value must agree with the stated direction.
            assert (d["shap_value"] > 0) == (d["direction"] == "increases_risk")

    # Lapsed (L*) customers dominate the top of the ranking (see the ranking
    # test above); recency should show up as a driver pushing THEIR risk up —
    # ties the explanation back to the actual synthetic churn signal.
    top_lapsed = next(c for c in payload["at_risk_customers"] if c["customer"].startswith("L"))
    recency_driver = next(
        (d for d in top_lapsed["top_drivers"] if d["feature"] == "recency_days"), None
    )
    assert recency_driver is not None
    assert recency_driver["direction"] == "increases_risk"


def test_explain_predictions_handles_no_model() -> None:
    """engine.py calls this unconditionally; a None model (heuristic fallback)
    must degrade to an empty dict, never raise."""
    assert explain_predictions(None, np.zeros((3, 2)), ["a", "b"], ["c1", "c2", "c3"]) == {}


def test_heuristic_predict_has_no_shap() -> None:
    """The recency-heuristic fallback has no trained model to explain — its
    result dict must expose empty/None SHAP fields, not omit or crash on them."""
    df = _build_tx(seed=5)
    schema = build_schema_summary(df)
    tx = parse_time(df, schema["time_column"])
    res = heuristic_predict(tx, tx["_dt"].max(), horizon=30, schema=schema)
    assert res["shap_importance"] == []
    assert res["_model"] is None


def test_small_dataset_falls_back_gracefully() -> None:
    """~50 customers over a short span must still produce a usable payload."""
    rng = np.random.default_rng(7)
    base = pd.Timestamp("2024-01-01")
    rows = []
    for i in range(50):
        for d in sorted(rng.integers(0, 60, size=3)):
            rows.append({
                "customer_id": f"C{i}",
                "order_date": (base + pd.Timedelta(days=int(d))).strftime("%Y-%m-%d"),
                "total_price": float(rng.uniform(5, 50)),
            })
    payload = run_churn_analysis(data_df=pd.DataFrame(rows), horizon_days=90)
    assert payload["available"] is True
    assert payload["model"]["name"] in ("HistGradientBoosting", "Recency heuristic (fallback)")
