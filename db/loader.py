"""Schema-per-project creation + bulk load — Stage 7.

One shared Postgres instance, one schema per project (``project_<slug>``).
Tables are created in the dependency-safe order from ``db.ddl`` and loaded
via ``COPY`` (through ``psycopg2``'s ``copy_expert``) instead of row-by-row
INSERT.

``load_project_schema`` runs CREATE SCHEMA + all DDL + all COPYs inside a
single transaction (one ``engine.begin()`` block) so a failure partway
through a multi-table save (e.g. a COPY failing on table 3 of 5) rolls back
everything already written in that call, instead of leaving some tables
freshly populated and others empty/missing. By the time this module runs,
Integrity Validation (``integrity.validation``) should already have blocked
anything that would fail here — the checks in this module are cheap,
final-line-of-defense safety nets, not the primary gate.
"""

from __future__ import annotations

import io

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Connection

from db.ddl import (
    TableDDL,
    check_primary_key_uniqueness,
    generate_schema_ddl,
    safe_identifier,
    sanitize_schema_columns,
)
from db.session import get_engine
from schema_discovery.models import RelationshipCandidate


def _create_project_schema(conn: Connection, schema_name: str) -> None:
    conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))


def _create_tables(conn: Connection, schema_name: str, table_ddls: list[TableDDL]) -> None:
    # Drop in reverse (dependents before their dependencies) so FK
    # constraints don't block re-creation on a re-run of the same project.
    for ddl in reversed(table_ddls):
        conn.execute(text(f'DROP TABLE IF EXISTS "{schema_name}"."{safe_identifier(ddl.name)}" CASCADE'))
    for ddl in table_ddls:
        conn.execute(text(ddl.to_sql(schema=schema_name)))


def _bulk_load_table(conn: Connection, schema_name: str, table_name: str, df: pd.DataFrame) -> None:
    """Fast bulk load via COPY, streaming an in-memory CSV buffer, on the
    same DBAPI connection/transaction as ``conn`` — no separate commit here,
    the caller's ``engine.begin()`` block owns commit/rollback for the whole
    save.
    """
    if df.empty:
        return
    table_name = safe_identifier(table_name)
    buffer = io.StringIO()
    df.to_csv(buffer, index=False, header=False, na_rep="\\N")
    buffer.seek(0)

    columns = ", ".join(f'"{safe_identifier(c)}"' for c in df.columns)
    cursor = conn.connection.cursor()
    try:
        cursor.copy_expert(
            f'COPY "{schema_name}"."{table_name}" ({columns}) '
            "FROM STDIN WITH (FORMAT csv, NULL '\\N')",
            buffer,
        )
    finally:
        cursor.close()


def create_project_schema(schema_name: str) -> None:
    schema_name = safe_identifier(schema_name)
    engine = get_engine()
    with engine.begin() as conn:
        _create_project_schema(conn, schema_name)


def create_tables(schema_name: str, table_ddls: list[TableDDL]) -> None:
    schema_name = safe_identifier(schema_name)
    engine = get_engine()
    with engine.begin() as conn:
        _create_tables(conn, schema_name, table_ddls)


def bulk_load_table(schema_name: str, table_name: str, df: pd.DataFrame) -> None:
    schema_name = safe_identifier(schema_name)
    engine = get_engine()
    with engine.begin() as conn:
        _bulk_load_table(conn, schema_name, table_name, df)


def load_project_schema(
    schema_name: str,
    tables: dict[str, pd.DataFrame],
    relationships: list[RelationshipCandidate] | None = None,
    *,
    primary_keys: dict[str, str | list[str]] | None = None,
) -> list[TableDDL]:
    """Create the schema, generate + run DDL, and bulk-load every table, in
    dependency-safe order, as one atomic transaction. Returns the TableDDL
    list that was created.

    ``primary_keys`` should come from Integrity Validation
    (``IntegrityReport.confirmed_primary_keys``); when omitted, DDL
    generation falls back to the legacy relationship-derived heuristic.

    Column names are sanitized into valid SQL identifiers here (see
    ``db.ddl.sanitize_schema_columns``) — a raw header like "Transaction ID"
    is the common case for uploaded data, not an edge case.
    """
    schema_name = safe_identifier(schema_name)
    tables, relationships, primary_keys = sanitize_schema_columns(tables, relationships, primary_keys)
    table_ddls = generate_schema_ddl(tables, relationships, primary_keys=primary_keys)
    check_primary_key_uniqueness(tables, table_ddls)

    engine = get_engine()
    with engine.begin() as conn:
        _create_project_schema(conn, schema_name)
        _create_tables(conn, schema_name, table_ddls)
        for ddl in table_ddls:
            _bulk_load_table(conn, schema_name, ddl.name, tables[ddl.name])
    return table_ddls
