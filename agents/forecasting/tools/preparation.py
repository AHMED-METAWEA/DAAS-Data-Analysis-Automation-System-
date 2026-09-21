"""
Turning a transaction table into a series a model can actually learn from.

This is the layer that decides *what the numbers mean* before any model sees
them, and it is where most real-world forecast error is won or lost:

* **A complete calendar grid.**  Grouping transactions by date silently drops
  every period with no rows.  A daily series with 26 order-free days then
  arrives at the model as 705 "consecutive" points spanning 731 real days, so
  every index-based seasonal model (seasonal-naive, SARIMA, STL, the
  day-of-week profile) is reading a calendar that has been compressed out of
  alignment.  We reindex onto the real grid and fill the holes.

* **Gaps mean different things for different metrics.**  A day with no orders
  has *zero* revenue — but it does not have a zero average order value, it has
  no average order value at all.  Additive metrics are zero-filled; rates are
  interpolated.

* **Partial buckets are dropped.**  Resampling to weeks/months almost always
  leaves a truncated final bucket (data ending mid-week gives a 3-day "week").
  That bucket is not a low period, it is an incomplete one — and feeding it to
  a model produces a forecast anchored to a fictional collapse in demand.

* **Diagnostics travel with the series**, so downstream code can be honest
  about intermittency, sparsity and how much of the series was inferred.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# Metrics that accumulate over a period (sum them per day/week/month) vs. rates
# that should be averaged. "total_price" -> additive (has "total"); "unit_price"
# / "discount_pct" -> rate. Additive tokens win when both appear.
# Separator-free tokens. Avoid greedy fragments like "count"/"order" that also
# appear inside rate names ("dis*count*_pct", "re*order*_rate").
_ADDITIVE_TOKENS = (
    "revenue", "sales", "gmv", "turnover", "amount", "spend", "income", "payment",
    "total", "profit", "cost", "quantity", "qty", "units", "volume",
    "orders", "ordercount", "itemcount", "unitssold", "transactions",
)
_RATE_TOKENS = (
    "price", "rate", "pct", "percent", "ratio", "avg", "average", "mean",
    "aov", "margin", "discount", "unit",
)

# granularity label -> (pandas offset alias, nominal days per period)
#
# Weeks are anchored to Sunday and months to month-start rather than being
# re-anchored to wherever the data happens to end: a fixed anchor keeps bucket
# boundaries identical from run to run, so a forecast stays comparable after new
# rows arrive.  The cost is up to 6 unused trailing days, which the partial
# bucket drop would discard anyway.
FREQ_SPEC = {
    "daily": ("D", 1),
    "weekly": ("W-SUN", 7),
    "monthly": ("MS", 30),
}


def freq_alias(freq_label: str) -> str:
    return FREQ_SPEC.get(freq_label, ("D", 1))[0]


def period_days(freq_label: str) -> int:
    return FREQ_SPEC.get(freq_label, ("D", 1))[1]


def aggregation_for(value_col: str) -> str:
    """Return "sum" for additive metrics, "mean" for rates/ratios."""
    low = value_col.lower().replace("_", "").replace("-", "").replace(" ", "")
    if any(t in low for t in _ADDITIVE_TOKENS):
        return "sum"
    if any(t in low for t in _RATE_TOKENS):
        return "mean"
    return "mean"


@dataclass
class PreparedSeries:
    """A model-ready series plus everything we learned while building it."""

    y: np.ndarray
    dates: pd.DatetimeIndex
    freq_label: str
    pandas_freq: str
    aggregation: str
    period_days: int
    filled_periods: int = 0
    dropped_partial: list[str] = field(default_factory=list)
    zero_share: float = 0.0
    intermittent: bool = False
    constant: bool = False
    all_positive: bool = True
    notes: list[str] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.y)

    @property
    def fill_share(self) -> float:
        return self.filled_periods / self.n if self.n else 0.0

    def to_dict(self) -> dict:
        """Diagnostics only — safe to serialise into API/report payloads."""
        return {
            "granularity": self.freq_label,
            "periods": self.n,
            "aggregation": self.aggregation,
            "filled_periods": self.filled_periods,
            "fill_share": round(self.fill_share, 4),
            "dropped_partial": list(self.dropped_partial),
            "zero_share": round(self.zero_share, 4),
            "intermittent": self.intermittent,
            "constant": self.constant,
            "notes": list(self.notes),
        }


def _bucket_bounds(start: pd.Timestamp, freq_label: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Inclusive [first day, last day] of the bucket labelled ``start``."""
    if freq_label == "daily":
        return start.normalize(), start.normalize()
    if freq_label == "weekly":
        # "W-SUN" labels a bucket by the Sunday it *ends* on.
        return (start - pd.Timedelta(days=6)).normalize(), start.normalize()
    # "MS" labels a bucket by the first day of the month.
    return start.normalize(), (start + pd.offsets.MonthEnd(0)).normalize()


def _drop_partial_buckets(
    s: pd.Series, freq_label: str, raw_min: pd.Timestamp, raw_max: pd.Timestamp
) -> tuple[pd.Series, list[str]]:
    """Drop leading/trailing buckets the raw data does not fully cover.

    A week bucket built from 3 days of data is not a bad week, it is an
    unfinished one.  Left in, it reads as a ~60% collapse in demand right at the
    point the model cares about most — the end of the series — and every model
    that anchors on the last value (naive, drift, ETS, ARIMA) propagates that
    fiction across the whole horizon.
    """
    dropped: list[str] = []
    if freq_label == "daily" or s.empty:
        # A day bucket is complete by construction: we group by calendar date,
        # so any observed day is "fully covered" as far as we can tell without
        # intra-day timestamps.
        return s, dropped

    while len(s) > 0:
        lo, hi = _bucket_bounds(s.index[0], freq_label)
        if lo >= raw_min.normalize():
            break
        s = s.iloc[1:]
        dropped.append("leading")

    while len(s) > 0:
        lo, hi = _bucket_bounds(s.index[-1], freq_label)
        if hi <= raw_max.normalize():
            break
        s = s.iloc[:-1]
        dropped.append("trailing")

    return s, dropped


