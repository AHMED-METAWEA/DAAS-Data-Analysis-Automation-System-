"""
Helper functions used by the Profiler node.

These functions encapsulate the pandas logic for building
ColumnProfile and DatasetProfile objects. They are kept separate
from the node itself for testability and reuse by future agents.
"""

from __future__ import annotations

from itertools import combinations

import pandas as pd

from models.profiler_models import ColumnProfile, DatasetProfile
from tools.arabic_text import ARABIC_PLACEHOLDERS, arabic_ratio, normalize_arabic

# Values commonly used as placeholders for missing data. Arabic placeholders
# are included both raw and normalized (the profiler normalizes Arabic text
# before this check runs, e.g. "خطأ" → "خطا").
PLACEHOLDER_VALUES: set[str] = (
    {"", "unknown", "error", "n/a", "na", "tbd", "-", "?", "null", "none"}
    | ARABIC_PLACEHOLDERS
    | {normalize_arabic(p) for p in ARABIC_PLACEHOLDERS}
)


# ── Per-column helpers ─────────────────────────────────────────────────


def detect_type_mismatch(series: pd.Series) -> bool:
    """
    Check if a column contains values that conflict with its inferred dtype.

    For object columns: check if >50% of non-null values can be parsed as numbers,
    suggesting the column *should* be numeric but has rogue strings mixed in.

    For numeric columns: check if the original raw series (before pandas coercion)
    contained non-numeric strings — pandas may have already coerced them to NaN.
    """
    if series.dropna().empty:
        return False

    if pd.api.types.is_string_dtype(series):
        non_null = series.dropna()
        numeric_count = pd.to_numeric(non_null, errors="coerce").notna().sum()
        ratio = numeric_count / len(non_null) if len(non_null) > 0 else 0
        # If most values look numeric but dtype is string → mismatch
        if 0.3 < ratio < 1.0:
            return True
        return False

    return False


def detect_outliers(series: pd.Series) -> bool:
    """
    IQR-based outlier detection for numeric columns.
    Returns True if any values fall outside 1.5 * IQR from Q1/Q3.
    Non-numeric columns always return False.
    """
    if not pd.api.types.is_numeric_dtype(series):
        return False

    clean = series.dropna()
    if len(clean) < 4:
        return False

    q1 = clean.quantile(0.25)
    q3 = clean.quantile(0.75)
    iqr = q3 - q1

    if iqr == 0:
        return False

    lower = q1 - 1.5 * iqr
    upper = q3 + 1.5 * iqr

    return bool(((clean < lower) | (clean > upper)).any())


def compute_numeric_stats(series: pd.Series) -> dict | None:
    """
    Compute descriptive statistics for a numeric column.
    Returns None for non-numeric columns.

    Stats: min, max, mean, std, skewness.
    """
    if not pd.api.types.is_numeric_dtype(series):
        return None

    clean = series.dropna()
    if clean.empty:
        return {
            "min": None,
            "max": None,
            "mean": None,
            "std": None,
            "skewness": None,
        }

    return {
        "min": float(clean.min()),
        "max": float(clean.max()),
        "mean": float(clean.mean()),
        "std": float(clean.std()) if len(clean) > 1 else 0.0,
        "skewness": float(clean.skew()) if len(clean) > 2 else 0.0,
    }


