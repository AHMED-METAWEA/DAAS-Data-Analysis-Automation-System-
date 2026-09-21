"""
PostgreSQL connection and data persistence utilities.

Uses environment variables for configuration:
    PG_HOST, PG_PORT, PG_DBNAME, PG_USER, PG_PASSWORD

All functions raise on connection/query failure — caller handles errors.
"""

from __future__ import annotations

import os
import re

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.sql import quoted_name

# Unicode-aware for the same reason as db.ddl._IDENTIFIER_RE: an Arabic table name
# is legitimate (identifiers reaching Postgres here are quoted), while anything
# that could break out of an identifier — quotes, whitespace, `;`, `--` — still
# fails to match. `[^\W\d]` means "word character that isn't a digit".
_IDENTIFIER_RE = re.compile(r"^[^\W\d]\w*$", re.UNICODE)

# One pooled engine per distinct connection string, reused for the whole
# process. See ``get_engine`` for why this must be cached, not rebuilt.
_ENGINES: dict[str, Engine] = {}


def _safe_identifier(name: str) -> str:
    if not _IDENTIFIER_RE.match(name):
        raise ValueError(f"Invalid SQL identifier: '{name}'")
    return name


def _conn_str() -> str:
    host = os.environ.get("PG_HOST", "localhost")
    port = os.environ.get("PG_PORT", "5433")
    dbname = os.environ.get("PG_DBNAME", "smart_analyst")
    user = os.environ.get("PG_USER", "postgres")
    password = os.environ.get("PG_PASSWORD", "postgres")
    return f"postgresql://{user}:{password}@{host}:{port}/{dbname}"


def get_engine() -> Engine:
    """Return a process-wide, pooled SQLAlchemy engine for the platform database.

    This is called on *every* save/load/agent-persist (``store_df_to_pg``,
    ``db.loader.load_project_schema``, report/churn/forecast/marketing storage,
    ``db.views`` …). It previously did ``create_engine(_conn_str())`` — a brand
    new engine, and therefore a brand new connection pool, on every call. Those
    pools were never disposed, so their connections accumulated until Postgres
    hit ``max_connections`` ("FATAL: sorry, too many clients already") and saves
    began failing intermittently. It also had no ``pool_pre_ping``, so a
    connection left idle long enough for Postgres (or a proxy/firewall) to drop
    it would raise "server closed the connection unexpectedly" on the next use.

    Caching one engine per connection string fixes the leak; ``pool_pre_ping``
    validates a connection before handing it out (silently replacing a dead one)
    and ``pool_recycle`` retires connections before the server would. This mirrors
    what ``ingestion/db_link.py`` already does for *external* user databases —
    the internal engine just hadn't been brought in line. Keyed by connection
    string so tests that point at a different database still get their own engine.
    """
    conn_str = _conn_str()
    engine = _ENGINES.get(conn_str)
    if engine is None:
        engine = create_engine(
            conn_str,
            pool_pre_ping=True,
            pool_recycle=1800,
            pool_size=5,
            max_overflow=10,
        )
        _ENGINES[conn_str] = engine
    return engine


def is_pg_configured() -> bool:
    return bool(os.environ.get("PG_HOST") and os.environ.get("PG_USER"))


def store_df_to_pg(df: pd.DataFrame, table_name: str) -> None:
    safe = _safe_identifier(table_name)
    engine = get_engine()
    with engine.begin() as conn:
        df.to_sql(safe, conn, if_exists="replace", index=False)


def load_df_from_pg(table_name: str) -> pd.DataFrame:
    safe = _safe_identifier(table_name)
    engine = get_engine()
    with engine.connect() as conn:
        return pd.read_sql(text(f"SELECT * FROM {quoted_name(safe, False)}"), conn)


def get_schema_info(table_name: str) -> str:
    safe = _safe_identifier(table_name)
    engine = get_engine()
    with engine.connect() as conn:
        cols = conn.execute(
            text(
                "SELECT column_name, data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_name = :t "
                "ORDER BY ordinal_position"
            ),
            {"t": safe},
        ).fetchall()
        row_count = conn.execute(
            text(f"SELECT COUNT(*) FROM {quoted_name(safe, False)}")
        ).scalar()
        sample = pd.read_sql(
            text(f"SELECT * FROM {quoted_name(safe, False)} LIMIT 5"), conn
        )

    parts = [
        f"Table: {table_name}  |  Rows: {row_count}  |  Columns: {len(cols)}"
    ]
    for c in cols:
        nullable = "YES" if c[2] == "YES" else "NO"
        parts.append(f"  - {c[0]}: {c[1]} (nullable={nullable})")
    parts.append(f"\nSample ({len(sample)} rows):\n{sample.to_string()}")
    return "\n".join(parts)
