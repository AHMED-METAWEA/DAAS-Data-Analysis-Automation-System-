"""
RFM Segmentation Engine — deterministic customer segmentation from cleaned
e-commerce data.

Computes per-customer Recency, Frequency and Monetary scores (quintiles 1-5)
and maps each customer to a marketing segment (Champions, Loyal Customers,
At Risk, Hibernating, Lost, ...).  No LLM is involved — this is pure pandas so
the downstream marketing strategy is grounded in real numbers.

Resilient to missing columns: when no customer / time / monetary column is
detected, it returns ``{"available": False, "reason": ...}`` and the caller
falls back gracefully.
"""

from __future__ import annotations

import pandas as pd

from agents.analytics.schema_intel import build_schema_summary

# ── 5×5 RFM grid → segment name ────────────────────────────────────────────
# Keyed by (recency_score, frequency_monetary_score), both 1 (low) .. 5 (high).
# Recency score is inverted internally so 5 always means "most recent".
SEGMENT_GRID: dict[tuple[int, int], str] = {
    (5, 5): "Champions",        (5, 4): "Champions",        (5, 3): "Loyal Customers",
    (5, 2): "Potential Loyalists", (5, 1): "New Customers",
    (4, 5): "Champions",        (4, 4): "Loyal Customers",  (4, 3): "Loyal Customers",
    (4, 2): "Potential Loyalists", (4, 1): "Promising",
    (3, 5): "Can't Lose Them",  (3, 4): "Loyal Customers",  (3, 3): "Need Attention",
    (3, 2): "Need Attention",   (3, 1): "About to Sleep",
    (2, 5): "Can't Lose Them",  (2, 4): "At Risk",          (2, 3): "At Risk",
    (2, 2): "About to Sleep",   (2, 1): "Hibernating",
    (1, 5): "Can't Lose Them",  (1, 4): "At Risk",          (1, 3): "At Risk",
    (1, 2): "Hibernating",      (1, 1): "Lost",
}

# Marketing playbook hint per segment — used by the UI and the LLM context.
SEGMENT_PLAYBOOK: dict[str, str] = {
    "Champions": "Reward, upsell premium, ask for reviews/referrals.",
    "Loyal Customers": "Loyalty perks, cross-sell, early access.",
    "Potential Loyalists": "Membership offers, recommend complementary products.",
    "New Customers": "Onboarding series, first-repeat-purchase incentive.",
    "Promising": "Build awareness, limited-time welcome offer.",
    "Need Attention": "Reactivation offer, recommend best-sellers.",
    "About to Sleep": "Win-back email, share value/new arrivals.",
    "At Risk": "Personalized win-back, strong discount, survey.",
    "Can't Lose Them": "VIP win-back, talk to them directly, renewal offer.",
    "Hibernating": "Re-engagement campaign, brand reminder.",
    "Lost": "Low-cost reactivation or ignore; aggressive last offer.",
}


def _quintile_score(series: pd.Series, reverse: bool = False) -> pd.Series:
    """
    Map a numeric series to integer quintile scores 1..5.

    Uses rank-based binning so ties and skewed distributions are handled.
    Falls back to a linear scaling when there are too few distinct values
    for ``pd.qcut`` to produce 5 bins.

    Parameters
    ----------
    series : pd.Series
        Values to score.
    reverse : bool
        When True, invert the score (used for recency where a *lower* value —
        fewer days since last purchase — should map to a *higher* score).
    """
    ranks = series.rank(method="first")
    try:
        scores = pd.qcut(ranks, 5, labels=[1, 2, 3, 4, 5]).astype(int)
    except ValueError:
        n = max(len(series), 1)
        scores = (ranks / n * 5).clip(lower=1, upper=5).round().astype(int)
    if reverse:
        scores = 6 - scores
    return scores