def _detect_intermittent(y: np.ndarray) -> bool:
    """Croston's criterion: mean interval between non-zero demands > 1.32.

    Intermittent series (spare parts, long-tail SKUs, low-traffic days) need
    demand-rate methods rather than level/trend models, and percentage error
    metrics are meaningless on them.
    """
    nz = np.flatnonzero(np.asarray(y, dtype=float) != 0)
    if len(nz) < 2:
        return True
    return float(np.mean(np.diff(nz))) > 1.32


def build_series(
    df: pd.DataFrame,
    date_col: str,
    value_col: str,
    freq_label: str = "daily",
    agg: str | None = None,
) -> PreparedSeries:
    """Collapse a transaction table into a clean, gap-free series at ``freq_label``.

    Additive metrics (revenue, quantity, profit, …) are **summed** per period;
    rates (unit price, discount %) are **averaged**.  The result is always on a
    complete, evenly-spaced calendar grid with no partial end buckets.
    """
    pandas_freq, pdays = FREQ_SPEC.get(freq_label, ("D", 1))
    aggregation = agg or aggregation_for(value_col)
    notes: list[str] = []

    work = pd.DataFrame({
        "ds": pd.to_datetime(df[date_col], errors="coerce"),
        "y": pd.to_numeric(df[value_col], errors="coerce"),
    }).dropna(subset=["ds", "y"])

    if work.empty:
        return PreparedSeries(
            y=np.asarray([], dtype=float), dates=pd.DatetimeIndex([]),
            freq_label=freq_label, pandas_freq=pandas_freq, aggregation=aggregation,
            period_days=pdays, notes=["No usable (date, value) pairs."],
        )

    raw_min, raw_max = work["ds"].min(), work["ds"].max()
    grouped = work.set_index("ds")["y"].sort_index().resample(pandas_freq)
    s = grouped.agg(aggregation)

    # `resample` lays down a complete grid, but it does *not* mark empty periods
    # consistently: `.sum()` reports an empty bucket as 0.0 while `.mean()`
    # reports NaN. Row counts are the only reliable way to tell "no records
    # here" from "genuinely zero", so the record count drives the fill logic.
    counts = grouped.size()
    observed_mask = counts.reindex(s.index, fill_value=0) > 0
    s, dropped = _drop_partial_buckets(s, freq_label, raw_min, raw_max)
    observed_mask = observed_mask.reindex(s.index, fill_value=False)

    if dropped:
        kinds = sorted(set(dropped))
        notes.append(
            f"Dropped {len(dropped)} incomplete {'/'.join(kinds)} "
            f"{freq_label[:-2] if freq_label.endswith('ly') else freq_label} bucket(s) "
            f"not fully covered by the data."
        )

    filled = int((~observed_mask).sum())
    if filled:
        if aggregation == "sum":
            # No transactions in the period genuinely means zero of an additive
            # quantity — zero revenue, zero units.
            s = s.fillna(0.0)
            notes.append(f"{filled} period(s) with no records treated as 0 (additive metric).")
        else:
            # A rate is undefined, not zero, when nothing happened: an empty
            # bucket has no average order value, it does not have one of zero.
            s = s.where(observed_mask).interpolate(limit_direction="both")
            notes.append(f"{filled} period(s) with no records interpolated (rate metric).")

    s = s.dropna()
    y = s.to_numpy(dtype=float)
    n = len(y)

    zero_share = float(np.mean(y == 0)) if n else 0.0
    prepared = PreparedSeries(
        y=y,
        dates=pd.DatetimeIndex(s.index),
        freq_label=freq_label,
        pandas_freq=pandas_freq,
        aggregation=aggregation,
        period_days=pdays,
        filled_periods=filled,
        dropped_partial=sorted(set(dropped)),
        zero_share=zero_share,
        intermittent=_detect_intermittent(y) if n else True,
        constant=bool(n and np.allclose(y, y[0])),
        all_positive=bool(n and np.all(y > 0)),
        notes=notes,
    )

    if prepared.fill_share > 0.4:
        prepared.notes.append(
            f"{prepared.fill_share:.0%} of periods had no records — this series is sparse "
            f"at {freq_label} granularity; a coarser granularity will be more reliable."
        )
    if prepared.intermittent and n:
        prepared.notes.append(
            "Demand is intermittent (frequent zero periods); percentage errors are "
            "unreliable here and demand-rate models are preferred."
        )

    return prepared


def prepare_data(
    df: pd.DataFrame, date_col: str, value_col: str, rule: str | None = None
) -> pd.DataFrame:
    """Backwards-compatible ``(ds, y)`` frame used by existing callers/tests.

    ``rule`` is the legacy pandas alias ("W", "MS", ``None`` for native daily);
    it is mapped onto the granularity labels :func:`build_series` understands.
    """
    label = {None: "daily", "D": "daily", "W": "weekly", "W-SUN": "weekly",
             "M": "monthly", "MS": "monthly", "ME": "monthly"}.get(rule, "daily")
    prepared = build_series(df, date_col, value_col, label)
    return pd.DataFrame({"ds": prepared.dates, "y": prepared.y})
