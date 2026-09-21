"""Independent, per-table primary-key detection.

Deliberately has no dependency on ``relationships/`` or cross-table scoring
(``heuristics.py::score_candidate``) — that module decides which columns are
*probably related* across two tables, gated by a lenient 0.5 uniqueness floor,
which is the right signal for relationship review but the wrong one for
deciding what becomes a real ``PRIMARY KEY`` constraint. This module answers a
narrower, stricter question for every table on its own: does any column (or
small column combination) actually behave like a primary key in this data?

A column only becomes a **confirmed** primary key if it satisfies every
requirement: 100% uniqueness among non-null values, zero nulls, and a stable
(non-mixed) representation. Anything short of that is a **candidate** —
returned with the exact evidence (duplicate/null counts and a sample of the
offending values) so it can be surfaced for human review instead of silently
becoming a constraint. A table that is never referenced by any other table
still gets evaluated here, unlike the old relationship-derived approach.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field

CONFIRMED_UNIQUENESS = 1.0
CANDIDATE_UNIQUENESS_FLOOR = 0.5
_KEY_SUFFIXES = ("_id", "_fk", "_ref")
_MAX_COMPOSITE_CANDIDATE_COLUMNS = 6
_SAMPLE_LIMIT = 10
# Only attempt composite-key search when no single column even comes close to
# unique. A column already at/above this is almost certainly the intended
# single natural key with a handful of dirty duplicate rows (e.g. the same
# customer_id entered twice) — brute-force composite search would otherwise
# find some other column that happens to differ on exactly those duplicate
# rows and "confirm" a spurious multi-column key, silently masking a real
# data-quality problem instead of surfacing it as a candidate to fix.
_COMPOSITE_SEARCH_CEILING = 0.9


class PrimaryKeyDetectionResult(BaseModel):
    """Outcome of primary-key detection for one table."""

    table: str
    status: Literal["confirmed", "candidate", "none"] = "none"
    columns: list[str] = Field(default_factory=list)
    is_composite: bool = False
    uniqueness: float = 0.0
    null_count: int = 0
    duplicate_count: int = 0
    duplicate_sample: list[str] = Field(default_factory=list)
    reason: str = ""


def _looks_like_key(col: str) -> bool:
    low = str(col).strip().lower()
    return low == "id" or any(low.endswith(suf) for suf in _KEY_SUFFIXES)


def _is_numeric_like_value(v: object) -> bool:
    try:
        float(str(v).strip())
        return True
    except (ValueError, TypeError):
        return False


def _is_dtype_stable(vals: pd.Series) -> bool:
    """False for an object column mixing numeric-looking and non-numeric-looking
    values (e.g. ``1001`` and ``"CUST-1001"``) — a 100% "unique" count on a
    column like that isn't trustworthy, since two differently-typed
    representations of the same logical key would count as distinct.
    """
    if (
        pd.api.types.is_numeric_dtype(vals)
        or pd.api.types.is_bool_dtype(vals)
        or pd.api.types.is_datetime64_any_dtype(vals)
    ):
        return True
    looks_numeric = vals.map(_is_numeric_like_value)
    return bool(looks_numeric.all() or not looks_numeric.any())


def _uniqueness_only(series: pd.Series) -> float:
    vals = series.dropna()
    if vals.empty:
        return 0.0
    return vals.nunique() / len(vals)


def _duplicate_values(series: pd.Series) -> tuple[int, list[str]]:
    dupe_mask = series.duplicated(keep=False) & series.notna()
    count = int(dupe_mask.sum())
    if count == 0:
        return 0, []
    sample = sorted(series.loc[dupe_mask].astype(str).unique())[:_SAMPLE_LIMIT]
    return count, sample


@dataclass
class _ColumnStats:
    name: str
    order: int
    uniqueness: float
    null_count: int
    dtype_stable: bool
    key_like: bool
    duplicate_count: int
    duplicate_sample: list[str]


def _column_stats(df: pd.DataFrame) -> list[_ColumnStats]:
    stats: list[_ColumnStats] = []
    for order, col in enumerate(df.columns):
        series = df[col]
        vals = series.dropna()
        if vals.empty:
            continue
        dup_count, dup_sample = _duplicate_values(series)
        stats.append(
            _ColumnStats(
                name=str(col),
                order=order,
                uniqueness=vals.nunique() / len(vals),
                null_count=int(series.isna().sum()),
                dtype_stable=_is_dtype_stable(vals),
                key_like=_looks_like_key(col),
                duplicate_count=dup_count,
                duplicate_sample=dup_sample,
            )
        )
    return stats


def _pick_best(stats: list[_ColumnStats]) -> _ColumnStats:
    """Key-like naming wins first, then highest uniqueness, then fewer
    nulls, then original column order (earliest wins) for determinism.

    Naming is checked before raw uniqueness so the report points a human
    at the column they'd actually expect to fix (e.g. ``order_id``) rather
    than whichever column coincidentally has slightly fewer duplicate
    values (e.g. a price column, which is "more unique" only by accident
    of having more distinct numeric values, not because it's meant to be
    a key) — every candidate here has already cleared
    ``CANDIDATE_UNIQUENESS_FLOOR``, so this is a tiebreak among plausible
    options, not a way to let a low-uniqueness column win outright.
    """
    return max(
        stats,
        key=lambda s: (s.key_like, s.uniqueness, -s.null_count, -s.order),
    )


def detect_single_column_pk(df: pd.DataFrame) -> PrimaryKeyDetectionResult | None:
    """Best single-column result: a confirmed PK if any column is 100% unique,
    non-null, and dtype-stable; otherwise the best candidate at or above
    ``CANDIDATE_UNIQUENESS_FLOOR``; otherwise ``None``.
    """
    if df is None or df.empty or len(df.columns) == 0:
        return None

    stats = _column_stats(df)
    if not stats:
        return None

    confirmed = [
        s for s in stats
        if s.uniqueness == CONFIRMED_UNIQUENESS and s.null_count == 0 and s.dtype_stable
    ]
    if confirmed:
        best = _pick_best(confirmed)
        return PrimaryKeyDetectionResult(
            table="",
            status="confirmed",
            columns=[best.name],
            is_composite=False,
            uniqueness=best.uniqueness,
            null_count=best.null_count,
            duplicate_count=0,
            duplicate_sample=[],
            reason=f"Column '{best.name}' is 100% unique with no null values.",
        )

    candidates = [s for s in stats if s.uniqueness >= CANDIDATE_UNIQUENESS_FLOOR]
    if not candidates:
        return None
    best = _pick_best(candidates)
    return PrimaryKeyDetectionResult(
        table="",
        status="candidate",
        columns=[best.name],
        is_composite=False,
        uniqueness=round(best.uniqueness, 4),
        null_count=best.null_count,
        duplicate_count=best.duplicate_count,
        duplicate_sample=best.duplicate_sample,
        reason=(
            f"Column '{best.name}' is only {best.uniqueness:.1%} unique "
            f"({best.duplicate_count} row(s) share a duplicate value"
            + (f", {best.null_count} null" if best.null_count else "")
            + ") — not unique/complete enough to confirm as a primary key."
        ),
    )


def detect_composite_pk(df: pd.DataFrame) -> PrimaryKeyDetectionResult | None:
    """Search 2-column combinations of key-like-named columns (extended with
    the highest-uniqueness remaining columns, bounded by
    ``_MAX_COMPOSITE_CANDIDATE_COLUMNS``) for a pair that is 100% unique
    together with no nulls in either column — covers junction-table patterns
    like ``order_id`` + ``line_number``. Only ever returns a confirmed result;
    there is no "candidate composite" concept.
    """
    if df is None or df.empty or len(df.columns) < 2:
        return None

    columns = list(df.columns)
    key_like_cols = [c for c in columns if _looks_like_key(c)]
    remaining = [c for c in columns if c not in key_like_cols]
    remaining_ranked = sorted(remaining, key=lambda c: _uniqueness_only(df[c]), reverse=True)
    candidate_cols = (key_like_cols + remaining_ranked)[:_MAX_COMPOSITE_CANDIDATE_COLUMNS]
    if len(candidate_cols) < 2:
        return None

    for i in range(len(candidate_cols)):
        for j in range(i + 1, len(candidate_cols)):
            col_a, col_b = candidate_cols[i], candidate_cols[j]
            sub = df[[col_a, col_b]]
            if sub.isna().any().any():
                continue
            if sub.duplicated(keep=False).any():
                continue
            return PrimaryKeyDetectionResult(
                table="",
                status="confirmed",
                columns=[str(col_a), str(col_b)],
                is_composite=True,
                uniqueness=1.0,
                null_count=0,
                duplicate_count=0,
                duplicate_sample=[],
                reason=(
                    f"Columns '{col_a}' + '{col_b}' are 100% unique together "
                    "with no nulls (composite key)."
                ),
            )
    return None


def detect_primary_key(table_name: str, df: pd.DataFrame) -> PrimaryKeyDetectionResult:
    """Authoritative per-table primary-key detection — the single source of
    truth for what Constraint Generation is allowed to turn into a real
    ``PRIMARY KEY``. Independent of any cross-table relationship.
    """
    if df is None or df.empty or len(df.columns) == 0:
        return PrimaryKeyDetectionResult(table=table_name, status="none", reason="Table is empty.")

    single = detect_single_column_pk(df)
    if single is not None and single.status == "confirmed":
        return single.model_copy(update={"table": table_name})

    if single is None or single.uniqueness < _COMPOSITE_SEARCH_CEILING:
        composite = detect_composite_pk(df)
        if composite is not None:
            return composite.model_copy(update={"table": table_name})

    if single is not None:
        return single.model_copy(update={"table": table_name})

    return PrimaryKeyDetectionResult(
        table=table_name,
        status="none",
        reason=(
            "No single column or 2-column combination met the minimum "
            "uniqueness threshold for primary-key candidacy."
        ),
    )


def detect_primary_keys(tables: dict[str, pd.DataFrame]) -> dict[str, PrimaryKeyDetectionResult]:
    return {name: detect_primary_key(name, df) for name, df in tables.items()}


def confirmed_primary_keys(
    results: dict[str, PrimaryKeyDetectionResult],
) -> dict[str, list[str]]:
    """table -> primary-key column list, confirmed tables only — the map
    Constraint Generation (``db.ddl.generate_schema_ddl``) consumes.
    """
    return {name: r.columns for name, r in results.items() if r.status == "confirmed"}
