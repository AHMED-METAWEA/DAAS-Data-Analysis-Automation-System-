"""
Data Quality Engine — automatically compute quality metrics and flag issues.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .schema_intel import build_schema_summary

OUTLIER_IQR_MULTIPLIER = 3.0
OUTLIER_ZSCORE_THRESHOLD = 3.0


def compute_quality(df: pd.DataFrame) -> list[dict]:
    notes = []
    schema = build_schema_summary(df)

    # ── Missing values ─────────────────────────────────────────────────
    null_counts = df.isnull().sum()
    null_cols = null_counts[null_counts > 0]
    total_cells = df.shape[0] * df.shape[1]
    total_null = int(null_counts.sum())
    overall_pct = round(total_null / total_cells * 100, 2) if total_cells > 0 else 0.0
    notes.append({
        "check": "missing_values",
        "severity": "high" if overall_pct > 10 else "medium" if overall_pct > 3 else "low",
        "total_missing": total_null,
        "overall_pct": overall_pct,
        "columns": {
            str(col): {"missing": int(v), "pct": round(float(v / len(df) * 100), 2)}
            for col, v in null_cols.items()
        } if not null_cols.empty else {},
    })

    # ── Duplicate rows ─────────────────────────────────────────────────
    dup_count = df.duplicated().sum()
    dup_pct = round(dup_count / len(df) * 100, 2) if len(df) > 0 else 0.0
    notes.append({
        "check": "duplicate_rows",
        "severity": "high" if dup_pct > 10 else "medium" if dup_pct > 3 else "low",
        "count": int(dup_count),
        "pct": dup_pct,
    })

    # ── Numeric distributions ──────────────────────────────────────────
    numeric_cols = df.select_dtypes(include=[np.number]).columns
    for col in numeric_cols:
        s = df[col].dropna()
        if len(s) < 5:
            continue
        q1, q3 = s.quantile(0.25), s.quantile(0.75)
        iqr = q3 - q1
        if iqr == 0:
            continue
        lower = q1 - OUTLIER_IQR_MULTIPLIER * iqr
        upper = q3 + OUTLIER_IQR_MULTIPLIER * iqr
        outliers = s[(s < lower) | (s > upper)]
        outlier_pct = round(len(outliers) / len(s) * 100, 2) if len(s) > 0 else 0.0
        if outlier_pct > 1:
            notes.append({
                "check": f"outliers_{col}",
                "severity": "high" if outlier_pct > 10 else "medium" if outlier_pct > 3 else "low",
                "column": str(col),
                "outlier_count": int(len(outliers)),
                "outlier_pct": outlier_pct,
                "lower_bound": round(float(lower), 2),
                "upper_bound": round(float(upper), 2),
                "min_value": round(float(s.min()), 2),
                "max_value": round(float(s.max()), 2),
            })

    # ── Negative values in monetary columns ────────────────────────────
    for col in schema["monetary_columns"]:
        s = df[col].dropna()
        neg = (s < 0).sum()
        if neg > 0:
            neg_pct = round(neg / len(s) * 100, 2)
            notes.append({
                "check": f"negative_values_{col}",
                "severity": "high" if neg_pct > 5 else "low",
                "column": str(col),
                "negative_count": int(neg),
                "negative_pct": neg_pct,
            })

    # ── Zero values in quantity column ─────────────────────────────────
    qty_col = schema["quantity_column"]
    if qty_col and df[qty_col].dtype in ("int64", "float64"):
        zeros = (df[qty_col] == 0).sum()
        if zeros > 0:
            notes.append({
                "check": f"zero_quantity_{qty_col}",
                "severity": "low",
                "column": str(qty_col),
                "zero_count": int(zeros),
                "zero_pct": round(zeros / len(df) * 100, 2),
            })

    # ── Schema inconsistencies ─────────────────────────────────────────
    inferred_types = {}
    for col in df.columns:
        if pd.api.types.is_integer_dtype(df[col]):
            inferred_types[str(col)] = "integer"
        elif pd.api.types.is_float_dtype(df[col]):
            inferred_types[str(col)] = "float"
        elif pd.api.types.is_datetime64_any_dtype(df[col]):
            inferred_types[str(col)] = "datetime"
        elif pd.api.types.is_bool_dtype(df[col]):
            inferred_types[str(col)] = "boolean"
        elif isinstance(df[col].dtype, pd.CategoricalDtype):
            inferred_types[str(col)] = "categorical"
        else:
            inferred_types[str(col)] = "string"

    notes.append({
        "check": "column_types",
        "severity": "info",
        "inferred_types": inferred_types,
        "total_columns": len(df.columns),
    })

    return notes
