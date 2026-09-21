"""
Decision Metrics — the deterministic layer that turns *descriptive* analytics
into *decision-grade* evidence.

The KPI engine answers "what are the totals?".  A business owner reading a
report needs the next three questions answered instead:

  1. **Is it better or worse than before, and by how much?**  (period comparison)
  2. **What actually drove the change — more orders, or bigger baskets?**
     (revenue bridge; the two imply completely different actions)
  3. **Where is the money concentrated, where is it leaking, and how much is
     at stake?**  (concentration, margin, discount, momentum, scenarios)

Every figure here is computed by exact arithmetic on the DataFrame — nothing is
modelled, estimated or inferred by an LLM — and every figure carries the inputs
it was derived from so the report can show its work.

Two invariants make the output safe to publish verbatim:

* **Single revenue source of truth.**  Period revenue is summed from the very
  same per-row series (:func:`agents.analytics.kpi_engine.line_revenue`) that
  produces the headline revenue, so the parts always reconcile with the whole.
* **Validate before claiming.**  Derived quantities that depend on an assumed
  column relationship (profit = revenue − cost, revenue = gross × (1 − discount))
  are only reported when that identity is *checked against the actual rows*.
  When it fails, the section is omitted with a stated reason instead of
  publishing a plausible-looking wrong number.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from agents.analytics.kpi_engine import line_revenue
from agents.analytics.schema_intel import build_schema_summary

# A row-level identity (profit = revenue − cost) counts as "how this dataset
# works" only when it holds on essentially every row. Below this share we treat
# the columns as unrelated and publish nothing rather than a wrong margin.
_IDENTITY_MIN_SHARE = 0.95
# Relative tolerance when checking those identities row-by-row (floating point
# money columns rarely reconcile to the cent).
_IDENTITY_RTOL = 0.01
# Comparison windows need this many days of history to be a like-for-like
# comparison ("last 30 days vs the 30 before it" needs 60 days).
_ROLLING_WINDOW_DAYS = 30
# A product must move at least this share of window revenue to be worth naming
# as a riser/faller — otherwise the report chases noise.
_MOMENTUM_MIN_SHARE = 0.005
# Each weekday needs this many occurrences before a day-of-week pattern is
# anything but an accident of the calendar.
_MIN_WEEKDAY_OCCURRENCES = 4


def _r(x: object, nd: int = 2) -> float | None:
    """Round to *nd* places, returning None for anything not a finite number."""
    try:
        v = float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not np.isfinite(v):
        return None
    return round(v, nd)


def _share(part: float | None, whole: float | None) -> float | None:
    """``part / whole`` as a percentage, or None when it is undefined."""
    if part is None or not whole:
        return None
    return _r(part / whole * 100, 1)


def _detect_cost_column(df: pd.DataFrame) -> str | None:
    for col in df.columns:
        name = str(col).lower()
        if "cost" in name and pd.api.types.is_numeric_dtype(df[col]):
            return str(col)
    return None


def _detect_profit_column(df: pd.DataFrame) -> str | None:
    for col in df.columns:
        name = str(col).lower()
        if ("profit" in name or "margin" in name) and pd.api.types.is_numeric_dtype(df[col]):
            return str(col)
    return None


def _detect_discount_column(df: pd.DataFrame) -> str | None:
    for col in df.columns:
        name = str(col).lower()
        if "discount" in name and pd.api.types.is_numeric_dtype(df[col]):
            return str(col)
    return None


def _detect_unit_price_column(df: pd.DataFrame) -> str | None:
    for col in df.columns:
        name = str(col).lower().replace("_", "").replace(" ", "")
        if name in ("unitprice", "priceperunit", "priceeach", "itemprice", "price"):
            if pd.api.types.is_numeric_dtype(df[col]):
                return str(col)
    return None


def _identity_holds(left: pd.Series, right: pd.Series) -> float:
    """Share of rows where ``left ≈ right`` within :data:`_IDENTITY_RTOL`.

    Used to *prove* a column relationship on the actual data before any figure
    derived from it is published.
    """
    left = pd.to_numeric(left, errors="coerce")
    right = pd.to_numeric(right, errors="coerce")
    both = left.notna() & right.notna()
    if not bool(both.any()):
        return 0.0
    lo, ro = left[both], right[both]
    tol = np.maximum(np.abs(ro) * _IDENTITY_RTOL, 0.01)
    return float((np.abs(lo - ro) <= tol).mean())


# ── Windowed aggregation ────────────────────────────────────────────────────

def _window_stats(
    mask: pd.Series,
    work: pd.DataFrame,
    *,
    label: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> dict[str, Any]:
    """Revenue / orders / customers / AOV for one date window.

    ``orders`` counts distinct order ids when one exists and rows otherwise, and
    AOV is always ``revenue / orders`` for that same definition — so the three
    figures are arithmetically consistent with each other by construction.
    """
    sub = work[mask]
    revenue = float(sub["_rev"].sum())
    if "_order" in sub.columns:
        orders = int(sub["_order"].nunique())
        order_basis = "distinct order ids"
    else:
        orders = int(len(sub))
        order_basis = "rows (no order id column)"
    customers = int(sub["_customer"].nunique()) if "_customer" in sub.columns else None
    units = float(sub["_qty"].sum()) if "_qty" in sub.columns else None
    return {
        "label": label,
        "start": start.date().isoformat(),
        "end": end.date().isoformat(),
        "days": int((end.normalize() - start.normalize()).days) + 1,
        "revenue": _r(revenue),
        "orders": orders,
        "order_basis": order_basis,
        "customers": customers,
        "units": _r(units) if units is not None else None,
        "aov": _r(revenue / orders) if orders else None,
        "rows": int(len(sub)),
        # Unrounded mirror. The bridge must decompose from these: derived from
        # the published 2-dp AOV instead, the three components miss the revenue
        # change by ~0.20 — small, but a reader who adds them up finds a report
        # whose own arithmetic does not close.
        "exact": {"revenue": revenue, "aov": (revenue / orders) if orders else None},
    }


def _bridge(current: dict, prior: dict) -> dict[str, Any]:
    """Decompose the revenue change into volume, basket-size and interaction.

    Revenue = orders × AOV, so the change between two periods splits exactly:

        Δrevenue = (Δorders × AOV_prior)      ← volume effect
                 + (ΔAOV   × orders_prior)    ← basket-size effect
                 + (Δorders × ΔAOV)           ← interaction

    The three components sum to Δrevenue identically; ``residual`` is reported
    so a reader can see the identity closes (it is 0 up to rounding) rather
    than having to trust it.
    """
    # Decompose from the unrounded values so the identity closes exactly; only
    # the published components are rounded.
    exact_c, exact_p = current.get("exact") or {}, prior.get("exact") or {}
    rev_c, rev_p = exact_c.get("revenue"), exact_p.get("revenue")
    ord_c, ord_p = current.get("orders"), prior.get("orders")
    aov_c, aov_p = exact_c.get("aov"), exact_p.get("aov")
    if None in (rev_c, rev_p, aov_c, aov_p) or not ord_p:
        return {"available": False, "reason": "orders or AOV unavailable in one of the periods"}

    d_rev = rev_c - rev_p
    volume = (ord_c - ord_p) * aov_p
    basket = (aov_c - aov_p) * ord_p
    interaction = (ord_c - ord_p) * (aov_c - aov_p)
    residual = d_rev - (volume + basket + interaction)

    driver = "volume" if abs(volume) >= abs(basket) else "basket size"
    return {
        "available": True,
        "revenue_change": _r(d_rev),
        "revenue_change_pct": _share(d_rev, abs(rev_p)) if rev_p else None,
        "volume_effect": _r(volume),
        "basket_effect": _r(basket),
        "interaction_effect": _r(interaction),
        "residual": _r(residual),
        "primary_driver": driver,
        "orders_change": ord_c - ord_p,
        "aov_change": _r(aov_c - aov_p),
        "customers_change": (
            current["customers"] - prior["customers"]
            if current.get("customers") is not None and prior.get("customers") is not None
            else None
        ),
    }


def _comparison(work: pd.DataFrame, first: pd.Timestamp, last: pd.Timestamp) -> dict[str, Any]:
    """Build both standard comparisons: rolling 30-day and last complete month.

    Each carries a ``comparable`` flag: a period the dataset only partially
    covers is *reported as such* rather than silently compared against a full
    one (the classic way a report invents a fake "-40% collapse" out of a data
    cut-off).
    """
    out: dict[str, Any] = {}
    dt = work["_dt"]

    # ── Rolling 30 vs prior 30, anchored on the last day with data ──────────
    cur_start = last.normalize() - pd.Timedelta(days=_ROLLING_WINDOW_DAYS - 1)
    prior_end = cur_start - pd.Timedelta(days=1)
    prior_start = prior_end - pd.Timedelta(days=_ROLLING_WINDOW_DAYS - 1)
    rolling_comparable = first.normalize() <= prior_start
    cur = _window_stats(
        (dt >= cur_start) & (dt <= last), work,
        label=f"last {_ROLLING_WINDOW_DAYS} days", start=cur_start, end=last,
    )
    pri = _window_stats(
        (dt >= prior_start) & (dt <= prior_end), work,
        label=f"previous {_ROLLING_WINDOW_DAYS} days", start=prior_start, end=prior_end,
    )
    out["rolling_30d"] = {
        "comparable": bool(rolling_comparable),
        "reason": None if rolling_comparable else (
            f"data starts {first.date().isoformat()}, so the previous "
            f"{_ROLLING_WINDOW_DAYS}-day window is only partly covered"
        ),
        "current": cur,
        "prior": pri,
        "bridge": _bridge(cur, pri) if rolling_comparable else {"available": False,
                                                               "reason": "window not fully covered"},
    }

    # ── Last complete calendar month vs the month before ────────────────────
    last_month_end = last.normalize()
    month_end_of_last = (last_month_end + pd.offsets.MonthEnd(0)).normalize()
    if last_month_end < month_end_of_last:
        # The final month is truncated — step back to the last complete one.
        cur_m_end = (last_month_end - pd.offsets.MonthBegin(1)).normalize() - pd.Timedelta(days=1)
    else:
        cur_m_end = month_end_of_last
    cur_m_start = cur_m_end.replace(day=1)
    pri_m_end = cur_m_start - pd.Timedelta(days=1)
    pri_m_start = pri_m_end.replace(day=1)
    month_comparable = first.normalize() <= pri_m_start and cur_m_start <= last.normalize()
    if month_comparable:
        cur_m = _window_stats(
            (dt >= cur_m_start) & (dt <= cur_m_end + pd.Timedelta(hours=23, minutes=59, seconds=59)),
            work, label=cur_m_start.strftime("%B %Y"), start=cur_m_start, end=cur_m_end,
        )
        pri_m = _window_stats(
            (dt >= pri_m_start) & (dt <= pri_m_end + pd.Timedelta(hours=23, minutes=59, seconds=59)),
            work, label=pri_m_start.strftime("%B %Y"), start=pri_m_start, end=pri_m_end,
        )
        out["last_month"] = {
            "comparable": True, "reason": None,
            "current": cur_m, "prior": pri_m, "bridge": _bridge(cur_m, pri_m),
        }
    else:
        out["last_month"] = {
            "comparable": False,
            "reason": "the dataset does not fully cover two consecutive calendar months",
        }
    return out


# ── Concentration ───────────────────────────────────────────────────────────

def _concentration(series_rev: pd.Series, kind: str) -> dict[str, Any]:
    """Pareto profile of a revenue distribution (products or customers).

    Returns how many entities carry 80% of revenue and the share held by the
    largest ones — the difference between "we have 300 products" and "9 of them
    are the business".
    """
    s = series_rev[series_rev > 0].sort_values(ascending=False)
    total = float(s.sum())
    n = int(s.size)
    if n == 0 or total <= 0:
        return {"available": False, "reason": f"no positive {kind} revenue"}
    cum_share = (s.cumsum() / total).to_numpy()
    n_for_80 = int(np.searchsorted(cum_share, 0.80) + 1)
    n_for_50 = int(np.searchsorted(cum_share, 0.50) + 1)
    top_n = min(10, n)
    top_decile = max(1, int(round(n * 0.10)))
    return {
        "available": True,
        "kind": kind,
        "total_count": n,
        "total_revenue": _r(total),
        "count_for_80pct": n_for_80,
        "count_for_80pct_share_of_base": _share(n_for_80, n),
        "count_for_50pct": n_for_50,
        "top1_name": str(s.index[0]),
        "top1_revenue": _r(float(s.iloc[0])),
        "top1_share": _share(float(s.iloc[0]), total),
        "top5_share": _share(float(s.iloc[:5].sum()), total) if n >= 5 else None,
        "top10_share": _share(float(s.iloc[:top_n].sum()), total) if n >= 10 else None,
        "top_decile_count": top_decile,
        "top_decile_share": _share(float(s.iloc[:top_decile].sum()), total),
        "bottom_half_share": _share(float(s.iloc[n // 2:].sum()), total),
        "top_entities": [
            {"name": str(k), "revenue": _r(float(v)), "share": _share(float(v), total)}
            for k, v in s.head(10).items()
        ],
    }


# ── Momentum ────────────────────────────────────────────────────────────────

def _momentum(work: pd.DataFrame, comparison: dict) -> dict[str, Any]:
    """Which products gained or lost the most revenue between the two windows.

    Ranked by *absolute* revenue change, not percentage: a product that fell
    from 40,000 to 30,000 matters more to the P&L than one that fell from 90 to
    30, even though the latter looks worse in percentage terms.
    """
    if "_product" not in work.columns:
        return {"available": False, "reason": "no product column detected"}
    # Same preference order as the headline comparison (calendar month first).
    # If these two disagreed, the report would name a revenue drop for one window
    # and then attribute it to products measured over a different one.
    basis = None
    for key in ("last_month", "rolling_30d"):
        if comparison.get(key, {}).get("comparable"):
            basis = key
            break
    if basis is None:
        return {"available": False, "reason": "no fully covered comparison window"}

    cmp_ = comparison[basis]
    dt = work["_dt"]
    c, p = cmp_["current"], cmp_["prior"]
    cur_mask = (dt >= pd.Timestamp(c["start"])) & (dt < pd.Timestamp(c["end"]) + pd.Timedelta(days=1))
    pri_mask = (dt >= pd.Timestamp(p["start"])) & (dt < pd.Timestamp(p["end"]) + pd.Timedelta(days=1))

    cur = work[cur_mask].groupby("_product")["_rev"].sum()
    pri = work[pri_mask].groupby("_product")["_rev"].sum()
    joined = pd.concat([cur.rename("current"), pri.rename("prior")], axis=1).fillna(0.0)
    joined["delta"] = joined["current"] - joined["prior"]

    window_rev = float(max(cur.sum(), pri.sum()))
    floor = window_rev * _MOMENTUM_MIN_SHARE

    def _rows(frame: pd.DataFrame) -> list[dict]:
        out = []
        for name, row in frame.iterrows():
            prior_v, cur_v = float(row["prior"]), float(row["current"])
            out.append({
                "product": str(name),
                "current_revenue": _r(cur_v),
                "prior_revenue": _r(prior_v),
                "change": _r(cur_v - prior_v),
                "change_pct": _share(cur_v - prior_v, prior_v) if prior_v > 0 else None,
                "is_new": prior_v == 0,
                "is_lost": cur_v == 0,
            })
        return out

    material = joined[joined["delta"].abs() >= floor]
    risers = material.sort_values("delta", ascending=False).head(5)
    fallers = material.sort_values("delta").head(5)
    return {
        "available": True,
        "basis": basis,
        "current_window": c["label"],
        "prior_window": p["label"],
        "materiality_floor": _r(floor),
        "risers": _rows(risers[risers["delta"] > 0]),
        "fallers": _rows(fallers[fallers["delta"] < 0]),
        "products_compared": int(len(joined)),
    }


# ── Margin ──────────────────────────────────────────────────────────────────

def derive_line_profit(
    df: pd.DataFrame,
    rev: pd.Series,
    qty: pd.Series | None,
    revenue_col: str,
) -> tuple[pd.Series | None, str, dict[str, Any]]:
    """Per-row gross profit — but only when the data *proves* the relationship.

    Returns ``(line_profit, basis, checks)``; ``line_profit`` is None when no
    cost/profit column reconciles with revenue on the actual rows, in which case
    ``basis`` states why.

    A cost column may hold a *line* cost or a *unit* cost; a profit column may
    or may not actually equal revenue − cost.  Both possibilities are checked
    row-by-row here so that every consumer — the margin section of this module
    and the root-cause engine's gross-profit measure — derives profit the same
    way, and neither can publish a margin the data does not support.
    """
    cost_col = _detect_cost_column(df)
    profit_col = _detect_profit_column(df)
    checks: dict[str, Any] = {}
    if not cost_col and not profit_col:
        return None, "no cost or profit column in the data", checks

    if profit_col:
        prof = pd.to_numeric(df[profit_col], errors="coerce").fillna(0.0)
        prof.index = rev.index if len(prof) == len(rev) else prof.index
        if cost_col:
            cost = pd.to_numeric(df[cost_col], errors="coerce").fillna(0.0)
            share = _identity_holds(prof, rev - cost)
            checks["profit_equals_revenue_minus_cost_share"] = _r(share, 3)
            if share >= _IDENTITY_MIN_SHARE:
                return prof, (
                    f"'{profit_col}' column, verified row-by-row against "
                    f"'{revenue_col}' − '{cost_col}' on {share * 100:.1f}% of rows"
                ), checks
        else:
            return prof, f"'{profit_col}' column (no cost column available to cross-check)", checks

    if cost_col:
        cost = pd.to_numeric(df[cost_col], errors="coerce").fillna(0.0)
        line_share = float((cost <= rev + 0.01).mean())
        checks["cost_within_revenue_share"] = _r(line_share, 3)
        if line_share >= _IDENTITY_MIN_SHARE:
            return (
                rev - cost,
                f"'{revenue_col}' − '{cost_col}' (cost verified as a line-level cost)",
                checks,
            )
        if qty is not None:
            extended = cost * qty
            unit_share = float((extended <= rev + 0.01).mean())
            checks["unit_cost_x_qty_within_revenue_share"] = _r(unit_share, 3)
            if unit_share >= _IDENTITY_MIN_SHARE:
                return (
                    rev - extended,
                    f"'{revenue_col}' − ('{cost_col}' × quantity), cost read as a per-unit cost",
                    checks,
                )

    return None, (
        f"a cost/profit column exists but does not reconcile with "
        f"'{revenue_col}' on the rows, so no margin is reported"
    ), checks


def _margin(df: pd.DataFrame, work: pd.DataFrame, revenue_col: str | None) -> dict[str, Any]:
    """Gross profit and per-product margin — only when the data proves it.

    The row-level profit derivation (and the identity checks that license it)
    lives in :func:`derive_line_profit`; everything below turns that series into
    the portfolio and per-product figures the report cites.
    """
    if not _detect_cost_column(df) and not _detect_profit_column(df):
        return {"available": False, "reason": "no cost or profit column in the data"}
    if revenue_col is None:
        return {"available": False, "reason": "no revenue column detected"}

    rev = work["_rev"]
    line_profit, basis, checks = derive_line_profit(
        df, rev, work["_qty"] if "_qty" in work.columns else None, revenue_col,
    )

    if line_profit is None:
        return {"available": False, "reason": basis, "checks": checks}

    total_rev = float(rev.sum())
    gross_profit = float(line_profit.sum())
    result: dict[str, Any] = {
        "available": True,
        "basis": basis,
        "checks": checks,
        "gross_profit": _r(gross_profit),
        "gross_margin_pct": _share(gross_profit, total_rev),
        "revenue": _r(total_rev),
    }

    if "_product" not in work.columns:
        return result

    grp = pd.DataFrame({"_product": work["_product"], "rev": rev, "profit": line_profit})
    per = grp.groupby("_product").agg(rev=("rev", "sum"), profit=("profit", "sum"))
    per = per[per["rev"] > 0]
    per["margin_pct"] = per["profit"] / per["rev"] * 100
    overall_pct = gross_profit / total_rev * 100 if total_rev else 0.0

    def _prod_rows(frame: pd.DataFrame) -> list[dict]:
        return [
            {
                "product": str(i),
                "revenue": _r(float(r["rev"])),
                "gross_profit": _r(float(r["profit"])),
                "margin_pct": _r(float(r["margin_pct"]), 1),
                "revenue_share": _share(float(r["rev"]), total_rev),
            }
            for i, r in frame.iterrows()
        ]

    # Products big enough to matter but earning less than the portfolio rate:
    # the single most actionable pricing/sourcing list in the whole report.
    material = per[per["rev"] >= total_rev * 0.01]
    drag = material[material["margin_pct"] < overall_pct - 2].sort_values("rev", ascending=False)
    result["overall_margin_pct"] = _r(overall_pct, 1)
    result["best_margin_products"] = _prod_rows(material.sort_values("margin_pct", ascending=False).head(5))
    result["worst_margin_products"] = _prod_rows(material.sort_values("margin_pct").head(5))
    result["loss_making_products"] = _prod_rows(per[per["profit"] < 0].sort_values("profit").head(5))
    result["margin_drag_products"] = _prod_rows(drag.head(5))
    result["margin_drag_revenue"] = _r(float(drag["rev"].sum()))
    result["margin_drag_share"] = _share(float(drag["rev"].sum()), total_rev)
    # Exact arithmetic: profit if the drag products earned the portfolio rate.
    if len(drag):
        uplift = float((drag["rev"] * overall_pct / 100 - drag["profit"]).sum())
        result["margin_drag_uplift"] = _r(uplift)
    return result


# ── Discount ────────────────────────────────────────────────────────────────

def _discount(df: pd.DataFrame, work: pd.DataFrame) -> dict[str, Any]:
    """Revenue given away as discount — only when the pricing identity checks out.

    Requires unit price × quantity × (1 − discount) ≈ revenue on the actual
    rows.  Without that proof the discount column's scale (fraction vs percent)
    and meaning are guesswork, and the section is skipped.
    """
    disc_col = _detect_discount_column(df)
    if not disc_col:
        return {"available": False, "reason": "no discount column in the data"}
    price_col = _detect_unit_price_column(df)
    if not price_col or "_qty" not in work.columns:
        return {"available": False, "reason": "no unit price / quantity columns to reconstruct the pre-discount price"}

    disc_raw = pd.to_numeric(df[disc_col], errors="coerce").fillna(0.0).to_numpy()
    gross = pd.to_numeric(df[price_col], errors="coerce").fillna(0.0).to_numpy() * work["_qty"].to_numpy()
    rev = work["_rev"].to_numpy()

    # Try both conventions and keep the one the rows actually support.
    for scale, label in ((100.0, "percent (0-100)"), (1.0, "fraction (0-1)")):
        frac = disc_raw / scale
        if np.nanmax(frac) > 1.0 or np.nanmin(frac) < 0.0:
            continue
        modelled = gross * (1 - frac)
        share = _identity_holds(pd.Series(rev), pd.Series(modelled))
        if share >= _IDENTITY_MIN_SHARE:
            gross_total = float(np.nansum(gross))
            given = gross_total - float(np.nansum(rev))
            discounted = frac > 0
            n_disc = int(discounted.sum())
            return {
                "available": True,
                "column": disc_col,
                "scale": label,
                "identity_share": _r(share, 3),
                "basis": (
                    f"'{price_col}' × quantity × (1 − {disc_col}) reconciles with revenue "
                    f"on {share * 100:.1f}% of rows"
                ),
                "gross_before_discount": _r(gross_total),
                "revenue_after_discount": _r(float(np.nansum(rev))),
                "discount_given": _r(given),
                "discount_share_of_gross": _share(given, gross_total),
                "lines_discounted": n_disc,
                "lines_discounted_pct": _share(n_disc, len(frac)),
                "avg_discount_on_discounted_lines_pct": (
                    _r(float(frac[discounted].mean()) * 100, 1) if n_disc else None
                ),
                "max_discount_pct": _r(float(np.nanmax(frac)) * 100, 1),
            }
    return {
        "available": False,
        "reason": (
            f"'{disc_col}' does not reconcile with price × quantity vs revenue, "
            "so the amount discounted cannot be proven from this data"
        ),
    }


# ── Weekday rhythm ──────────────────────────────────────────────────────────

def _weekday(work: pd.DataFrame) -> dict[str, Any]:
    """Average revenue per weekday — the staffing / promo-timing decision.

    Uses the *mean per occurrence*, never the sum: a dataset ending on a Tuesday
    has more Tuesdays than Sundays, and summing would invent a Tuesday peak.
    """
    daily = work.set_index("_dt")["_rev"].resample("D").sum()
    if daily.empty:
        return {"available": False, "reason": "no daily series"}
    frame = pd.DataFrame({"rev": daily.to_numpy(), "dow": daily.index.dayofweek,
                          "name": daily.index.day_name()})
    counts = frame.groupby("dow").size()
    if int(counts.min()) < _MIN_WEEKDAY_OCCURRENCES:
        return {
            "available": False,
            "reason": f"fewer than {_MIN_WEEKDAY_OCCURRENCES} occurrences of some weekdays",
        }
    stats = frame.groupby(["dow", "name"])["rev"].mean().reset_index().sort_values("rev", ascending=False)
    best, worst = stats.iloc[0], stats.iloc[-1]
    overall = float(frame["rev"].mean())
    worst_count = int(counts.loc[int(worst["dow"])])
    return {
        "available": True,
        "weeks_covered": _r(len(daily) / 7, 1),
        "avg_daily_revenue": _r(overall),
        "best_day": str(best["name"]),
        "best_day_avg": _r(float(best["rev"])),
        "best_day_vs_avg_pct": _share(float(best["rev"]) - overall, overall),
        "worst_day": str(worst["name"]),
        "worst_day_avg": _r(float(worst["rev"])),
        "worst_day_vs_avg_pct": _share(float(worst["rev"]) - overall, overall),
        "worst_day_count": worst_count,
        # Exact arithmetic: what the weakest weekday would have added over the
        # whole period had it merely performed like an ordinary day.
        "worst_day_gap_total": _r((overall - float(worst["rev"])) * worst_count),
        "by_day": [
            {"day": str(r["name"]), "avg_revenue": _r(float(r["rev"]))}
            for _, r in stats.iterrows()
        ],
    }


# ── Repeat-purchase economics ───────────────────────────────────────────────

def _repeat(work: pd.DataFrame) -> dict[str, Any]:
    """How much of the business comes from customers who came back.

    Splits revenue between one-time and repeat customers — the number that
    decides whether to spend the next marketing pound on acquisition or on
    retention.
    """
    if "_customer" not in work.columns:
        return {"available": False, "reason": "no customer column detected"}
    if "_order" in work.columns:
        orders_per = work.groupby("_customer")["_order"].nunique()
        basis = "distinct orders per customer"
    else:
        orders_per = work.groupby("_customer").size()
        basis = "rows per customer (no order id column)"
    rev_per = work.groupby("_customer")["_rev"].sum()
    total = int(orders_per.size)
    if total == 0:
        return {"available": False, "reason": "no customers"}

    repeat_ids = orders_per[orders_per >= 2].index
    one_ids = orders_per[orders_per < 2].index
    repeat_rev = float(rev_per.reindex(repeat_ids).sum())
    one_rev = float(rev_per.reindex(one_ids).sum())
    total_rev = repeat_rev + one_rev
    return {
        "available": True,
        "basis": basis,
        "customers": total,
        "repeat_customers": int(len(repeat_ids)),
        "repeat_rate_pct": _share(len(repeat_ids), total),
        "one_time_customers": int(len(one_ids)),
        "one_time_rate_pct": _share(len(one_ids), total),
        "repeat_revenue": _r(repeat_rev),
        "repeat_revenue_share": _share(repeat_rev, total_rev),
        "one_time_revenue": _r(one_rev),
        "avg_orders_per_customer": _r(float(orders_per.mean())),
        "avg_revenue_per_repeat_customer": _r(float(rev_per.reindex(repeat_ids).mean())) if len(repeat_ids) else None,
        "avg_revenue_per_one_time_customer": _r(float(rev_per.reindex(one_ids).mean())) if len(one_ids) else None,
        "median_order_value": _r(float(work.groupby("_order")["_rev"].sum().median())) if "_order" in work.columns else None,
    }


# ── Scenarios (exact arithmetic under a stated assumption) ──────────────────

def _scenarios(
    repeat: dict,
    conc: dict,
    margin: dict,
    discount: dict,
    comparison: dict,
    weekday: dict,
) -> list[dict[str, Any]]:
    """Size the opportunities in money, by arithmetic, with the assumption named.

    These are deliberately NOT forecasts.  Each one is a multiplication whose
    inputs are all measured, published together with the assumption that makes
    it meaningful, so a reader can accept or reject the assumption rather than
    having to trust a model.

    They also exist for a second, defensive reason.  A report has to answer
    "what is this action worth?", and if no figure is provided for it the model
    will reach for the nearest large number it can see — quoting a period's
    total revenue as the value of fixing a decline.  Providing the *correct*
    figure for the question is what stops that, far more reliably than a rule
    telling it not to.
    """
    out: list[dict[str, Any]] = []

    # Recovering a decline: the exposure is the decline itself, never the period
    # total the decline happened inside.
    for key in ("last_month", "rolling_30d"):
        cmp_ = comparison.get(key) or {}
        bridge = cmp_.get("bridge") or {}
        if not cmp_.get("comparable") or not bridge.get("available"):
            continue
        change = bridge.get("revenue_change")
        if change is not None and change < 0:
            cur, pri = cmp_["current"], cmp_["prior"]
            out.append({
                "key": "decline_recovery",
                "label": f"Revenue recovered if {cur['label']} returned to the {pri['label']} level",
                "value": _r(abs(change)),
                "formula": f"revenue in {pri['label']} − revenue in {cur['label']}",
                "assumption": f"the drop between {pri['label']} and {cur['label']} is fully reversed",
                "caveat": "this is the size of what was lost, not a promise it comes back",
                "action": "demand / traffic recovery",
            })
        break

    if weekday.get("available") and weekday.get("worst_day_gap_total"):
        out.append({
            "key": "weak_weekday_uplift",
            "label": f"Revenue if every {weekday['worst_day']} performed like an average day",
            "value": weekday["worst_day_gap_total"],
            "formula": (
                f"({weekday['avg_daily_revenue']:,.2f} average day − "
                f"{weekday['worst_day_avg']:,.2f} average {weekday['worst_day']}) × "
                f"{weekday['worst_day_count']:,} {weekday['worst_day']}s in the period"
            ),
            "assumption": "the weakest weekday is lifted to the all-day average and holds there",
            "caveat": "weekday demand differs for real reasons; part of the gap is not addressable",
            "action": "staffing, stock and promotion timing",
        })

    if repeat.get("available") and repeat.get("one_time_customers") and repeat.get("median_order_value"):
        n, mov = repeat["one_time_customers"], repeat["median_order_value"]
        out.append({
            "key": "second_purchase_upside",
            "label": "Revenue if every one-time customer bought once more",
            "value": _r(n * mov),
            "formula": f"{n:,} one-time customers × {mov:,.2f} median order value",
            "assumption": "each one-time customer places exactly one more order of median size",
            "caveat": "an arithmetic ceiling, not a forecast — real reactivation converts a fraction of them",
            "action": "retention / reactivation campaign",
        })

    if conc.get("products", {}).get("available"):
        p = conc["products"]
        out.append({
            "key": "top_product_dependency",
            "label": f"Revenue that disappears if '{p['top1_name']}' is lost",
            "value": p["top1_revenue"],
            "formula": f"revenue of '{p['top1_name']}' over the full period",
            "assumption": "no substitution — customers do not switch to another product",
            "caveat": "a worst-case exposure figure; some demand would shift to other products",
            "action": "supply / supplier risk review for the top seller",
        })

    if margin.get("available") and margin.get("margin_drag_uplift"):
        out.append({
            "key": "margin_normalisation_upside",
            "label": "Extra gross profit if below-average-margin products earned the portfolio rate",
            "value": margin["margin_drag_uplift"],
            "formula": (
                f"Σ(revenue × {margin['overall_margin_pct']}% − actual gross profit) over the "
                f"{len(margin.get('margin_drag_products', []))} named below-average products"
            ),
            "assumption": "volume is unchanged when price rises or cost falls to the portfolio margin",
            "caveat": "raising price usually costs some volume; treat as the ceiling of a pricing action",
            "action": "pricing / sourcing review on the named products",
        })

    if discount.get("available") and discount.get("discount_given"):
        out.append({
            "key": "discount_recapture",
            "label": "Revenue currently given away as discount",
            "value": discount["discount_given"],
            "formula": (
                f"{discount['gross_before_discount']:,.2f} pre-discount value − "
                f"{discount['revenue_after_discount']:,.2f} actual revenue"
            ),
            "assumption": "the same units would have sold at full price",
            "caveat": "discounts drive some of that volume; the recoverable share is smaller",
            "action": "discount-policy review",
        })

    return out


# ── Public entry point ──────────────────────────────────────────────────────

def compute_decision_metrics(
    df: pd.DataFrame,
    schema: dict | None = None,
) -> dict[str, Any]:
    """Compute the full decision layer for *df*.

    Every section degrades independently: a dataset with no dates still gets
    concentration and margin, and each unavailable section states *why* so the
    report can tell the reader what the data cannot answer instead of glossing
    over it.
    """
    result: dict[str, Any] = {
        "available": False, "sections": {}, "errors": [], "scenarios": [], "blind_spots": [],
    }
    if df is None or len(df) == 0:
        result["reason"] = "empty dataset"
        result["blind_spots"] = ["The dataset is empty, so nothing can be measured from it."]
        return result

    schema = schema or build_schema_summary(df)
    monetary = schema.get("monetary_columns") or []
    revenue_col = monetary[0] if monetary else None
    time_col = schema.get("time_column")
    qty_col = schema.get("quantity_column")

    if revenue_col is None:
        result["reason"] = "no monetary column detected — money-based decisions cannot be computed"
        # The blind spots matter *more* here, not less: this is the case where a
        # reader would otherwise be handed a thin report with no explanation of
        # why it is thin.
        result["blind_spots"] = _blind_spots(df, schema, {})
        return result

    rev_series, revenue_basis = line_revenue(df, revenue_col, qty_col)
    work = pd.DataFrame({"_rev": rev_series.to_numpy()}, index=df.index)
    for role, col in (
        ("_order", schema.get("order_column")),
        ("_customer", schema.get("customer_column")),
        ("_product", schema.get("product_column")),
    ):
        if col is not None and col in df.columns:
            work[role] = df[col].to_numpy()
    if qty_col is not None and qty_col in df.columns:
        work["_qty"] = pd.to_numeric(df[qty_col], errors="coerce").fillna(0.0).to_numpy()

    result["available"] = True
    result["revenue_column"] = revenue_col
    result["revenue_basis"] = revenue_basis
    result["total_revenue"] = _r(float(work["_rev"].sum()))

    # ── Time-dependent sections ────────────────────────────────────────────
    coverage: dict[str, Any] = {"available": False, "reason": "no date column detected"}
    comparison: dict[str, Any] = {}
    weekday: dict[str, Any] = {"available": False, "reason": "no date column detected"}
    if time_col is not None and time_col in df.columns:
        parsed = pd.to_datetime(df[time_col], errors="coerce")
        work["_dt"] = parsed.to_numpy()
        dated = work.dropna(subset=["_dt"])
        unparsed = int(len(work) - len(dated))
        if len(dated):
            work = dated.sort_values("_dt")
            first, last = work["_dt"].iloc[0], work["_dt"].iloc[-1]
            days_span = int((last.normalize() - first.normalize()).days) + 1
            days_with_data = int(work["_dt"].dt.normalize().nunique())
            coverage = {
                "available": True,
                "date_column": time_col,
                "first_date": first.date().isoformat(),
                "last_date": last.date().isoformat(),
                "days_span": days_span,
                "days_with_data": days_with_data,
                "days_without_data": days_span - days_with_data,
                "rows": int(len(df)),
                "rows_with_unparseable_date": unparsed,
                "rows_with_unparseable_date_pct": _share(unparsed, len(df)),
                "months_span": _r(days_span / 30.44, 1),
            }
            try:
                comparison = _comparison(work, first, last)
            except Exception as exc:  # pragma: no cover - defensive
                result["errors"].append(f"comparison: {exc}")
            try:
                weekday = _weekday(work)
            except Exception as exc:  # pragma: no cover - defensive
                result["errors"].append(f"weekday: {exc}")
        else:
            coverage = {"available": False, "reason": f"no parseable dates in '{time_col}'"}

    # ── Non-time sections ──────────────────────────────────────────────────
    concentration: dict[str, Any] = {}
    if "_product" in work.columns:
        concentration["products"] = _concentration(work.groupby("_product")["_rev"].sum(), "product")
    else:
        concentration["products"] = {"available": False, "reason": "no product column detected"}
    if "_customer" in work.columns:
        concentration["customers"] = _concentration(work.groupby("_customer")["_rev"].sum(), "customer")
    else:
        concentration["customers"] = {"available": False, "reason": "no customer column detected"}

    try:
        margin = _margin(df.loc[work.index], work, revenue_col)
    except Exception as exc:  # pragma: no cover - defensive
        margin = {"available": False, "reason": f"margin computation failed: {exc}"}
        result["errors"].append(f"margin: {exc}")
    try:
        discount = _discount(df.loc[work.index], work)
    except Exception as exc:  # pragma: no cover - defensive
        discount = {"available": False, "reason": f"discount computation failed: {exc}"}
        result["errors"].append(f"discount: {exc}")
    try:
        repeat = _repeat(work)
    except Exception as exc:  # pragma: no cover - defensive
        repeat = {"available": False, "reason": f"repeat computation failed: {exc}"}
        result["errors"].append(f"repeat: {exc}")
    try:
        momentum = _momentum(work, comparison) if comparison else {
            "available": False, "reason": "no comparison window available"
        }
    except Exception as exc:  # pragma: no cover - defensive
        momentum = {"available": False, "reason": f"momentum computation failed: {exc}"}
        result["errors"].append(f"momentum: {exc}")

    result["sections"] = {
        "coverage": coverage,
        "comparison": comparison,
        "concentration": concentration,
        "momentum": momentum,
        "margin": margin,
        "discount": discount,
        "repeat": repeat,
        "weekday": weekday,
    }
    result["scenarios"] = _scenarios(
        repeat, concentration, margin, discount, comparison, weekday,
    )
    result["blind_spots"] = _blind_spots(df, schema, result["sections"])
    return result


def _blind_spots(df: pd.DataFrame, schema: dict, sections: dict) -> list[str]:
    """Questions this dataset provably CANNOT answer.

    Stating these explicitly is what removes ambiguity: a reader who is told
    "there is no traffic column, so conversion rate is not knowable here" will
    not read the absence of a conversion number as a conversion problem.
    """
    out: list[str] = []
    cols = " ".join(str(c).lower() for c in df.columns)

    if not sections.get("margin", {}).get("available"):
        reason = sections.get("margin", {}).get("reason") or "no cost or profit column in the data"
        out.append(f"Profitability: {reason}. Every figure in this report is revenue, not profit.")
    if not any(k in cols for k in ("visit", "session", "traffic", "impression", "click", "view")):
        out.append(
            "Conversion rate and traffic: the data contains only completed transactions — "
            "there is no record of visitors who did not buy, so conversion cannot be measured."
        )
    if not any(k in cols for k in ("return", "refund", "cancel")):
        out.append(
            "Returns and refunds: no return/refund/cancellation column, so all revenue figures "
            "are gross of returns."
        )
    if not any(k in cols for k in ("channel", "campaign", "source", "medium", "utm")):
        out.append(
            "Marketing attribution: no channel/campaign column, so revenue cannot be attributed "
            "to a marketing source."
        )
    if not any(k in cols for k in ("currency", "curr_", "iso")):
        out.append(
            "Currency: no currency column — all monetary figures are in the source system's "
            "currency, and mixed-currency rows (if any) would be summed together."
        )
    if not sections.get("coverage", {}).get("available"):
        out.append("Time: no usable date column, so trend, seasonality and period comparison are unavailable.")
    else:
        cov = sections["coverage"]
        if cov.get("months_span", 0) and cov["months_span"] < 13:
            out.append(
                f"Seasonality: the data covers {cov['months_span']} months, less than a full year — "
                "a yearly seasonal pattern cannot be separated from a trend."
            )
    for w in schema.get("warnings", []) or []:
        out.append(f"Schema: {w}")
    return out
