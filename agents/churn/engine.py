"""
Churn Analytics Engine — main orchestrator (no LLM here).

Validates the data, trains/scoring the churn model (or heuristic fallback), and
assembles a structured payload consumable by the UI and the LLM retention-plan
agent:

    {available, horizon_days, snapshot_date, cutoff_date, model, feature_importance,
     risk_distribution, customers_scored, revenue_at_risk, expected_revenue_at_risk,
     at_risk_customers, metadata}
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd

from agents.analytics.schema_intel import build_schema_summary
from tools.db_tools import load_df_from_pg

from .features import parse_time
from .model import explain_predictions, heuristic_predict, train_and_predict

HIGH_RISK = 0.70
MED_RISK = 0.40


def _risk_tier(p: float) -> str:
    if p >= HIGH_RISK:
        return "High"
    if p >= MED_RISK:
        return "Medium"
    return "Low"


def _find_name_column(df: pd.DataFrame, customer_col: str) -> str | None:
    for col in df.columns:
        if col == customer_col:
            continue
        low = col.lower()
        if "name" in low and ("customer" in low or "client" in low or "contact" in low):
            return col
    return None


def run_churn_analysis(
    data_df: pd.DataFrame | None = None,
    table_name: str = "",
    horizon_days: int = 90,
    top_n: int = 200,
) -> dict:
    """Execute the churn pipeline. Returns the structured payload (see module doc)."""
    df = data_df if data_df is not None else load_df_from_pg(table_name)

    schema = build_schema_summary(df)
    cust = schema["customer_column"]
    time_col = schema["time_column"]

    if not cust:
        return {"available": False, "reason": "No customer identifier column detected."}
    if not time_col:
        return {"available": False,
                "reason": "No date column detected (needed to measure recency and churn)."}

    tx = parse_time(df, time_col)
    if tx.empty:
        return {"available": False, "reason": f"Date column '{time_col}' has no parseable dates."}

    snapshot = tx["_dt"].max()
    span = int((snapshot - tx["_dt"].min()).days)

    # Keep the horizon feasible: need a pre-window for features and an
    # outcome-window for labels within the available history.
    horizon = min(horizon_days, max(7, span // 3))
    if span < horizon + 14:
        return {"available": False,
                "reason": f"History too short ({span} days) for a {horizon_days}-day churn window."}

    res = train_and_predict(tx, snapshot, horizon, schema)
    if res is None:
        res = heuristic_predict(tx, snapshot, horizon, schema)

    scored = res["scored"]
    p = scored["churn_probability"].to_numpy(dtype=float)

    risk_distribution = {
        "high": int((p >= HIGH_RISK).sum()),
        "medium": int(((p >= MED_RISK) & (p < HIGH_RISK)).sum()),
        "low": int((p < MED_RISK).sum()),
    }

    has_money = "monetary" in scored.columns
    if has_money:
        money = scored["monetary"].to_numpy(dtype=float)
        revenue_at_risk = float(np.round(money[p >= 0.5].sum(), 2))
        expected_revenue_at_risk = float(np.round((p * money).sum(), 2))
    else:
        revenue_at_risk = None
        expected_revenue_at_risk = None

    # ── Top at-risk customers (with optional human-readable name) ────────────
    name_col = _find_name_column(df, cust)
    name_map = {}
    if name_col:
        name_map = df.dropna(subset=[name_col]).groupby(cust)[name_col].first().to_dict()

    # Rank by *expected value at risk* (churn probability × historical spend) when
    # spend is available — a senior analyst targets high-value at-risk customers,
    # not just the highest-probability ones (a certain-to-churn $10 buyer matters
    # far less than a likely-to-churn $2,000 one). Falls back to probability.
    scored_ranked = scored.copy()
    if has_money:
        scored_ranked["_expected_loss"] = (
            scored_ranked["churn_probability"] * scored_ranked["monetary"].astype(float)
        )
        ranked = scored_ranked.sort_values(
            ["_expected_loss", "churn_probability"], ascending=False
        ).head(top_n)
        ranked_by = "expected_value_at_risk"
    else:
        ranked = scored_ranked.sort_values("churn_probability", ascending=False).head(top_n)
        ranked_by = "churn_probability"

    # Per-customer SHAP driver breakdown — computed ONLY for the customers
    # about to be shown (not the whole customer base), since it's an
    # explanation for a specific prediction, not a global stat.
    feature_cols = res.get("feature_cols") or []
    top_drivers_by_customer: dict[str, list[dict]] = {}
    if res.get("_model") is not None and feature_cols:
        top_drivers_by_customer = explain_predictions(
            res["_model"], ranked[feature_cols].to_numpy(dtype=float),
            feature_cols, list(ranked.index),
        )

    at_risk_customers = []
    for customer_id, row in ranked.iterrows():
        prob = float(row["churn_probability"])
        entry = {
            "customer": str(customer_id),
            "name": str(name_map.get(customer_id, "")) if name_map else "",
            "churn_probability": round(prob, 4),
            "risk_tier": _risk_tier(prob),
            "recency_days": int(row.get("recency_days", 0)),
            "frequency": int(row.get("frequency", 0)),
            "top_drivers": top_drivers_by_customer.get(str(customer_id), []),
        }
        if has_money:
            entry["monetary"] = round(float(row.get("monetary", 0.0)), 2)
            entry["expected_loss"] = round(prob * float(row.get("monetary", 0.0)), 2)
        at_risk_customers.append(entry)

    return {
        "available": True,
        "horizon_days": horizon,
        "requested_horizon_days": horizon_days,
        "snapshot_date": str(snapshot.date()),
        "cutoff_date": str(pd.Timestamp(res["cutoff"]).date()),
        "training_cutoffs": res.get("training_cutoffs", []),
        "model": res["metrics"],
        "feature_importance": res["importances"],
        "shap_global_importance": res.get("shap_importance", []),
        "risk_distribution": risk_distribution,
        "customers_scored": int(len(scored)),
        "revenue_at_risk": revenue_at_risk,
        "expected_revenue_at_risk": expected_revenue_at_risk,
        # These two numbers look similar but answer different questions, and
        # an LLM asked to explain the difference will otherwise guess a
        # plausible-sounding but wrong formula — spell out the real one so
        # narration/follow-up answers stay grounded instead of invented.
        "metric_definitions": {
            "feature_importance": (
                "Permutation importance: how much the model's AUC would drop "
                "if this feature were shuffled. A GLOBAL measure of the "
                "model's reliance on the feature, not specific to any one "
                "customer."
            ),
            "shap_global_importance": (
                "Mean absolute SHAP value per feature, averaged across a "
                "validation sample. Also global, but a different, "
                "complementary explainability technique from "
                "feature_importance (per-prediction attribution vs. "
                "performance-drop-on-shuffle) — the two use different scales "
                "and are not directly comparable."
            ),
            "top_drivers": (
                "SHAP values computed for that SPECIFIC customer's own "
                "prediction: which features pushed THEIR churn probability "
                "up ('increases_risk') or down ('decreases_risk'), and by "
                "how much, relative to the model's average prediction. Only "
                "computed for customers listed in at_risk_customers."
            ),
            "revenue_at_risk": (
                "Unweighted sum of historical monetary value across ALL scored "
                "customers whose churn_probability is >= 0.5 (not just the "
                "sample listed in at_risk_customers)."
            ),
            "expected_revenue_at_risk": (
                "Probability-weighted expected loss across ALL scored customers: "
                "sum of (churn_probability * monetary) for every customer "
                "regardless of risk tier, including the many low/medium-risk "
                "customers not shown in at_risk_customers. There is no fixed "
                "size relationship between this and revenue_at_risk — it "
                "depends on the actual probability/spend distribution."
            ),
        },
        "at_risk_customers": at_risk_customers,
        "at_risk_ranked_by": ranked_by,
        # Full per-customer score map for downstream agents (e.g. Marketing).
        # Underscore prefix = internal: stripped before any LLM prompt.
        "_customer_scores": {
            str(cid): float(prob)
            for cid, prob in scored["churn_probability"].items()
        },
        "metadata": {
            "customer_column": cust,
            "time_column": time_col,
            "monetary_column": schema["monetary_columns"][0] if schema["monetary_columns"] else None,
            "row_count": int(len(df)),
            "history_days": span,
            "generated_at": datetime.now(UTC).isoformat(),
            "source": "in_memory" if data_df is not None else f"pg:{table_name}",
        },
    }
