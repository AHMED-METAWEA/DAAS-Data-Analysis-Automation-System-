"""Deterministic (no-LLM) cross-table relationship detection.

Generalizes the single-shot star-schema heuristic in
``tools.ingestion._key_candidates`` (which only tries exact, case-insensitive
column-name matches between one fixed fact/dimension pair) into a scored,
any-pair-of-tables detector:

  - fuzzy column-name similarity (``difflib``, stdlib — no new dependency)
  - naming suffix patterns (``_id``, ``_fk``, ``_ref``, bare ``id``)
  - datatype compatibility
  - uniqueness ratio (primary-key candidacy) on whichever side is more unique
  - foreign-key coverage (fraction of the other side's values found in it)

Each candidate gets a confidence score in [0, 1]. Only the single best-scoring
column pair per table-pair is kept, matching the doc's goal of not
overwhelming the reviewer with redundant candidates.
"""

from __future__ import annotations

import difflib

import pandas as pd

from schema_discovery.models import RelationshipCandidate, TableProfile

_KEY_SUFFIXES = ("_id", "_fk", "_ref")
_NAME_SIMILARITY_FLOOR = 0.5
_PK_UNIQUENESS_FLOOR = 0.5


def profile_tables(tables: dict[str, pd.DataFrame]) -> list[TableProfile]:
    return [
        TableProfile(
            name=name,
            row_count=len(df),
            column_count=len(df.columns),
            columns=[str(c) for c in df.columns],
        )
        for name, df in tables.items()
    ]


def _looks_like_key(col: str) -> bool:
    low = str(col).strip().lower()
    return low == "id" or any(low.endswith(suf) for suf in _KEY_SUFFIXES)


def _name_similarity(a: str, b: str) -> float:
    a_norm, b_norm = str(a).strip().lower(), str(b).strip().lower()
    if a_norm == b_norm:
        return 1.0
    return difflib.SequenceMatcher(None, a_norm, b_norm).ratio()


def _coercible_to_numeric(s: pd.Series) -> bool:
    vals = s.dropna()
    if vals.empty:
        return False
    try:
        pd.to_numeric(vals.astype(str))
        return True
    except (ValueError, TypeError):
        return False


def _dtype_compatibility(a: pd.Series, b: pd.Series) -> float:
    a_numeric = pd.api.types.is_numeric_dtype(a)
    b_numeric = pd.api.types.is_numeric_dtype(b)
    if a_numeric == b_numeric:
        return 1.0
    if _coercible_to_numeric(a) and _coercible_to_numeric(b):
        return 0.6
    return 0.0


def _uniqueness(s: pd.Series) -> float:
    vals = s.dropna()
    if vals.empty:
        return 0.0
    return vals.nunique() / len(vals)


def _fk_coverage(fk_values: pd.Series, pk_value_set: set) -> float:
    vals = fk_values.dropna()
    if vals.empty or not pk_value_set:
        return 0.0
    return vals.isin(pk_value_set).mean()


def score_candidate(
    table_a: str, col_a: str, series_a: pd.Series,
    table_b: str, col_b: str, series_b: pd.Series,
) -> RelationshipCandidate | None:
    """Score one column pair. Returns None if it's not a plausible relationship."""
    name_sim = _name_similarity(col_a, col_b)
    key_like = _looks_like_key(col_a) or _looks_like_key(col_b)
    if name_sim < _NAME_SIMILARITY_FLOOR and not key_like:
        return None

    uniqueness_a, uniqueness_b = _uniqueness(series_a), _uniqueness(series_b)

    # Orient: whichever side is more unique is the primary-key ("one") side.
    if uniqueness_a >= uniqueness_b:
        pk_table, pk_col, pk_series, pk_uniqueness = table_a, col_a, series_a, uniqueness_a
        fk_table, fk_col, fk_series = table_b, col_b, series_b
    else:
        pk_table, pk_col, pk_series, pk_uniqueness = table_b, col_b, series_b, uniqueness_b
        fk_table, fk_col, fk_series = table_a, col_a, series_a

    if pk_uniqueness < _PK_UNIQUENESS_FLOOR:
        return None  # neither side is unique enough to plausibly be a primary key

    dtype_score = _dtype_compatibility(series_a, series_b)
    coverage = _fk_coverage(fk_series, set(pk_series.dropna()))

    confidence = (
        0.25 * name_sim
        + 0.15 * dtype_score
        + 0.30 * pk_uniqueness
        + 0.30 * coverage
    )

    return RelationshipCandidate(
        table_a=fk_table, column_a=fk_col,
        table_b=pk_table, column_b=pk_col,
        confidence=round(confidence, 4),
        evidence={
            "name_similarity": round(name_sim, 3),
            "dtype_compatibility": round(dtype_score, 3),
            "pk_uniqueness": round(pk_uniqueness, 3),
            "fk_coverage": round(coverage, 3),
        },
        source="heuristic",
    )


def find_relationship_candidates(
    tables: dict[str, pd.DataFrame],
) -> list[RelationshipCandidate]:
    """Best-scoring relationship candidate for every pair of tables, if any."""
    names = list(tables)
    candidates: list[RelationshipCandidate] = []

    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            table_a, table_b = names[i], names[j]
            df_a, df_b = tables[table_a], tables[table_b]
            best: RelationshipCandidate | None = None
            for col_a in df_a.columns:
                for col_b in df_b.columns:
                    candidate = score_candidate(
                        table_a, col_a, df_a[col_a],
                        table_b, col_b, df_b[col_b],
                    )
                    if candidate is not None and (best is None or candidate.confidence > best.confidence):
                        best = candidate
            if best is not None:
                candidates.append(best)

    candidates.sort(key=lambda c: c.confidence, reverse=True)
    return candidates
