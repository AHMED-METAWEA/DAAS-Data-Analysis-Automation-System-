"""Canonical key-column normalization for cross-table reconciliation.

Cleaning each table independently can silently break join keys — e.g.
trimming whitespace or normalizing casing on ``customer_id`` in
``customers.csv`` but not in ``orders.csv`` creates orphan rows that didn't
exist before cleaning. Detecting/diffing exactly what an arbitrary
LLM-generated cleaning function did is impractical, so instead: normalize
BOTH sides of every approved relationship's key columns identically, right
before storage.
"""

from __future__ import annotations

import pandas as pd

from schema_discovery.models import RelationshipCandidate
from tools.arabic_text import normalize_arabic


def is_numeric_like_series(series: pd.Series) -> bool:
    vals = series.dropna()
    if vals.empty:
        return False
    if pd.api.types.is_numeric_dtype(vals):
        return True
    try:
        pd.to_numeric(vals.astype(str))
        return True
    except (ValueError, TypeError):
        return False


def _normalize_text_value(v) -> str | None:
    if pd.isna(v):
        return None
    s = str(v).strip()
    s = normalize_arabic(s)
    return s.casefold()


def normalize_key_series(series: pd.Series) -> pd.Series:
    """Normalize one key column to a canonical, comparable representation.

    Numeric-looking keys go through ``pd.to_numeric`` -> whole/fractional
    check -> string, so ``1001`` (int64), ``1001.0`` (float64, e.g. after an
    imputation pass widened the dtype), and ``"1001"`` (str) all normalize to
    the same value — a naive ``.astype(str).str.strip()`` would NOT catch
    this and would leave the orphan-rate check permanently "broken" for the
    common case of numeric IDs. Non-numeric keys are stripped and
    casefolded, routing Arabic text through the existing
    ``tools.arabic_text.normalize_arabic`` for consistency with the rest of
    the app.
    """
    if is_numeric_like_series(series):
        numeric = pd.to_numeric(series, errors="coerce")
        result = pd.Series(index=series.index, dtype="object")
        for idx, val in numeric.items():
            if pd.isna(val):
                result[idx] = None
            elif float(val) == int(val):
                result[idx] = str(int(val))
            else:
                result[idx] = str(val)
        return result

    return series.apply(_normalize_text_value)


def normalize_relationship_keys(
    tables: dict[str, pd.DataFrame], relationship: RelationshipCandidate
) -> dict[str, pd.DataFrame]:
    """Return new table copies with the relationship's key columns
    overwritten by their canonically normalized values — this IS the final
    representation that goes into storage (Stage 7), not a side column.
    """
    tables = dict(tables)
    df_a = tables[relationship.table_a].copy()
    df_b = tables[relationship.table_b].copy()
    df_a[relationship.column_a] = normalize_key_series(df_a[relationship.column_a])
    df_b[relationship.column_b] = normalize_key_series(df_b[relationship.column_b])
    tables[relationship.table_a] = df_a
    tables[relationship.table_b] = df_b
    return tables
