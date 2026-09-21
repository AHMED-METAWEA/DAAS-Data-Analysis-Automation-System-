"""
Step 2 — Metric discovery for sales / e-commerce datasets.
"""

from __future__ import annotations

import pandas as pd

from agents.forecasting.forecast_models import ForecastState

# Tokens are matched against the column name with separators stripped
# (``total_price`` -> ``totalprice``), so keep tokens separator-free.
_PRIORITY_TOKENS = (
    "revenue", "sales", "gmv", "totalsales", "netrevenue", "turnover",
    "totalprice", "linetotal", "grandtotal", "subtotal", "ordervalue",
    "totalspent", "amount",
    "orders", "ordercount", "transactions",
    "customers", "customercount", "uniquecustomers",
    "quantity", "units", "aov", "averageordervalue",
    "profit", "margin",
)


def rank_metrics(metrics: list[str]) -> list[str]:
    scored: list[tuple[int, str]] = []
    for col in metrics:
        norm = col.lower().replace("_", "").replace("-", "").replace(" ", "")
        score = next(
            (len(_PRIORITY_TOKENS) - i for i, p in enumerate(_PRIORITY_TOKENS) if p in norm),
            0,
        )
        scored.append((score, col))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [col for _, col in scored]


def discover_metrics(
    df: pd.DataFrame, available_metrics: list[str] | None = None
) -> list[dict[str, str | int]]:
    cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    if available_metrics:
        cols = [c for c in cols if c in available_metrics]
    ranked = rank_metrics(cols)
    return [{"metric": m, "rank": i} for i, m in enumerate(ranked[:10])]


def discover_metrics_node(state: ForecastState) -> dict:
    if not state.get("forecastable", False):
        return {
            "forecast_targets": [],
            "current_target_index": 0,
            "forecast_outputs": [],
            "model_selections": [],
            "execution_figures": [],
            "execution_tables": [],
            "forecast_evaluations": [],
            "forecast_history_rows": [],
        }

    available = state.get("available_metrics", [])
    targets = rank_metrics(available)[:3]

    return {
        "forecast_targets": targets,
        "current_target_index": 0,
        "forecast_outputs": [],
        "model_selections": [],
        "execution_figures": [],
        "execution_tables": [],
        "forecast_evaluations": [],
        "forecast_history_rows": [],
    }
