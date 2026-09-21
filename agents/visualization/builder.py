"""
Deterministic executive-dashboard builder.

Rather than hoping an LLM writes good chart code, this assembles a curated,
board-quality dashboard directly from the schema-intelligence layer and the KPI
engine — so the core dashboard is always correct, consistent and instantly
professional. The LLM path (dashboard.py / viz_graph.py) stays available for
ad-hoc, chat-driven charts, themed to match.

Everything is grounded in the same deterministic engines the rest of the
platform uses, and a corrected revenue series (price × quantity when only a
unit price exists) is used for both the KPI cards and the charts.

Pure pandas / Plotly — no Streamlit, so it is headless-testable.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from agents.analytics.kpi_engine import compute_kpis

from .theme import (
    MUTED,
    PRIMARY,
    SEQUENCE,
    human_int,
    human_money,
    style_figure,
)

_HALF = "half"
_FULL = "full"


# ── Corrected revenue series ────────────────────────────────────────────────

def _looks_like_unit_price(col: str) -> bool:
    low = col.lower().replace("_", "").replace(" ", "")
    if "unitprice" in low:
        return True
    if "price" in low and not any(
        t in low for t in ("total", "line", "sub", "net", "gross", "amount", "sales", "revenue")
    ):
        return True
    return False


def _revenue_series(df: pd.DataFrame, schema: dict) -> tuple[pd.Series | None, str]:
    """Best revenue series for the dataset (price × qty when a unit price is the
    only money column) plus a human label of how it was derived."""
    monetary = schema.get("monetary_columns") or []
    if not monetary:
        return None, ""
    col = monetary[0]
    qty = schema.get("quantity_column")
    base = pd.to_numeric(df[col], errors="coerce").fillna(0)
    if _looks_like_unit_price(col) and qty and qty in df.columns:
        q = pd.to_numeric(df[qty], errors="coerce").fillna(0)
        return base * q, f"{col} × {qty}"
    return base, col


def _frame(df: pd.DataFrame, schema: dict, rev: pd.Series) -> pd.DataFrame:
    """Assemble an aligned working frame (time parsed, revenue attached)."""
    out = pd.DataFrame(index=df.index)
    out["rev"] = rev.values
    tcol = schema.get("time_column")
    if tcol and tcol in df.columns:
        out["t"] = pd.to_datetime(df[tcol], errors="coerce")
    return out


# ── Individual charts (each returns a chart dict or None) ────────────────────

def _chart_revenue_trend(work: pd.DataFrame) -> dict | None:
    if "t" not in work:
        return None
    d = work.dropna(subset=["t"])
    if d.empty:
        return None
    span = (d["t"].max() - d["t"].min()).days
    freq, freq_label = ("W", "Weekly") if span > 180 else ("D", "Daily")
    g = d.set_index("t").resample(freq)["rev"].sum()
    g = g[g.index.notna()]
    if len(g) < 2:
        return None
    win = min(7, max(2, len(g) // 4))
    roll = g.rolling(win).mean()

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=g.index, y=g.values, mode="lines", name="Revenue",
        line=dict(color=PRIMARY, width=2), fill="tozeroy",
        fillcolor="rgba(47,109,246,0.10)",
        hovertemplate="%{x|%b %d, %Y}<br>$%{y:,.0f}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=roll.index, y=roll.values, mode="lines", name=f"{win}-pt rolling avg",
        line=dict(color="#F5A524", width=2, dash="dot"),
        hovertemplate="%{x|%b %d, %Y}<br>$%{y:,.0f}<extra></extra>",
    ))
    style_figure(
        fig, title="Revenue Over Time",
        subtitle=f"{freq_label} totals · {g.index.min():%b %Y}–{g.index.max():%b %Y}",
        money_axis="y", height=380,
    )
    peak = g.idxmax()
    insight = (
        f"Peak {'week' if freq == 'W' else 'day'} was {peak:%b %d, %Y} at "
        f"{human_money(g.max())}; total {human_money(g.sum())} over the period."
    )
    return {"title": "Revenue Over Time", "fig": fig, "insight": insight, "width": _FULL}


def _chart_by_dimension(work_df: pd.DataFrame, label: str, title: str,
                        subtitle: str, top: int = 10) -> dict | None:
    g = work_df.groupby("key")["rev"].sum().sort_values(ascending=False).head(top)
    g = g[g > 0]
    if g.empty:
        return None
    total = work_df["rev"].sum()
    g = g.sort_values()  # ascending -> largest on top for horizontal bars
    fig = go.Figure(go.Bar(
        x=g.values, y=[str(i) for i in g.index], orientation="h",
        marker=dict(color=PRIMARY),
        text=[human_money(v) for v in g.values], textposition="auto",
        cliponaxis=False,
        hovertemplate="%{y}<br>$%{x:,.0f}<extra></extra>",
    ))
    style_figure(fig, title=title, subtitle=subtitle, money_axis="x",
                 height=380, show_legend=False)
    fig.update_layout(hovermode="closest")
    leader = g.idxmax()
    share = (g.max() / total * 100) if total else 0
    insight = f"‘{leader}’ leads with {human_money(g.max())} ({share:.0f}% of revenue)."
    return {"title": title, "fig": fig, "insight": insight, "width": _HALF}


def _chart_customer_mix(df: pd.DataFrame, schema: dict) -> dict | None:
    cust = schema.get("customer_column")
    if not cust or cust not in df.columns:
        return None
    order = schema.get("order_column")
    if order and order in df.columns:
        orders_per_cust = df.groupby(cust)[order].nunique()
    else:
        orders_per_cust = df.groupby(cust).size()
    total = int(len(orders_per_cust))
    if total == 0:
        return None
    rc = int((orders_per_cust >= 2).sum())   # returning = 2+ orders
    nc = total - rc                          # one-time buyers
    fig = go.Figure(go.Pie(
        labels=["One-time", "Returning"], values=[nc, rc], hole=0.62, sort=False,
        marker=dict(colors=[SEQUENCE[0], SEQUENCE[1]], line=dict(color="white", width=2)),
        textinfo="label+percent",
        hovertemplate="%{label}<br>%{value:,} customers (%{percent})<extra></extra>",
    ))
    repeat = (rc / total * 100) if total else 0
    fig.update_layout(annotations=[dict(
        text=f"<b>{repeat:.0f}%</b><br><span style='color:{MUTED};font-size:11px'>repeat</span>",
        x=0.5, y=0.5, showarrow=False, font=dict(size=20, color=PRIMARY),
    )])
    style_figure(fig, title="Customer Mix", subtitle="New vs returning customers", height=380)
    insight = f"{repeat:.0f}% of customers are returning ({human_int(rc)} of {human_int(total)})."
    return {"title": "Customer Mix", "fig": fig, "insight": insight, "width": _HALF}


def _chart_monthly(work: pd.DataFrame) -> dict | None:
    if "t" not in work:
        return None
    d = work.dropna(subset=["t"])
    m = d.set_index("t").resample("MS")["rev"].sum()
    m = m[m.index.notna()]
    if len(m) < 2:
        return None
    colors = [PRIMARY] * len(m)
    colors[int(np.argmax(m.values))] = "#12B5A5"
    fig = go.Figure(go.Bar(
        x=m.index, y=m.values, marker=dict(color=colors),
        hovertemplate="%{x|%b %Y}<br>$%{y:,.0f}<extra></extra>",
    ))
    fig.update_xaxes(dtick="M1", tickformat="%b\n%Y")
    style_figure(fig, title="Monthly Revenue", subtitle="Calendar-month totals",
                 money_axis="y", height=360, show_legend=False)
    best = m.idxmax()
    insight = f"Best month: {best:%b %Y} at {human_money(m.max())}."
    return {"title": "Monthly Revenue", "fig": fig, "insight": insight, "width": _HALF}


def _chart_day_of_week(work: pd.DataFrame) -> dict | None:
    if "t" not in work:
        return None
    d = work.dropna(subset=["t"]).copy()
    if d.empty:
        return None
    order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    d["dow"] = d["t"].dt.day_name()
    g = d.groupby("dow")["rev"].sum().reindex(order).fillna(0)
    if g.sum() == 0:
        return None
    colors = [PRIMARY if v < g.max() else "#12B5A5" for v in g.values]
    fig = go.Figure(go.Bar(
        x=order, y=g.values, marker=dict(color=colors),
        hovertemplate="%{x}<br>$%{y:,.0f}<extra></extra>",
    ))
    style_figure(fig, title="Revenue by Day of Week", subtitle="Which days drive sales",
                 money_axis="y", height=360, show_legend=False)
    best = g.idxmax()
    insight = f"‘{best}’ is the strongest day ({human_money(g.max())})."
    return {"title": "Revenue by Day of Week", "fig": fig, "insight": insight, "width": _HALF}


def _chart_order_value(df: pd.DataFrame, schema: dict, work: pd.DataFrame) -> dict | None:
    order_col = schema.get("order_column")
    if order_col and order_col in df.columns:
        vals = work["rev"].groupby(df[order_col].values).sum()
        title, subtitle = "Order Value Distribution", "Revenue per order"
    else:
        vals = work["rev"]
        title, subtitle = "Transaction Value Distribution", "Revenue per record"
    vals = vals[vals > 0]
    if len(vals) < 10:
        return None
    # Trim extreme tail so the histogram stays readable.
    hi = float(vals.quantile(0.99))
    clipped = vals[vals <= hi]
    fig = go.Figure(go.Histogram(
        x=clipped.values, nbinsx=40, marker=dict(color=PRIMARY, line=dict(color="white", width=0.5)),
        hovertemplate="$%{x:,.0f}<br>%{y} orders<extra></extra>",
    ))
    median = float(vals.median())
    fig.add_vline(x=median, line=dict(color="#F5A524", width=2, dash="dash"),
                  annotation_text=f"median {human_money(median)}",
                  annotation_position="top")
    style_figure(fig, title=title, subtitle=subtitle, money_axis="x",
                 height=360, show_legend=False)
    fig.update_layout(hovermode="closest", bargap=0.05)
    insight = f"Median {title.split()[0].lower()} value is {human_money(median)}."
    return {"title": title, "fig": fig, "insight": insight, "width": _HALF}


# ── Orchestration ────────────────────────────────────────────────────────────

def _metrics(df: pd.DataFrame, schema: dict, rev: pd.Series, kpi: dict) -> list[dict]:
    order_col = schema.get("order_column")
    cust_col = schema.get("customer_column")
    revenue = float(rev.sum())
    orders = int(df[order_col].nunique()) if order_col and order_col in df.columns else len(df)
    if order_col and order_col in df.columns and orders:
        aov = float(rev.groupby(df[order_col].values).sum().mean())
    else:
        aov = float(rev.mean()) if len(rev) else 0.0

    cards: list[dict] = [{"label": "Revenue", "value": human_money(revenue)}]
    cards.append({"label": "Orders", "value": human_int(orders)})
    cards.append({"label": "Avg Order Value", "value": human_money(aov)})
    if cust_col and cust_col in df.columns:
        cards.append({"label": "Customers", "value": human_int(df[cust_col].nunique())})
    gr = kpi.get("monthly_growth_avg")
    if gr is not None:
        cards.append({"label": "Avg MoM Growth", "value": f"{gr * 100:+.1f}%",
                      "direction": "up" if gr > 0 else "down" if gr < 0 else "flat"})
    return cards


def build_executive_dashboard(df: pd.DataFrame) -> dict:
    """
    Build a curated executive dashboard from a cleaned DataFrame.

    Returns
    -------
    dict with keys:
      - ``kpis``    : list of {label, value, [direction]} cards
      - ``charts``  : list of {title, fig, insight, width}
      - ``revenue_label`` : how revenue was derived
      - ``row_count`` : rows analysed
    """
    if df is None or df.empty:
        return {"kpis": [], "charts": [], "revenue_label": "", "row_count": 0}

    kpi = compute_kpis(df)
    schema = kpi.get("_schema") or {}
    rev, rev_label = _revenue_series(df, schema)
    if rev is None:
        # No monetary column — fall back to a count-based series so the time
        # charts still work.
        rev = pd.Series(np.ones(len(df)), index=df.index)
        rev_label = "record count"

    work = _frame(df, schema, rev)
    charts: list[dict] = []

    def add(chart: dict | None) -> None:
        if chart and chart.get("fig") is not None:
            charts.append(chart)

    add(_chart_revenue_trend(work))

    cats = schema.get("category_columns") or []
    if cats:
        wd = work.copy()
        wd["key"] = df[cats[0]].astype("string").fillna("(unknown)").values
        add(_chart_by_dimension(
            wd, cats[0], f"Revenue by {cats[0].replace('_', ' ').title()}",
            "Top categories by revenue", top=12))

    prod = schema.get("product_column")
    if prod and prod in df.columns:
        wp = work.copy()
        wp["key"] = df[prod].astype("string").fillna("(unknown)").values
        add(_chart_by_dimension(
            wp, prod, "Top Products", "Best sellers by revenue", top=10))

    add(_chart_customer_mix(df, schema))
    add(_chart_monthly(work))
    add(_chart_day_of_week(work))
    add(_chart_order_value(df, schema, work))

    return {
        "kpis": _metrics(df, schema, rev, kpi),
        "charts": charts,
        "revenue_label": rev_label,
        "row_count": len(df),
    }
