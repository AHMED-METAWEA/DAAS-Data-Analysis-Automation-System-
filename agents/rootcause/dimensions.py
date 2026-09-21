"""Which columns are worth slicing by — and, just as importantly, which are not
and why.

The search cost is driven almost entirely by this decision.  A 40-column table
where every column is treated as a dimension produces a lattice with more nodes
than there are rows; the same table with the eight real business dimensions
identified produces a search that finishes in under a second.  So the filtering
here is not tidying-up, it is the first and cheapest pruning step.

Every rejection is recorded with its reason and published in the result, because
a drill-down that silently ignored ``region`` would produce an answer that reads
as complete while being blind to the dimension that mattered.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from agents.analytics.schema_intel import build_schema_summary

# A dimension with more distinct values than this is not a business dimension,
# it is an identifier (order id, email, row hash). Slicing by it produces one
# row per value and explains nothing.
MAX_CARDINALITY = 300
# Even below the absolute cap, a column whose values are nearly unique for the
# rows present is an identifier in disguise.
MAX_UNIQUE_RATIO = 0.5
# One value is not a dimension: it cannot discriminate anything.
MIN_CARDINALITY = 2
# Numeric columns are only treated as categorical when they are plainly codes
# (store_id, rating, size) rather than measurements.
MAX_NUMERIC_CARDINALITY = 50

# Roles that are the measure, the clock or the key — never a slicing dimension.
_NEVER_DIMENSION_ROLES = ("time_column", "order_column")


@dataclass(frozen=True)
class Dimension:
    """A column the search may slice on."""

    name: str
    source: str          # "column" | "derived"
    cardinality: int
    kind: str            # "categorical" | "numeric_code" | "temporal"
    role: str = ""       # product / category / customer / channel … when known

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "source": self.source,
            "cardinality": self.cardinality, "kind": self.kind, "role": self.role,
        }


def _is_text_like(series: pd.Series) -> bool:
    dtype = series.dtype
    return (
        dtype == object  # noqa: E721 - numpy dtype equality, not a type() comparison
        or pd.api.types.is_string_dtype(series)
        or isinstance(dtype, pd.CategoricalDtype)
        or pd.api.types.is_bool_dtype(series)
    )


def _role_of(name: str, schema: dict) -> str:
    if name == schema.get("product_column"):
        return "product"
    if name == schema.get("customer_column"):
        return "customer"
    if name in (schema.get("category_columns") or []):
        return "category"
    return ""


def detect_dimensions(
    df: pd.DataFrame,
    *,
    schema: dict | None = None,
    exclude: set[str] | None = None,
    max_cardinality: int = MAX_CARDINALITY,
    include_weekday: bool = False,
    time_column: str | None = None,
) -> tuple[list[Dimension], list[dict[str, str]]]:
    """Return ``(dimensions, skipped)`` for *df*.

    ``skipped`` carries one ``{"column": …, "reason": …}`` per rejected column so
    the caller can publish exactly what the search did not look at.
    """
    schema = schema or build_schema_summary(df)
    exclude = set(exclude or set())

    monetary = set(schema.get("monetary_columns") or [])
    for role in _NEVER_DIMENSION_ROLES:
        col = schema.get(role)
        if col:
            exclude.add(col)
    qty = schema.get("quantity_column")
    if qty:
        exclude.add(qty)
    if time_column:
        exclude.add(time_column)

    rows = len(df)
    dimensions: list[Dimension] = []
    skipped: list[dict[str, str]] = []

    for raw in df.columns:
        col = str(raw)
        if col.startswith("_"):
            # Internal working columns added by the engine (_rev, _dt, …).
            continue
        if col in exclude:
            skipped.append({"column": col, "reason": "it is the date, the measure, the quantity or the order key"})
            continue
        series = df[raw]
        if col in monetary or pd.api.types.is_float_dtype(series):
            skipped.append({"column": col, "reason": "it is a continuous/monetary measurement, not a category"})
            continue
        if pd.api.types.is_datetime64_any_dtype(series):
            skipped.append({"column": col, "reason": "it is a timestamp; time is the comparison axis, not a slice"})
            continue

        nunique = int(series.nunique(dropna=True))
        if nunique < MIN_CARDINALITY:
            skipped.append({"column": col, "reason": f"only {nunique} distinct value(s) — nothing to compare"})
            continue

        numeric = pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series)
        if numeric:
            if nunique > MAX_NUMERIC_CARDINALITY:
                skipped.append({
                    "column": col,
                    "reason": f"numeric with {nunique:,} distinct values — read as a measurement, not a code",
                })
                continue
            kind = "numeric_code"
        elif _is_text_like(series):
            kind = "categorical"
        else:
            skipped.append({"column": col, "reason": f"unsupported dtype '{series.dtype}'"})
            continue

        if nunique > max_cardinality:
            skipped.append({
                "column": col,
                "reason": f"{nunique:,} distinct values exceeds the {max_cardinality:,} cap — treated as an identifier",
            })
            continue
        if rows and nunique / rows > MAX_UNIQUE_RATIO:
            skipped.append({
                "column": col,
                "reason": f"{nunique:,} distinct values across {rows:,} rows — nearly unique, so it is an identifier",
            })
            continue

        dimensions.append(Dimension(
            name=col, source="column", cardinality=nunique, kind=kind,
            role=_role_of(col, schema),
        ))

    # Weekday is available as a dimension but is OFF by default, and the reason
    # is a correctness one rather than a performance one.
    #
    # Every other dimension has the same *exposure* in both periods: April and
    # May both contain all four regions. Weekday does not. April 2025 has five
    # Tuesdays and May 2025 has four, so a flat business shows "Tuesday explains
    # 92% of the decline" — a calendar artefact presented as a root cause, and a
    # spectacularly convincing one. Enabling it is a deliberate choice, made
    # after reading the imbalance the caller is shown alongside it.
    if time_column and time_column in df.columns:
        parsed = pd.to_datetime(df[time_column], errors="coerce")
        if not parsed.notna().any():
            pass
        elif include_weekday:
            dimensions.append(Dimension(
                name="weekday", source="derived", cardinality=7, kind="temporal", role="time",
            ))
        else:
            skipped.append({
                "column": "weekday",
                "reason": (
                    "derived dimension, off by default: two periods rarely contain the same "
                    "number of each weekday, so a weekday can 'explain' a change it did not "
                    "cause. Enable it deliberately if you are comparing equal-length, "
                    "week-aligned periods."
                ),
            })

    # Fewest values first: a 4-value dimension gives a more concise explanation
    # than a 200-value one at the same explanatory power, and evaluating it first
    # raises the pruning floor sooner for everything after it.
    dimensions.sort(key=lambda d: (d.cardinality, d.name))
    return dimensions, skipped


def materialise(df: pd.DataFrame, dimension: Dimension, time_column: str | None) -> pd.Series:
    """The slicing key for *dimension*, as a string series aligned to ``df.index``.

    Values are stringified once here so every downstream comparison, group-by
    and rendered label uses the same representation — an integer store code
    grouped as ``7`` and rendered as ``7.0`` would read as two different stores.
    """
    if dimension.source == "derived" and dimension.name == "weekday":
        parsed = pd.to_datetime(df[time_column], errors="coerce")
        return parsed.dt.day_name().fillna("(unknown)").astype(str)
    series = df[dimension.name]
    if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
        # Integer-like codes must not pick up a ".0" suffix.
        series = series.astype("Int64").astype(object)
    return series.astype(object).where(series.notna(), "(missing)").astype(str)
