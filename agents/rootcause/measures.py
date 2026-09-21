"""What is being explained, and over which two periods.

Two things have to be pinned down before a drill-down means anything:

* **The measure.**  Contribution analysis decomposes a *sum*.  Revenue and units
  are additive over rows, so the parts provably add up to the whole.  Order and
  customer counts are distinct-counts: one order can span three products, so
  slicing by product double-counts it.  Both are offered — because "which
  segment lost customers?" is a real question — but the non-additive ones are
  flagged, and the engine refuses to claim their parts sum to the total.

* **The window.**  A drill-down compared against the wrong baseline explains a
  change that never happened.  The windows here are the *same* ones the insights
  report uses (last complete calendar month first, rolling 30 days second), so a
  finding in the report and the drill-down that explains it are always measured
  over the identical period.  Anything else and the two pages contradict each
  other in front of the user.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from agents.analytics.kpi_engine import line_revenue
from agents.insights.decision_metrics import derive_line_profit
from tools.localize import format_month

# Mirrors decision_metrics._ROLLING_WINDOW_DAYS — the two must stay equal or the
# insights headline and its drill-down would cover different spans.
ROLLING_WINDOW_DAYS = 30


class MeasureUnavailableError(ValueError):
    """The requested measure cannot be computed from this dataset."""


# Measure and window labels are not decoration: they are substituted into the
# narrative through the figure registry, so an English label lands inside an
# Arabic sentence — "{{rc_measure}} انخفض" rendering as "Revenue انخفض". They
# are localised here, where they are created, so the registry, the API response
# and the UI all agree without any of them having to re-translate.
_MEASURE_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "revenue": "Revenue", "units": "Units sold", "gross_profit": "Gross profit",
        "orders": "Orders", "customers": "Active customers",
        "transactions": "Transaction lines",
    },
    "ar": {
        "revenue": "الإيراد", "units": "الوحدات المُباعة", "gross_profit": "إجمالي الربح",
        "orders": "الطلبات", "customers": "العملاء النشطون",
        "transactions": "سطور المعاملات",
    },
}

_WINDOW_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "selected": "the selected period", "baseline": "the baseline period",
        "last_days": "last {days} days", "previous_days": "previous {days} days",
        "first_half": "the first half", "second_half": "the second half",
    },
    "ar": {
        "selected": "الفترة المحدّدة", "baseline": "فترة المقارنة",
        "last_days": "آخر {days} يومًا", "previous_days": "الـ {days} يومًا السابقة",
        "first_half": "النصف الأول", "second_half": "النصف الثاني",
    },
}


def _label_for(language: str):
    table = _MEASURE_LABELS.get(language) or _MEASURE_LABELS["en"]
    return lambda key: table.get(key) or _MEASURE_LABELS["en"][key]


def _window_label(language: str, key: str, **kwargs) -> str:
    table = _WINDOW_LABELS.get(language) or _WINDOW_LABELS["en"]
    template = table.get(key) or _WINDOW_LABELS["en"][key]
    return template.format(**kwargs)


@dataclass
class Measure:
    """A quantity whose change is being attributed to slices of the business."""

    key: str
    label: str
    unit: str                      # currency | count
    additive: bool
    basis: str
    # Exactly one of these is set. ``values`` is summed per slice; ``distinct``
    # is counted-distinct per slice.
    values: pd.Series | None = None
    distinct: pd.Series | None = None

    def aggregate(self, mask: pd.Series | None = None) -> float:
        if self.values is not None:
            series = self.values if mask is None else self.values[mask]
            return float(series.sum())
        series = self.distinct if mask is None else self.distinct[mask]
        return float(series.nunique())


def available_measures(
    df: pd.DataFrame, schema: dict, language: str = "en",
) -> list[dict[str, str | bool]]:
    """The measures this dataset actually supports, for the UI's picker."""
    label = _label_for(language)
    out: list[dict[str, str | bool]] = []
    monetary = schema.get("monetary_columns") or []
    if monetary:
        out.append({"key": "revenue", "label": label("revenue"), "unit": "currency", "additive": True})
    if schema.get("quantity_column"):
        out.append({"key": "units", "label": label("units"), "unit": "count", "additive": True})
    if monetary:
        rev, _ = line_revenue(df, monetary[0], schema.get("quantity_column"))
        qty = (
            pd.to_numeric(df[schema["quantity_column"]], errors="coerce").fillna(0.0)
            if schema.get("quantity_column") else None
        )
        profit, _, _ = derive_line_profit(df, rev, qty, monetary[0])
        if profit is not None:
            out.append({"key": "gross_profit", "label": label("gross_profit"), "unit": "currency", "additive": True})
    if schema.get("order_column"):
        out.append({"key": "orders", "label": label("orders"), "unit": "count", "additive": False})
    if schema.get("customer_column"):
        out.append({"key": "customers", "label": label("customers"), "unit": "count", "additive": False})
    out.append({"key": "transactions", "label": label("transactions"), "unit": "count", "additive": True})
    return out