def infer_column_type(series: pd.Series, col_name: str) -> str:
    """
    Infer the semantic type of a column based on its name, dtype, and values.

    Returns one of: numeric, categorical, text, date, id, boolean, unknown.
    """
    name_lower = col_name.lower().strip()

    # Heuristic: name suggests date/time (check before id to catch e.g. "order_date")
    if any(kw in name_lower for kw in ("date", "time", "year", "month", "day")):
        return "date"

    # Heuristic: name-based ID detection + high uniqueness
    if any(kw in name_lower for kw in ("id", "code", "key", "ord", "sku", "ref")):
        nunique = series.dropna().nunique()
        total = max(len(series.dropna()), 1)
        if nunique / total > 0.5:
            return "id"

    if pd.api.types.is_bool_dtype(series.dtype):
        return "boolean"

    if pd.api.types.is_numeric_dtype(series.dtype):
        return "numeric"

    non_null = series.dropna()
    if non_null.empty:
        return "unknown"

    # Check if mostly numeric strings → numeric (before categorical, since
    # e.g. "quantity" with values 1-10 stored as strings is numeric, not categorical)
    if pd.api.types.is_string_dtype(series):
        numeric_count = pd.to_numeric(non_null, errors="coerce").notna().sum()
        if numeric_count / len(non_null) > 0.8 and non_null.nunique() > 2:
            return "numeric"

    # Low cardinality → categorical
    card_ratio = non_null.nunique() / len(non_null)
    if card_ratio < 0.05 or non_null.nunique() < 25:
        return "categorical"

    return "text"


def detect_column_relationships(df: pd.DataFrame) -> list[dict]:
    """
    Detect meaningful relationships between columns.

    Currently detects:
      - Arithmetic relationships (a * b = c, a + b = c)
    """
    relationships: list[dict] = []

    # Collect columns that are or can be treated as numeric
    numeric_cols = []
    for col in df.columns:
        if pd.api.types.is_numeric_dtype(df[col]):
            numeric_cols.append(col)
        elif pd.api.types.is_string_dtype(df[col]):
            numeric_count = pd.to_numeric(df[col], errors="coerce").notna().sum()
            if numeric_count / max(len(df[col].dropna()), 1) > 0.5:
                numeric_cols.append(col)

    if len(numeric_cols) < 3:
        return relationships

    # Check arithmetic: col_a * col_b ≈ col_c
    for col_a, col_b in combinations(numeric_cols, 2):
        a = pd.to_numeric(df[col_a], errors="coerce")
        b = pd.to_numeric(df[col_b], errors="coerce")
        for col_c in numeric_cols:
            if col_c in (col_a, col_b):
                continue
            c = pd.to_numeric(df[col_c], errors="coerce")
            mask = a.notna() & b.notna() & c.notna()
            if mask.sum() < 5:
                continue
            prod = a[mask] * b[mask]
            match = (prod / c[mask] - 1).abs() < 0.01
            if match.mean() > 0.9:
                relationships.append({
                    "type": "arithmetic",
                    "expression": f"{col_c} = {col_a} × {col_b}",
                    "match_pct": round(match.mean() * 100, 1),
                })
                break

        if any(r["expression"] == f"{col_c} = {col_a} × {col_b}"
               for r in relationships for col_c in numeric_cols):
            continue

    # Check additive: col_a + col_b ≈ col_c
    for col_a, col_b in combinations(numeric_cols, 2):
        a = pd.to_numeric(df[col_a], errors="coerce")
        b = pd.to_numeric(df[col_b], errors="coerce")
        for col_c in numeric_cols:
            if col_c in (col_a, col_b):
                continue
            c = pd.to_numeric(df[col_c], errors="coerce")
            mask = a.notna() & b.notna() & c.notna()
            if mask.sum() < 5:
                continue
            total = a[mask] + b[mask]
            match = (total / c[mask] - 1).abs() < 0.01
            if match.mean() > 0.9:
                relationships.append({
                    "type": "arithmetic",
                    "expression": f"{col_c} = {col_a} + {col_b}",
                    "match_pct": round(match.mean() * 100, 1),
                })
                break

    return relationships


# ── Builders ───────────────────────────────────────────────────────────


def _count_placeholders(series: pd.Series) -> int:
    """Count values that are known placeholder strings for missing data."""
    if not pd.api.types.is_string_dtype(series):
        return 0
    return int(series.dropna().str.strip().str.lower().isin(PLACEHOLDER_VALUES).sum())


