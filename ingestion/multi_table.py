"""Path A ingestion — multiple uploaded files kept as separate related tables.

Unlike ``tools.ingestion``'s ``plan_combination``/``combine_datasets`` (which
collapses multiple files into ONE DataFrame via append/join heuristics), this
keeps every file as its own named table so Schema Discovery (see
``schema_discovery/``) can detect and preserve real cross-table relationships
instead of losing them at ingestion time. ``tools.ingestion.load_tabular_file``
is still used for the actual per-file parsing — only the "then what" changes.

Google Sheets (Stage 10) and existing-database linking (Stage 11) converge on
the same contract: a ``dict[str, pd.DataFrame]`` keyed by table name.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from db.ddl import sanitize_identifier
from tools.ingestion import Dataset


def _table_name(name: str) -> str:
    """Filename stem, sanitized into a valid SQL identifier — a raw upload
    like "Customer Data.csv" must not carry spaces/punctuation into a table
    name that Schema Discovery, Save, and every downstream engine treat as
    a stable key (see db.ddl.sanitize_identifier)."""
    return sanitize_identifier(Path(name).stem)


def tables_from_datasets(datasets: list[Dataset]) -> dict[str, pd.DataFrame]:
    """Convert loaded datasets into a name -> DataFrame mapping, one entry per
    file. Table names are derived from the filename stem and de-duplicated
    (``orders``, ``orders_2``, ...) if two files would collide.
    """
    tables: dict[str, pd.DataFrame] = {}
    for d in datasets:
        base = _table_name(d.name)
        final = base
        suffix = 2
        while final in tables:
            final = f"{base}_{suffix}"
            suffix += 1
        tables[final] = d.df
    return tables


def choose_primary_table(tables: dict[str, pd.DataFrame]) -> str:
    """Heuristic default for which table feeds the (still single-table)
    cleaning pipeline before Stage 5's multi-table orchestration lands: the
    table with the most rows, matching the existing star-schema convention
    where the largest table is the fact/transaction table.
    """
    if not tables:
        raise ValueError("No tables provided.")
    return max(tables, key=lambda name: len(tables[name]))
