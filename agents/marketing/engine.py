"""
Marketing Analytics Engine — main orchestrator.

Runs the deterministic marketing analytics pipeline:
  1. Schema intelligence (column-role detection)
  2. Core business KPIs (reused from the analytics engine)
  3. RFM customer segmentation
  4. Marketing-specific KPIs (repeat rate, CLV proxy, churn-risk base)
  5. Channel / dimension performance (region, category, payment, ...)

Returns a structured payload consumable by the marketing strategy / campaign
LLM agent.  No LLM runs here — everything is computed from the data.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from agents.analytics.kpi_engine import compute_kpis
from agents.analytics.schema_intel import build_schema_summary
from tools.db_tools import load_df_from_pg

from .segmentation import compute_rfm

# Segments considered "at risk" / lapsing for the churn-base metric.
_CHURN_RISK_SEGMENTS = {
    "At Risk", "Can't Lose Them", "About to Sleep", "Hibernating", "Lost",
}
# Segments worth acquiring-style nurturing.
_GROWTH_SEGMENTS = {"New Customers", "Promising", "Potential Loyalists"}


def _safe_numeric(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").fillna(0)


def _is_text_like(s: pd.Series) -> bool:
    """True for object / string / categorical columns (pandas 2.x and 3.x)."""
    return (
        s.dtype == object
        or pd.api.types.is_string_dtype(s)
        or isinstance(s.dtype, pd.CategoricalDtype)
    )


def compute_marketing_kpis(df: pd.DataFrame, kpi: dict, rfm: dict) -> dict:
    """Derive marketing-oriented KPIs from the core KPIs and RFM output."""
    out: dict = {}

    total_customers = kpi.get("total_customers")
    returning = kpi.get("returning_customers")
    new = kpi.get("new_customers")

    # ── Repeat-purchase / loyalty ──────────────────────────────────────────
    if total_customers and returning is not None:
        out["repeat_purchase_rate"] = round(returning / total_customers * 100, 1)
        out["one_time_buyer_rate"] = round((total_customers - returning) / total_customers * 100, 1)
    else:
        out["repeat_purchase_rate"] = None
        out["one_time_buyer_rate"] = None

    out["new_customers"] = new
    out["returning_customers"] = returning
    out["new_customer_pct"] = kpi.get("new_customer_pct")
    out["returning_customer_pct"] = kpi.get("returning_customer_pct")

    # ── CLV proxy (avg revenue per customer) ───────────────────────────────
    out["avg_customer_value"] = kpi.get("revenue_per_customer_avg")
    out["median_customer_value"] = kpi.get("revenue_per_customer_median")
    out["aov"] = kpi.get("aov")
    out["aov_median"] = kpi.get("aov_median")

    # ── Churn-risk customer base (from RFM) ────────────────────────────────
    if rfm.get("available"):
        segs = rfm["segments"]
        churn_count = sum(segs[s]["count"] for s in segs if s in _CHURN_RISK_SEGMENTS)
        churn_rev = sum(segs[s]["revenue"] for s in segs if s in _CHURN_RISK_SEGMENTS)
        growth_count = sum(segs[s]["count"] for s in segs if s in _GROWTH_SEGMENTS)
        total = rfm["total_customers"] or 1
        out["churn_risk_customers"] = churn_count
        out["churn_risk_pct"] = round(churn_count / total * 100, 1)
        out["revenue_at_risk"] = round(churn_rev, 2)
        out["growth_segment_customers"] = growth_count
        high_value = {"Champions", "Loyal Customers"}
        out["high_value_customers"] = sum(
            segs[s]["count"] for s in segs if s in high_value
        )
    else:
        out["churn_risk_customers"] = None
        out["churn_risk_pct"] = None

    return out


def compute_channel_performance(df: pd.DataFrame, schema: dict, top_n: int = 8) -> dict:
    """
    Revenue and order breakdown across categorical "channel" dimensions
    (region, category, payment method, marketing channel, ...).
    """
    monetary_cols = schema["monetary_columns"]
    revenue_col = monetary_cols[0] if monetary_cols else None

    # Columns that are not useful as marketing "channel" dimensions.
    exclude = set(filter(None, [
        schema.get("customer_column"),
        schema.get("order_column"),
        schema.get("product_column"),
        schema.get("time_column"),
    ]))

    # Candidate dimensions: detected category columns + any low-cardinality
    # text column or one whose name looks like a channel/segment dimension.
    _CHANNEL_KEYWORDS = (
        "region", "channel", "country", "city", "state", "source", "medium",
        "segment", "category", "payment", "department", "type",
    )
    dims: list[str] = list(schema["category_columns"])
    for col in df.columns:
        if col in dims or col in exclude:
            continue
        s = df[col]
        if not _is_text_like(s):
            continue
        nunique = int(s.nunique(dropna=True))
        keyword_hit = any(k in col.lower() for k in _CHANNEL_KEYWORDS)
        if keyword_hit or (2 <= nunique <= 30):
            dims.append(col)

    result: dict[str, dict] = {}
    for dim in dims:
        if dim not in df.columns:
            continue
        try:
            if revenue_col:
                rev = df.groupby(dim)[revenue_col].apply(lambda s: float(_safe_numeric(s).sum()))
                rev = rev.sort_values(ascending=False)
                total = float(rev.sum()) or 1.0
                breakdown = {
                    str(k): {
                        "revenue": round(float(v), 2),
                        "revenue_pct": round(float(v) / total * 100, 1),
                    }
                    for k, v in rev.head(top_n).items()
                }
            else:
                counts = df[dim].value_counts().head(top_n)
                total = int(counts.sum()) or 1
                breakdown = {
                    str(k): {
                        "count": int(v),
                        "share_pct": round(int(v) / total * 100, 1),
                    }
                    for k, v in counts.items()
                }
            if breakdown:
                result[dim] = breakdown
        except Exception:
            continue
    return result


def build_churn_section(churn_payload: dict | None, rfm: dict) -> dict:
    """Fold the Churn agent's model output into the marketing payload.

    Produces a prompt-safe summary (risk distribution, revenue exposure,
    churn risk *per RFM segment*) plus internal ``_target_lists`` — the exact
    customer IDs per risk tier — so campaigns can target model-scored
    customers instead of RFM proxies.
    """
    if not churn_payload or not churn_payload.get("available"):
        return {"available": False,
                "reason": "No churn model run available — run the Churn agent first."}

    # Same tier thresholds as the churn engine (single source of truth).
    from agents.churn.engine import HIGH_RISK, MED_RISK

    scores: dict[str, float] = churn_payload.get("_customer_scores", {}) or {}
    seg_by_cust: dict[str, str] = (rfm or {}).get("_segment_by_customer", {}) or {}

    # ── Churn risk per RFM segment ──────────────────────────────────────────
    by_segment: dict[str, dict] = {}
    if scores and seg_by_cust:
        agg: dict[str, list[float]] = {}
        for cid, prob in scores.items():
            seg = seg_by_cust.get(cid)
            if seg is not None:
                agg.setdefault(seg, []).append(prob)
        for seg, probs in agg.items():
            n = len(probs)
            by_segment[seg] = {
                "customers_scored": n,
                "avg_churn_probability": round(sum(probs) / n, 3),
                "high_risk_count": sum(1 for p in probs if p >= HIGH_RISK),
                "high_risk_pct": round(
                    sum(1 for p in probs if p >= HIGH_RISK) / n * 100, 1
                ),
            }
        by_segment = dict(
            sorted(by_segment.items(),
                   key=lambda kv: -kv[1]["avg_churn_probability"])
        )

    # ── Targetable audience lists (internal; exported by the UI) ────────────
    target_lists = {
        "high_risk": sorted(
            (c for c, p in scores.items() if p >= HIGH_RISK),
            key=lambda c: -scores[c],
        ),
        "medium_risk": sorted(
            (c for c, p in scores.items() if MED_RISK <= p < HIGH_RISK),
            key=lambda c: -scores[c],
        ),
    }

    model = churn_payload.get("model", {}) or {}
    return {
        "available": True,
        "horizon_days": churn_payload.get("horizon_days"),
        "snapshot_date": churn_payload.get("snapshot_date"),
        "model_name": model.get("name"),
        "model_auc": model.get("auc"),
        "risk_distribution": churn_payload.get("risk_distribution"),
        "revenue_at_risk": churn_payload.get("revenue_at_risk"),
        "expected_revenue_at_risk": churn_payload.get("expected_revenue_at_risk"),
        "customers_scored": churn_payload.get("customers_scored"),
        "churn_risk_by_rfm_segment": by_segment,
        "top_at_risk_customers": churn_payload.get("at_risk_customers", [])[:10],
        "_target_lists": target_lists,
    }


def run_marketing_analytics(
    data_df: pd.DataFrame | None = None,
    table_name: str = "",
    churn_payload: dict | None = None,
) -> dict:
    """
    Execute the full marketing analytics pipeline.

    Parameters
    ----------
    data_df : DataFrame or None
        In-memory cleaned data.  When *None* loads from *table_name*.
    table_name : str
        PostgreSQL table name (used only when *data_df* is None).
    churn_payload : dict, optional
        Output of ``agents.churn.engine.run_churn_analysis``.  When provided,
        the payload gains a ``churn`` section with model-scored risk per RFM
        segment and targetable at-risk customer lists.

    Returns
    -------
    dict
        ``{schema, kpi, rfm, marketing_kpis, channels, churn, metadata}``
    """
    df = data_df if data_df is not None else load_df_from_pg(table_name)

    schema = build_schema_summary(df)
    kpi = compute_kpis(df)
    # The raw schema dict embedded in kpi is verbose; drop it from the payload.
    kpi.pop("_schema", None)

    rfm = compute_rfm(df)
    marketing_kpis = compute_marketing_kpis(df, kpi, rfm)
    channels = compute_channel_performance(df, schema)
    churn = build_churn_section(churn_payload, rfm)

    # When the real model ran, surface its numbers next to the RFM proxies so
    # the LLM (and the UI) can prefer them.
    if churn.get("available"):
        rd = churn.get("risk_distribution") or {}
        scored = churn.get("customers_scored") or 0
        if scored:
            marketing_kpis["model_churn_high_risk_customers"] = rd.get("high")
            marketing_kpis["model_churn_high_risk_pct"] = round(
                (rd.get("high") or 0) / scored * 100, 1
            )
        if churn.get("expected_revenue_at_risk") is not None:
            marketing_kpis["model_expected_revenue_at_risk"] = churn[
                "expected_revenue_at_risk"
            ]

    return {
        "schema": schema,
        "kpi": kpi,
        "rfm": rfm,
        "marketing_kpis": marketing_kpis,
        "channels": channels,
        "churn": churn,
        "metadata": {
            "row_count": len(df),
            "column_count": len(df.columns),
            "columns": list(df.columns),
            "generated_at": datetime.now(UTC).isoformat(),
            "source": "in_memory" if data_df is not None else f"pg:{table_name}",
        },
    }
