"""
Step 1 — Data validation for forecasting readiness.
"""

from __future__ import annotations

import pandas as pd

MIN_HISTORY = {"daily": 30, "weekly": 12, "monthly": 6}


def _detect_date_column(df: pd.DataFrame) -> str | None:
    candidates = []
    for col in df.columns:
        lowered = col.lower().replace("_", "").replace("-", "").replace(" ", "")
        date_keywords = [
            "date", "time", "timestamp", "datetime", "orderdate", "order_date",
            "createdat", "created_at", "transactiondate", "period",
        ]
        score = sum(2 if lowered == kw or lowered.endswith(kw) else 1 for kw in date_keywords if kw in lowered)
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            score += 3
        if score > 0:
            candidates.append((score, col))

    if not candidates:
        for col in df.columns:
            if pd.api.types.is_datetime64_any_dtype(df[col]):
                return col
            if df[col].dtype == "object":
                try:
                    pd.to_datetime(df[col].dropna().head(30))
                    return col
                except (ValueError, TypeError):
                    pass
    if not candidates:
        return None
    candidates.sort(key=lambda x: -x[0])
    return candidates[0][1]


def _determine_frequency(dates: pd.Series) -> str:
    if len(dates) < 2:
        return "daily"
    diffs = pd.Series(dates).sort_values().diff().dropna()
    if diffs.empty:
        return "daily"
    median_gap = diffs.median()
    if median_gap <= pd.Timedelta(days=2):
        return "daily"
    if median_gap <= pd.Timedelta(days=10):
        return "weekly"
    return "monthly"


def _check_missing_dates(dates: pd.Series, freq: str) -> list[str]:
    if len(dates) < 2:
        return []
    freq_code = "D" if freq == "daily" else "W"
    date_range = pd.date_range(start=dates.min(), end=dates.max(), freq=freq_code)
    existing = set(pd.Series(dates).dt.date)
    return [str(d.date()) for d in date_range if d.date() not in existing]


def _detect_numeric_metrics(df: pd.DataFrame, date_col: str) -> list[str]:
    exclude_tokens = ("id", "code", "key", "index")
    metrics = []
    for col in df.columns:
        if col == date_col:
            continue
        col_clean = col.lower().replace("_", "").replace("-", "")
        if any(col_clean.endswith(tok) or col_clean == tok for tok in exclude_tokens):
            continue
        if pd.api.types.is_numeric_dtype(df[col]):
            metrics.append(col)
    return metrics


def list_forecastable_metrics(df: pd.DataFrame) -> tuple[str | None, list[str]]:
    date_col = _detect_date_column(df)
    if not date_col:
        return None, []
    return date_col, _detect_numeric_metrics(df, date_col)
