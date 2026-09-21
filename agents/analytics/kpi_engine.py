"""
KPI Engine — compute business metrics from cleaned e-commerce data.

Resilient to missing columns: every metric falls back gracefully
when its required columns are not detected.

Revenue is derived **once**, into a single per-row ``line_revenue`` series, and
every downstream revenue metric (AOV, revenue-per-customer, top products,
category mix, monthly growth) is computed from that same series.  This
guarantees the figures reconcile with each other and removes the historical
ambiguity where the primary monetary column was treated *both* as an already-
extended line total *and* as a per-unit price (see ``line_revenue``).

``line_revenue`` is public because the decision-metrics layer
(``agents/insights/decision_metrics.py``) must derive its period comparisons
from the *same* per-row revenue series — otherwise a period total could
disagree with the headline revenue it is compared against.
"""

from __future__ import annotations

import pandas as pd

from .schema_intel import build_schema_summary


def _safe_numeric(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").fillna(0)


def _normalize(name: str) -> str:
    return name.lower().replace("_", "").replace("-", "").replace(" ", "")


# Tokens that mark a monetary column as an ALREADY-EXTENDED line/order total
# (revenue is its plain sum — never multiply by quantity).
_LINE_TOTAL_TOKENS = (
    "total", "amount", "revenue", "sales", "gmv", "subtotal", "linetotal",
    "grandtotal", "spent", "value", "net", "gross", "turnover", "income",
)
# Tokens that mark a monetary column as a PER-UNIT price (revenue is price ×
# quantity).  ``price`` alone is treated as a unit price only when no line-total
# token is present.
_UNIT_PRICE_TOKENS = (
    "unitprice", "priceperunit", "priceeach", "itemprice", "saleprice", "priceper",
)


def _is_unit_price_column(name: str) -> bool:
    """True when a monetary column holds a PER-UNIT price (so revenue must be
    price × quantity); False when it is an already-extended line/order total.

    Ambiguity resolves toward "line total" — the safe default — because the
    schema layer boosts true revenue/total columns into the primary slot, so a
    per-unit price only reaches this function when no total column exists.
    """
    norm = _normalize(name)
    if any(t in norm for t in _LINE_TOTAL_TOKENS):
        return False
    if any(t in norm for t in _UNIT_PRICE_TOKENS):
        return True
    return norm == "price"


def line_revenue(df: pd.DataFrame, revenue_col: str, qty_col: str | None) -> tuple[pd.Series, str]:
    """Return ``(line_revenue_series, basis_label)`` for the chosen revenue column.

    The single source of truth for "revenue per row" used by every revenue KPI.
    When the primary monetary column is a per-unit price *and* a quantity column
    exists, revenue is ``price × quantity``; otherwise the column is summed
    as-is (it is already a line/order total).
    """
    if qty_col and _is_unit_price_column(revenue_col):
        line_rev = _safe_numeric(df[revenue_col]) * _safe_numeric(df[qty_col])
        return line_rev, "unit_price × quantity"
    return _safe_numeric(df[revenue_col]), "line/order total (column sum)"


def compute_kpis(df: pd.DataFrame) -> dict:
    schema = build_schema_summary(df)
    kpi = {}

    time_col = schema["time_column"]
    monetary_cols = schema["monetary_columns"]
    qty_col = schema["quantity_column"]
    product_col = schema["product_column"]
    customer_col = schema["customer_column"]
    order_col = schema["order_column"]

    # ── Revenue (single source of truth) ───────────────────────────────
    # ``line_rev`` is a per-row revenue series aligned to df.index; EVERY
    # revenue metric below is grouped from it, so the parts always reconcile
    # with the whole (Σ top_products ≤ revenue, order totals sum to revenue…).
    revenue_col = monetary_cols[0] if monetary_cols else None
    if revenue_col:
        line_rev, revenue_basis = line_revenue(df, revenue_col, qty_col)
        kpi["revenue"] = round(float(line_rev.sum()), 2)
        kpi["revenue_column_used"] = revenue_col
        kpi["revenue_basis"] = revenue_basis
        kpi["avg_revenue_per_row"] = round(float(line_rev.mean()), 2)
    else:
        line_rev = None
        kpi["revenue"] = 0.0
        kpi["revenue_column_used"] = None
        kpi["revenue_basis"] = None

    # ── Orders ─────────────────────────────────────────────────────────
    if order_col:
        kpi["orders"] = int(df[order_col].nunique())
        kpi["order_column_used"] = order_col
        if line_rev is not None:
            order_rev = line_rev.groupby(df[order_col]).sum()
            kpi["aov"] = round(float(order_rev.mean()), 2)
            kpi["aov_median"] = round(float(order_rev.median()), 2)
            kpi["min_order_value"] = round(float(order_rev.min()), 2)
            kpi["max_order_value"] = round(float(order_rev.max()), 2)
            kpi["aov_basis"] = "order total (revenue grouped by order id)"
    else:
        kpi["orders"] = len(df)
        kpi["order_column_used"] = None
        kpi["aov"] = kpi.get("avg_revenue_per_row", 0.0)
        # No order id: each row is treated as an "order", so AOV == avg row
        # revenue. Label it so the number is never mistaken for a true basket.
        kpi["aov_basis"] = "revenue per row (no order id detected)"

    # ── Items per order ────────────────────────────────────────────────
    if order_col and qty_col:
        items_per_order = df.groupby(order_col)[qty_col].sum()
        kpi["items_per_order_avg"] = round(float(items_per_order.mean()), 2)
        kpi["items_per_order_median"] = round(float(items_per_order.median()), 2)
    else:
        kpi["items_per_order_avg"] = None

    # ── Revenue per customer ───────────────────────────────────────────
    if customer_col and line_rev is not None:
        rpc = line_rev.groupby(df[customer_col]).sum()
        kpi["revenue_per_customer_avg"] = round(float(rpc.mean()), 2)
        kpi["revenue_per_customer_median"] = round(float(rpc.median()), 2)
        kpi["total_customers"] = int(df[customer_col].nunique())
    elif customer_col:
        kpi["total_customers"] = int(df[customer_col].nunique())
        kpi["revenue_per_customer_avg"] = None
    else:
        kpi["total_customers"] = None
        kpi["revenue_per_customer_avg"] = None

    # ── Top / bottom products ──────────────────────────────────────────
    if product_col and line_rev is not None:
        prod_rev = line_rev.groupby(df[product_col]).sum().sort_values(ascending=False)
        kpi["top_products"] = {
            str(k): round(float(v), 2)
            for k, v in prod_rev.head(10).items()
        }
        kpi["bottom_products"] = {
            str(k): round(float(v), 2)
            for k, v in prod_rev.tail(10).items()
        }
        kpi["product_count"] = int(prod_rev.count())
    elif product_col:
        kpi["product_count"] = int(df[product_col].nunique())
        kpi["top_products"] = {}
        kpi["bottom_products"] = {}
    else:
        kpi["product_count"] = None
        kpi["top_products"] = {}
        kpi["bottom_products"] = {}

    # ── Customer segmentation: one-time vs returning ───────────────────
    # A "returning" customer has 2+ distinct orders (or 2+ transactions when no
    # order id exists); "new"/one-time customers bought exactly once. These are a
    # non-overlapping partition, so new + returning == total_customers and the
    # percentages sum to 100 (the previous version double-counted first purchases,
    # which made new_customer_pct always ~100%).
    if customer_col:
        try:
            if order_col:
                orders_per_cust = df.groupby(customer_col)[order_col].nunique()
            else:
                orders_per_cust = df.groupby(customer_col).size()
            total_c = int(orders_per_cust.size)
            returning = int((orders_per_cust >= 2).sum())
            one_time = total_c - returning
            kpi["returning_customers"] = returning
            kpi["new_customers"] = one_time
            kpi["one_time_customers"] = one_time
            if total_c > 0:
                kpi["returning_customer_pct"] = round(returning / total_c * 100, 1)
                kpi["new_customer_pct"] = round(one_time / total_c * 100, 1)
        except Exception:
            kpi["new_customers"] = None
            kpi["returning_customers"] = None
    else:
        kpi["new_customers"] = None
        kpi["returning_customers"] = None

    # ── Growth metrics (if time exists) ────────────────────────────────
    if time_col and line_rev is not None:
        try:
            df_t = pd.DataFrame({
                "_period": pd.to_datetime(df[time_col], errors="coerce").dt.to_period("M"),
                "_line_rev": line_rev.to_numpy(),
            })
            monthly_rev = df_t.dropna(subset=["_period"]).groupby("_period")["_line_rev"].sum().sort_index()
            if len(monthly_rev) >= 2:
                growth = monthly_rev.pct_change().dropna()
                kpi["monthly_growth_avg"] = round(float(growth.mean()), 4)
                kpi["monthly_growth_std"] = round(float(growth.std()), 4)
                kpi["monthly_growth_min"] = round(float(growth.min()), 4)
                kpi["monthly_growth_max"] = round(float(growth.max()), 4)
                kpi["growth_trend_direction"] = (
                    "up" if kpi["monthly_growth_avg"] > 0.01
                    else "down" if kpi["monthly_growth_avg"] < -0.01
                    else "flat"
                )
            else:
                kpi["monthly_growth_avg"] = None
        except Exception:
            kpi["monthly_growth_avg"] = None
    else:
        kpi["monthly_growth_avg"] = None

    # ── Category breakdown ─────────────────────────────────────────────
    cat_cols = schema["category_columns"]
    if cat_cols and line_rev is not None:
        kpi["category_revenue"] = {}
        for cat in cat_cols:
            cat_rev = line_rev.groupby(df[cat]).sum().sort_values(ascending=False)
            kpi["category_revenue"][cat] = {
                str(k): round(float(v), 2)
                for k, v in cat_rev.head(20).items()
            }

    # Surface the schema layer's self-audit so ambiguous auto-detection is
    # visible to the report / grounding / UI instead of silently trusted.
    kpi["schema_warnings"] = schema.get("warnings", [])
    kpi["revenue_detection_confidence"] = (
        schema.get("detection_confidence", {}).get("revenue_column")
    )

    kpi["_schema"] = schema
    return kpi