def build_measure(df: pd.DataFrame, key: str, schema: dict, language: str = "en") -> Measure:
    """Materialise *key* as a per-row series (or distinct key) over ``df``."""
    monetary = schema.get("monetary_columns") or []
    revenue_col = monetary[0] if monetary else None
    qty_col = schema.get("quantity_column")

    label = _label_for(language)

    if key == "revenue":
        if revenue_col is None:
            raise MeasureUnavailableError(
                "No monetary column was detected, so revenue cannot be attributed to anything."
            )
        series, basis = line_revenue(df, revenue_col, qty_col)
        return Measure(
            key="revenue", label=label("revenue"), unit="currency", additive=True,
            basis=f"sum of '{revenue_col}' — {basis}",
            values=pd.to_numeric(series, errors="coerce").fillna(0.0),
        )

    if key == "units":
        if not qty_col:
            raise MeasureUnavailableError("No quantity column was detected, so units cannot be attributed.")
        return Measure(
            key="units", label=label("units"), unit="count", additive=True,
            basis=f"sum of '{qty_col}'",
            values=pd.to_numeric(df[qty_col], errors="coerce").fillna(0.0),
        )

    if key == "gross_profit":
        if revenue_col is None:
            raise MeasureUnavailableError("No monetary column was detected, so gross profit is unavailable.")
        rev, _ = line_revenue(df, revenue_col, qty_col)
        qty = pd.to_numeric(df[qty_col], errors="coerce").fillna(0.0) if qty_col else None
        profit, basis, _ = derive_line_profit(df, rev, qty, revenue_col)
        if profit is None:
            raise MeasureUnavailableError(f"Gross profit is not available: {basis}")
        return Measure(
            key="gross_profit", label=label("gross_profit"), unit="currency", additive=True,
            basis=basis, values=pd.to_numeric(profit, errors="coerce").fillna(0.0),
        )

    if key == "orders":
        order_col = schema.get("order_column")
        if not order_col:
            raise MeasureUnavailableError("No order id column was detected, so orders cannot be counted.")
        return Measure(
            key="orders", label=label("orders"), unit="count", additive=False,
            basis=f"distinct values of '{order_col}'", distinct=df[order_col].astype(str),
        )

    if key == "customers":
        customer_col = schema.get("customer_column")
        if not customer_col:
            raise MeasureUnavailableError("No customer column was detected, so customers cannot be counted.")
        return Measure(
            key="customers", label=label("customers"), unit="count", additive=False,
            basis=f"distinct values of '{customer_col}'", distinct=df[customer_col].astype(str),
        )

    if key == "transactions":
        return Measure(
            key="transactions", label=label("transactions"), unit="count", additive=True,
            basis="count of rows", values=pd.Series(1.0, index=df.index),
        )

    raise MeasureUnavailableError(f"Unknown measure '{key}'.")


# ── Comparison windows ──────────────────────────────────────────────────────

@dataclass
class WindowSpec:
    label: str
    start: pd.Timestamp
    end: pd.Timestamp

    def mask(self, dt: pd.Series) -> pd.Series:
        # end is inclusive to the last instant of the day, so a timestamped
        # dataset does not silently drop the final day's afternoon.
        return (dt >= self.start.normalize()) & (
            dt < self.end.normalize() + pd.Timedelta(days=1)
        )


@dataclass
class WindowPair:
    current: WindowSpec
    prior: WindowSpec
    basis: str
    comparable: bool = True
    caveat: str = ""


