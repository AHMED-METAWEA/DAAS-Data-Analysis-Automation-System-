"""
Customer-level feature engineering for churn prediction.

All features for a customer are computed from that customer's transactions
*up to a cutoff date*. This is what keeps the model honest: training features
are measured as-of a past cutoff while the label looks at the following
horizon, so the model learns to predict the future rather than memorise it.

The feature builder is schema-aware (reuses the analytics schema-intelligence
layer) and degrades gracefully when optional columns (product, quantity,
discount, …) are missing.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Candidate features, in a stable order. Only those that can be built from the
# detected schema are actually used by the model.
CANDIDATE_FEATURES = [
    "recency_days",
    "frequency",
    "monetary",
    "tenure_days",
    "avg_order_value",
    "avg_interpurchase_days",
    "orders_last_30",
    "orders_last_60",
    "orders_last_90",
    "spend_last_90",
    "spend_trend",
    "distinct_products",
    "distinct_categories",
    "total_quantity",
    "avg_discount",
]


def parse_time(df: pd.DataFrame, time_col: str) -> pd.DataFrame:
    """Return a copy with a parsed ``_dt`` column, dropping unparseable dates."""
    dt = pd.to_datetime(df[time_col], errors="coerce")
    return df.assign(_dt=dt).dropna(subset=["_dt"])


def _find_discount_column(df: pd.DataFrame) -> str | None:
    for col in df.columns:
        if "discount" in col.lower() and pd.api.types.is_numeric_dtype(df[col]):
            return col
    return None


def compute_customer_features(
    tx: pd.DataFrame, cutoff: pd.Timestamp, schema: dict
) -> pd.DataFrame:
    """Per-customer features from transactions already filtered to ``_dt <= cutoff``.

    Index is the customer id. Returns numeric, NaN/inf-free features.
    """
    cust = schema["customer_column"]
    order = schema["order_column"]
    monetary_cols = schema["monetary_columns"]
    money = monetary_cols[0] if monetary_cols else None
    product = schema["product_column"]
    qty = schema["quantity_column"]
    cats = schema["category_columns"]

    by_cust = tx.groupby(cust)
    feats = pd.DataFrame(index=by_cust.size().index)

    last = by_cust["_dt"].max()
    first = by_cust["_dt"].min()
    feats["recency_days"] = (cutoff - last).dt.days.clip(lower=0)
    feats["tenure_days"] = (cutoff - first).dt.days.clip(lower=0)

    if order and order in tx.columns:
        feats["frequency"] = by_cust[order].nunique()
    else:
        feats["frequency"] = by_cust.size()

    if money:
        money_series = pd.to_numeric(tx[money], errors="coerce")
        feats["monetary"] = money_series.groupby(tx[cust]).sum()
        feats["avg_order_value"] = feats["monetary"] / feats["frequency"].replace(0, np.nan)

    # Average days between purchases (tenure spread over the purchase count).
    feats["avg_interpurchase_days"] = feats["tenure_days"] / feats["frequency"].clip(lower=1)

    # ── Recent-activity windows (as-of the cutoff) ───────────────────────────
    # Long-horizon RFM misses momentum: a customer with 50 lifetime orders but
    # none in the last 60 days is far riskier than the totals suggest.
    for window in (30, 60, 90):
        recent = tx[tx["_dt"] > cutoff - pd.Timedelta(days=window)]
        if order and order in tx.columns:
            counts = recent.groupby(cust)[order].nunique()
        else:
            counts = recent.groupby(cust).size()
        feats[f"orders_last_{window}"] = counts.reindex(feats.index)

    if money:
        recent90 = tx[tx["_dt"] > cutoff - pd.Timedelta(days=90)]
        recent_spend = (
            pd.to_numeric(recent90[money], errors="coerce")
            .groupby(recent90[cust]).sum()
        )
        feats["spend_last_90"] = recent_spend.reindex(feats.index)
        # Momentum: share of lifetime spend that happened in the last 90 days.
        feats["spend_trend"] = (
            feats["spend_last_90"].fillna(0.0)
            / feats["monetary"].replace(0, np.nan)
        )

    if product:
        feats["distinct_products"] = by_cust[product].nunique()
    if cats:
        feats["distinct_categories"] = by_cust[cats[0]].nunique()
    if qty:
        qty_series = pd.to_numeric(tx[qty], errors="coerce")
        feats["total_quantity"] = qty_series.groupby(tx[cust]).sum()

    disc = _find_discount_column(tx)
    if disc:
        disc_series = pd.to_numeric(tx[disc], errors="coerce")
        feats["avg_discount"] = disc_series.groupby(tx[cust]).mean()

    feats = feats.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return feats


def feature_columns(feats: pd.DataFrame) -> list[str]:
    """Ordered list of model feature columns present in ``feats``."""
    return [c for c in CANDIDATE_FEATURES if c in feats.columns]
