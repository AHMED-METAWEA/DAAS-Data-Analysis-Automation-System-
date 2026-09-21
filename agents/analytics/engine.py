"""
Analytics Engine — main orchestrator.

Runs the full analytics pipeline:
  1. Schema intelligence
  2. KPI computation
  3. Time series intelligence
  4. Data quality checks

Returns a structured payload consumable by downstream AI agents
(insights, visualization, alerting).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from tools.db_tools import load_df_from_pg

from .kpi_engine import compute_kpis
from .quality import compute_quality
from .schema_intel import build_schema_summary
from .timeseries import compute_timeseries


def run_analytics(
    data_df: pd.DataFrame | None = None,
    table_name: str = "",
) -> dict:
    """
    Execute the full analytics pipeline.

    Parameters
    ----------
    data_df : DataFrame or None
        In-memory cleaned data.  When *None* loads from *table_name*.
    table_name : str
        PostgreSQL table name (used only when *data_df* is None).

    Returns
    -------
    dict
        Structured analytics payload with keys:
          - schema
          - kpi
          - timeseries
          - quality
          - metadata
    """
    if data_df is not None:
        df = data_df
    else:
        df = load_df_from_pg(table_name)

    schema = build_schema_summary(df)
    kpi = compute_kpis(df)
    timeseries = compute_timeseries(df)
    quality = compute_quality(df)

    payload = {
        "schema": schema,
        "kpi": kpi,
        "timeseries": timeseries,
        "quality": quality,
        "metadata": {
            "row_count": len(df),
            "column_count": len(df.columns),
            "columns": list(df.columns),
            "dtypes": {str(col): str(dtype) for col, dtype in df.dtypes.items()},
            "generated_at": datetime.now(UTC).isoformat(),
            "source": "in_memory" if data_df is not None else f"pg:{table_name}",
        },
    }

    return payload
