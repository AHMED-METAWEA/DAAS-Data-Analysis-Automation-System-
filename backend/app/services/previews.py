"""Shared DataFrame -> TablePreview conversion (ingestion + cleaning both
need to show a small JSON-safe sample of a table)."""

from __future__ import annotations

import json

import pandas as pd

from backend.app.schemas.pipeline import TablePreview

PREVIEW_ROWS = 20


def table_preview(name: str, df: pd.DataFrame) -> TablePreview:
    head = df.head(PREVIEW_ROWS)
    records = json.loads(head.to_json(orient="records", date_format="iso"))
    return TablePreview(name=name, rows=len(df), columns=[str(c) for c in df.columns], preview=records)