def resolve_windows(
    dt: pd.Series,
    *,
    mode: str = "auto",
    current_start: str | None = None,
    current_end: str | None = None,
    prior_start: str | None = None,
    prior_end: str | None = None,
    language: str = "en",
) -> WindowPair:
    """Pick the two periods to compare.

    ``mode`` is ``auto`` (calendar month, else rolling 30 days, else halves),
    ``last_month``, ``rolling_30d`` or ``custom``.  ``auto`` deliberately
    prefers the calendar month: it is the baseline an owner already carries in
    their head, and it is the window the insights report leads with.
    """
    valid = dt.dropna()
    if valid.empty:
        raise MeasureUnavailableError("No parseable dates, so two periods cannot be compared.")
    first, last = valid.min(), valid.max()

    if mode == "custom":
        missing = [
            name for name, value in (
                ("current_start", current_start), ("current_end", current_end),
                ("prior_start", prior_start), ("prior_end", prior_end),
            ) if not value
        ]
        if missing:
            raise MeasureUnavailableError(
                f"A custom comparison needs all four dates; missing: {', '.join(missing)}."
            )
        cur = WindowSpec(_window_label(language, "selected"),
                         pd.Timestamp(current_start), pd.Timestamp(current_end))
        pri = WindowSpec(_window_label(language, "baseline"),
                         pd.Timestamp(prior_start), pd.Timestamp(prior_end))
        if cur.start > cur.end or pri.start > pri.end:
            raise MeasureUnavailableError("Each period's start date must not be after its end date.")
        return WindowPair(current=cur, prior=pri, basis="custom", comparable=True)

    def month_pair() -> WindowPair | None:
        month_end_of_last = (last.normalize() + pd.offsets.MonthEnd(0)).normalize()
        if last.normalize() < month_end_of_last:
            # The final month is truncated — step back to the last complete one
            # so a data cut-off is never reported as a collapse.
            cur_end = (last.normalize() - pd.offsets.MonthBegin(1)).normalize() - pd.Timedelta(days=1)
        else:
            cur_end = month_end_of_last
        cur_start = cur_end.replace(day=1)
        pri_end = cur_start - pd.Timedelta(days=1)
        pri_start = pri_end.replace(day=1)
        if first.normalize() > pri_start or cur_start > last.normalize():
            return None
        return WindowPair(
            current=WindowSpec(format_month(cur_start, language), cur_start, cur_end),
            prior=WindowSpec(format_month(pri_start, language), pri_start, pri_end),
            basis="last_month",
        )

    def rolling_pair() -> WindowPair | None:
        cur_start = last.normalize() - pd.Timedelta(days=ROLLING_WINDOW_DAYS - 1)
        pri_end = cur_start - pd.Timedelta(days=1)
        pri_start = pri_end - pd.Timedelta(days=ROLLING_WINDOW_DAYS - 1)
        if first.normalize() > pri_start:
            return None
        return WindowPair(
            current=WindowSpec(_window_label(language, "last_days", days=ROLLING_WINDOW_DAYS),
                               cur_start, last.normalize()),
            prior=WindowSpec(_window_label(language, "previous_days", days=ROLLING_WINDOW_DAYS),
                             pri_start, pri_end),
            basis="rolling_30d",
        )

    if mode == "last_month":
        pair = month_pair()
        if pair is None:
            raise MeasureUnavailableError(
                "The data does not fully cover two consecutive calendar months."
            )
        return pair
    if mode == "rolling_30d":
        pair = rolling_pair()
        if pair is None:
            raise MeasureUnavailableError(
                f"The data does not cover {ROLLING_WINDOW_DAYS * 2} days, so a rolling "
                f"{ROLLING_WINDOW_DAYS}-day comparison is not possible."
            )
        return pair

    for candidate in (month_pair(), rolling_pair()):
        if candidate is not None:
            return candidate

    # Last resort: split the covered span down the middle. Labelled explicitly,
    # because "first half vs second half" is a weaker claim than "May vs April"
    # and the reader has to be able to tell which one they are looking at.
    span_days = int((last.normalize() - first.normalize()).days) + 1
    if span_days < 4:
        raise MeasureUnavailableError(
            f"The data covers only {span_days} day(s) — too short to compare two periods."
        )
    half = span_days // 2
    pri_start = first.normalize()
    pri_end = pri_start + pd.Timedelta(days=half - 1)
    cur_start = pri_end + pd.Timedelta(days=1)
    return WindowPair(
        current=WindowSpec(_window_label(language, "second_half"), cur_start, last.normalize()),
        prior=WindowSpec(_window_label(language, "first_half"), pri_start, pri_end),
        basis="halves",
        caveat=(
            f"The data covers {span_days} days — not two full calendar months and not "
            f"{ROLLING_WINDOW_DAYS * 2} days — so the comparison splits the period in half "
            "instead. Treat it as directional."
        ),
    )