def compute_rfm(df: pd.DataFrame, snapshot_date: pd.Timestamp | None = None) -> dict:
    """
    Compute an RFM segmentation summary for the dataset.

    Parameters
    ----------
    df : pd.DataFrame
        Cleaned transaction-level data.
    snapshot_date : pd.Timestamp, optional
        "Today" for the recency calculation.  Defaults to the day after the
        latest transaction in the data.

    Returns
    -------
    dict
        ``{"available": False, "reason": ...}`` when segmentation is not
        possible, otherwise a structured payload with per-segment statistics.
    """
    schema = build_schema_summary(df)
    customer_col = schema["customer_column"]
    time_col = schema["time_column"]
    order_col = schema["order_column"]
    monetary_cols = schema["monetary_columns"]
    revenue_col = monetary_cols[0] if monetary_cols else None

    if not customer_col:
        return {"available": False, "reason": "No customer identifier column detected."}
    if not time_col and not revenue_col:
        return {
            "available": False,
            "reason": "Need at least a date or a monetary column for RFM.",
        }

    work = df.copy()

    # ── Frequency source: distinct orders if available, else row count ──────
    if order_col:
        freq = work.groupby(customer_col)[order_col].nunique()
    else:
        freq = work.groupby(customer_col).size()
    freq = freq.rename("frequency")

    # ── Monetary source ────────────────────────────────────────────────────
    if revenue_col:
        money = pd.to_numeric(work[revenue_col], errors="coerce").fillna(0)
        work = work.assign(_rev=money)
        monetary = work.groupby(customer_col)["_rev"].sum().rename("monetary")
    else:
        # Fall back to frequency as a proxy so the model still runs.
        monetary = freq.rename("monetary").astype(float)

    # ── Recency source ──────────────────────────────────────────────────────
    if time_col:
        work["_dt"] = pd.to_datetime(work[time_col], errors="coerce")
        valid = work.dropna(subset=["_dt"])
        if valid.empty:
            return {"available": False, "reason": "Date column unparseable for recency."}
        snap = snapshot_date or (valid["_dt"].max() + pd.Timedelta(days=1))
        last_purchase = valid.groupby(customer_col)["_dt"].max()
        recency = (snap - last_purchase).dt.days.rename("recency")
    else:
        snap = None
        # Without dates, recency cannot be measured; use neutral score later.
        recency = pd.Series(0, index=freq.index, name="recency")

    rfm = pd.concat([recency, freq, monetary], axis=1).dropna()
    if rfm.empty:
        return {"available": False, "reason": "No customers with complete RFM data."}

    # ── Scoring ─────────────────────────────────────────────────────────────
    if time_col:
        rfm["R"] = _quintile_score(rfm["recency"], reverse=True)
    else:
        rfm["R"] = 3  # neutral when recency is unknown
    rfm["F"] = _quintile_score(rfm["frequency"])
    rfm["M"] = _quintile_score(rfm["monetary"])
    rfm["FM"] = ((rfm["F"] + rfm["M"]) / 2).round().clip(1, 5).astype(int)
    rfm["segment"] = rfm.apply(
        lambda r: SEGMENT_GRID.get((int(r["R"]), int(r["FM"])), "Need Attention"),
        axis=1,
    )

    total_customers = int(len(rfm))
    total_revenue = float(rfm["monetary"].sum()) or 1.0

    segments: dict[str, dict] = {}
    for name, grp in rfm.groupby("segment"):
        seg_rev = float(grp["monetary"].sum())
        segments[name] = {
            "count": int(len(grp)),
            "customer_pct": round(len(grp) / total_customers * 100, 1),
            "revenue": round(seg_rev, 2),
            "revenue_pct": round(seg_rev / total_revenue * 100, 1),
            "avg_recency_days": round(float(grp["recency"].mean()), 1) if time_col else None,
            "avg_frequency": round(float(grp["frequency"].mean()), 2),
            "avg_monetary": round(float(grp["monetary"].mean()), 2),
            "avg_rfm_score": round(float((grp["R"] + grp["F"] + grp["M"]).mean()), 2),
            "playbook": SEGMENT_PLAYBOOK.get(name, ""),
        }

    # Sort segments by revenue contribution (most valuable first)
    segments = dict(
        sorted(segments.items(), key=lambda kv: kv[1]["revenue"], reverse=True)
    )

    return {
        "available": True,
        "snapshot_date": str(snap.date()) if snap is not None else None,
        "total_customers": total_customers,
        "total_revenue": round(total_revenue, 2),
        "columns_used": {
            "customer": customer_col,
            "time": time_col,
            "order": order_col,
            "monetary": revenue_col,
        },
        "segments": segments,
        "segment_order": list(segments.keys()),
        # Per-customer segment map for cross-agent joins (e.g. churn scores).
        # Underscore prefix = internal: stripped before any LLM prompt.
        "_segment_by_customer": {
            str(cid): seg for cid, seg in rfm["segment"].items()
        },
        # Per-customer R/F/M component scores, for consumers that need to show
        # *why* a customer landed in a segment rather than just the label (the
        # CRM customer page). Exposed here rather than recomputed downstream so
        # there is one quintile implementation: two would drift the moment one
        # of them changed its tie-breaking or its qcut fallback, and a customer
        # would silently be a "Champion" on one page and "Loyal" on another.
        "_rfm_by_customer": {
            str(cid): {
                "r": int(row["R"]),
                "f": int(row["F"]),
                "m": int(row["M"]),
                "segment": str(row["segment"]),
                "recency_days": int(row["recency"]),
                "frequency": int(row["frequency"]),
                "monetary": float(row["monetary"]),
            }
            for cid, row in rfm.iterrows()
        },
    }
