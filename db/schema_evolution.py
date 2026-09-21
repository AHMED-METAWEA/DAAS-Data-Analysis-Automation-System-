"""Minimal schema evolution: add new columns when a project's data gains a
column on re-upload.

Anything bigger (a column removed, a type change) is a documented
"recreate the table" limitation, not scope creep — Alembic-style versioned
migrations aren't a good fit for N dynamically-named per-project schemas
(see db/base.py), and a single-dev tool doesn't need more than this.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import inspect, text

from db.ddl import infer_pg_type, safe_identifier
from db.session import get_engine


def diff_new_columns(schema_name: str, table_name: str, df: pd.DataFrame) -> list[str]:
    """Columns in `df` that don't exist yet in the stored table."""
    engine = get_engine()
    inspector = inspect(engine)
    existing = {col["name"] for col in inspector.get_columns(table_name, schema=schema_name)}
    return [c for c in df.columns if c not in existing]


def add_new_columns(schema_name: str, table_name: str, df: pd.DataFrame) -> list[str]:
    """ALTER TABLE ADD COLUMN for any column in `df` not already present.
    Returns the column names that were added.
    """
    new_cols = diff_new_columns(schema_name, table_name, df)
    if not new_cols:
        return []
    schema_id = safe_identifier(schema_name)
    table_id = safe_identifier(table_name)
    engine = get_engine()
    with engine.begin() as conn:
        for col in new_cols:
            pg_type = infer_pg_type(df[col])
            conn.execute(text(
                f'ALTER TABLE "{schema_id}"."{table_id}" ADD COLUMN "{safe_identifier(col)}" {pg_type}'
            ))
    return new_cols