def build_column_profile(series: pd.Series, *, is_relationship_key: bool = False) -> ColumnProfile:
    """Build a ColumnProfile for a single DataFrame column."""
    total = len(series)
    missing_nulls = int(series.isna().sum())
    placeholder_count = _count_placeholders(series)
    missing = missing_nulls + placeholder_count

    # Filter out placeholders for profiling (treat as missing)
    if pd.api.types.is_string_dtype(series):
        clean_series = series.dropna()
        clean_mask = ~clean_series.str.strip().str.lower().isin(PLACEHOLDER_VALUES)
        clean_vals = clean_series[clean_mask]
    else:
        clean_vals = series.dropna()

    # Sample up to 5 unique values, converted to native Python types
    sample_vals = (
        clean_vals.drop_duplicates()
        .head(5)
        .apply(lambda x: x.item() if hasattr(x, "item") else x)
        .tolist()
    )

    inferred = infer_column_type(series, series.name)

    return ColumnProfile(
        name=series.name,
        dtype=str(series.dtype),
        inferred_type=inferred,
        missing_count=missing,
        missing_pct=round((missing / total) * 100, 2) if total > 0 else 0.0,
        placeholder_count=placeholder_count,
        unique_count=int(clean_vals.nunique()),
        sample_values=sample_vals,
        type_mismatch_flag=detect_type_mismatch(series),
        outlier_flag=detect_outliers(series),
        numeric_stats=compute_numeric_stats(series),
        arabic_ratio=arabic_ratio(series),
        is_relationship_key=is_relationship_key,
    )


def replace_placeholders_with_nan(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert known placeholder strings (UNKNOWN, ERROR, N/A, etc.) to NaN.

    Superseded in the cleaning pipeline by the ``standardize_null_placeholders``
    operator (``tools/cleaning_ops.py``), which is dtype-agnostic — this
    function's ``is_string_dtype`` gate silently skips any column mixing
    strings with numbers, which is where placeholders most often hide. Kept for
    callers that want the simple string-column behaviour.

    No longer widens integer columns to float64. It used to, so that later
    imputation could store NaN, but it did so unconditionally: every integer
    id in every dataset became ``1001.0``, including columns with nothing to
    fix, and that representation carried into the generated DDL and into join
    keys. Widening is now the business of the operator that actually needs it.

    Returns a new DataFrame — the original is not modified.
    """
    df = df.copy()
    for col in df.columns:
        if pd.api.types.is_string_dtype(df[col]):
            mask = df[col].str.strip().str.lower().isin(PLACEHOLDER_VALUES)
            df.loc[mask, col] = pd.NA
    return df


def build_dataset_profile(df: pd.DataFrame, *, key_columns: set[str] | None = None) -> DatasetProfile:
    """Build a full DatasetProfile from a DataFrame.

    ``key_columns``, when given, marks columns that participate in an
    approved cross-table relationship (see relationships/review.py) so the
    cleaning planner treats them more conservatively (Stage 5).
    """
    key_columns = key_columns or set()
    column_profiles = [
        build_column_profile(df[col], is_relationship_key=col in key_columns)
        for col in df.columns
    ]
    relationships = detect_column_relationships(df)

    return DatasetProfile(
        total_rows=len(df),
        total_cols=len(df.columns),
        duplicate_rows_count=int(df.duplicated().sum()),
        columns=column_profiles,
        relationships=relationships,
    )


def is_profile_clean(profile: DatasetProfile) -> bool:
    """True if the deterministic profiler found no objective data-quality
    defect — no missing values, no placeholder values, no duplicate rows, no
    dtype/type mismatches.

    Deliberately excludes ``outlier_flag``: a statistical outlier is often a
    real, legitimate extreme value (a big-spender customer, a bulk order),
    not a defect — treating outliers alone as "dirty" would force cleaning
    plans onto data that doesn't actually need any changes, which is exactly
    the false-positive this check exists to eliminate. Used to short-circuit
    the planner LLM call entirely for already-clean data (see
    ``agents/cleaning/planner.py::planner_node``) — deterministic and free,
    rather than trusting the LLM to notice there's nothing to do.
    """
    if profile.duplicate_rows_count > 0:
        return False
    return all(
        col.missing_count == 0 and col.placeholder_count == 0 and not col.type_mismatch_flag
        for col in profile.columns
    )
