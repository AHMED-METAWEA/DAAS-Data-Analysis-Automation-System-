"""Path B ingestion — linking an existing user database (read-only).

Trust hierarchy per the source spec: existing DB constraints > heuristic
detection > LLM inference. If the source database already declares foreign
keys, we trust them directly (``introspect_relationships``) rather than
re-running Schema Discovery for those relationships.

Sampling, not full copy: ``sample_tables`` pulls ``LIMIT`` rows per table
(SQLAlchemy Core, so the LIMIT/TOP translation is correct per dialect)
instead of copying a potentially huge external database wholesale. Sync is
an on-demand snapshot into our own Postgres (Stage 7's ``db.loader``), not a
live query passthrough — the doc's simpler, preferred option "(b)" for this
stage.

``ReadOnlyConnector`` enforces, at the code level, that only SELECT /
introspection calls are ever issued against the linked database. It cannot
force the *database role itself* to be read-only — that's the user's
responsibility to configure — but this code never attempts a write.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import MetaData, create_engine, inspect, select, text
from sqlalchemy.engine import Engine, URL

from schema_discovery.models import RelationshipCandidate

SAMPLE_ROW_LIMIT = 50_000

# Dialect -> SQLAlchemy driver. mssql+pyodbc needs a system ODBC driver
# installed separately; postgresql/mysql work with pure-Python drivers.
_SUPPORTED_DIALECTS = {
    "postgresql": "postgresql+psycopg2",
    "mysql": "mysql+pymysql",
    "mssql": "mssql+pyodbc",
}


class ReadOnlyConnectionError(RuntimeError):
    """Any Path B connection/introspection/read problem — the message is
    meant for direct display to the user, not a raw traceback."""


def friendly_connect_error(exc: Exception, host: str, port: int, database: str) -> str:
    """Recognize the handful of connection failures every user actually hits
    (bad password, bad host, bad port, missing database) and phrase them in
    plain language. SQLAlchemy/DBAPI drivers otherwise report these as a
    driver-specific exception repr plus a "Background on this error at
    sqlalche.me/..." doc link — accurate, but not something to show an end
    user filling in a connection form. Anything not recognized here falls
    back to that raw message rather than guessing.
    """
    raw = str(exc)
    low = raw.lower()
    if "password authentication failed" in low or "access denied for user" in low or "login failed for user" in low:
        return "Incorrect username or password."
    if "does not exist" in low and "database" in low:
        return f'Database "{database}" does not exist on that server.'
    if "unknown database" in low:
        return f'Database "{database}" does not exist on that server.'
    if (
        "could not translate host name" in low
        or "name or service not known" in low
        or "getaddrinfo failed" in low
        or "nodename nor servname provided" in low
    ):
        return f'Could not reach "{host}" — check the hostname and your network connection.'
    if "connection refused" in low or "timed out" in low or "timeout expired" in low:
        return f"Could not reach {host}:{port} — check the host and port, and that the database is configured to accept remote connections."
    if "no module named" in low:
        missing = raw.rsplit("named", 1)[-1].strip().strip("'\"")
        return f"This deployment is missing the driver needed for this database type ({missing}). Contact your administrator."
    # Fall back to the raw driver message, minus SQLAlchemy's noisy doc-link
    # footer, rather than inventing a generic message for a case we don't
    # recognize — still readable, just not specially rephrased.
    return raw.split("\n(Background on this error")[0].strip()


class ReadOnlyConnector:
    """Wraps a SQLAlchemy engine, allowing only SELECT/introspection calls."""

    def __init__(self, engine: Engine):
        self._engine = engine

    @property
    def engine(self) -> Engine:
        return self._engine

    def execute_select(self, sql: str, params: dict | None = None) -> pd.DataFrame:
        stripped = sql.strip().lower()
        if not stripped.startswith("select"):
            raise ReadOnlyConnectionError(
                "Only SELECT statements are permitted through ReadOnlyConnector."
            )
        with self._engine.connect() as conn:
            return pd.read_sql(text(sql), conn, params=params)

    def dispose(self) -> None:
        self._engine.dispose()


def build_connection_url(
    dialect: str, host: str, port: int, database: str, username: str, password: str,
) -> URL:
    driver = _SUPPORTED_DIALECTS.get(dialect)
    if driver is None:
        raise ReadOnlyConnectionError(
            f"Unsupported database type '{dialect}'. Supported: {', '.join(_SUPPORTED_DIALECTS)}"
        )
    query: dict[str, str] = {}
    if dialect == "mssql":
        # mssql+pyodbc has no implicit default driver — SQLAlchemy needs to
        # be told which installed ODBC driver to hand the connection to, or
        # it fails before ever reaching the network with a confusing
        # "Data source name not found" error regardless of credentials.
        query["driver"] = "ODBC Driver 17 for SQL Server"
        # Most real-world SQL Server links (a company's own internal/dev
        # server) present a self-signed or internal-CA certificate; without
        # this the driver refuses the TLS handshake before login is even
        # attempted. Postgres/MySQL don't have this failure mode by default.
        query["TrustServerCertificate"] = "yes"
    # Built via SQLAlchemy's URL.create (not an f-string) specifically so a
    # password containing `@`, `:`, `/`, or `%` — all common in real
    # passwords — gets percent-encoded correctly instead of corrupting the
    # connection string (a literal `@` in the password would otherwise be
    # parsed as the credentials/host separator).
    return URL.create(
        driver, username=username, password=password, host=host, port=port,
        database=database, query=query,
    )


def connect(connection_url: URL | str) -> ReadOnlyConnector:
    try:
        engine = create_engine(connection_url, pool_pre_ping=True)
        with engine.connect():
            pass
    except Exception as exc:
        raise ReadOnlyConnectionError(f"Could not connect: {exc}") from exc
    return ReadOnlyConnector(engine)


def check_connection(connector: ReadOnlyConnector) -> None:
    """Health check: can we introspect, is the schema non-empty."""
    try:
        tables = inspect(connector.engine).get_table_names()
    except Exception as exc:
        raise ReadOnlyConnectionError(f"Could not introspect the database: {exc}") from exc
    if not tables:
        raise ReadOnlyConnectionError(
            "The database has no tables (or the connected role lacks SELECT permission)."
        )


def introspect_relationships(connector: ReadOnlyConnector) -> list[RelationshipCandidate]:
    """Pull the source DB's own declared foreign keys and trust them
    directly (confidence 1.0, source="database") — skips Schema Discovery
    heuristics/LLM for these, per the doc's trust hierarchy.
    """
    inspector = inspect(connector.engine)
    candidates: list[RelationshipCandidate] = []
    for table_name in inspector.get_table_names():
        for fk in inspector.get_foreign_keys(table_name):
            ref_table = fk.get("referred_table")
            constrained = fk.get("constrained_columns") or []
            referred = fk.get("referred_columns") or []
            if not ref_table or not constrained or not referred:
                continue
            candidates.append(RelationshipCandidate(
                table_a=table_name, column_a=constrained[0],
                table_b=ref_table, column_b=referred[0],
                confidence=1.0,
                # A DB-enforced FK constraint mathematically guarantees
                # referential integrity (the database itself rejects any
                # orphaned row) — reflect that honestly here rather than
                # leaving `evidence` empty, which relationships/review.py's
                # `validate_relationship` would otherwise default to 0.0 for
                # every stat (displaying a perfectly valid constraint as
                # "100% orphaned, incompatible types" in the review UI).
                evidence={"pk_uniqueness": 1.0, "fk_coverage": 1.0, "dtype_compatibility": 1.0},
                source="database",
            ))
    return candidates


def sample_tables(
    connector: ReadOnlyConnector,
    table_names: list[str] | None = None,
    row_limit: int = SAMPLE_ROW_LIMIT,
) -> dict[str, pd.DataFrame]:
    """Sample every table (or the given subset) into pandas via a reflected
    ``SELECT ... LIMIT row_limit`` — not a full copy.
    """
    metadata = MetaData()
    # resolve_fks=False: when `table_names` narrows to a subset, SQLAlchemy
    # would otherwise auto-reflect any FK-referenced parent table too (e.g.
    # asking for just "employees" silently also pulls "departments"),
    # ingesting tables the user didn't select. We only need each table for a
    # plain ``SELECT ... LIMIT`` here, and FK discovery runs separately via
    # ``introspect_relationships`` (its own inspector), so skipping FK
    # resolution is safe and makes the documented subset behavior actually hold.
    metadata.reflect(bind=connector.engine, only=table_names, resolve_fks=False)

    tables: dict[str, pd.DataFrame] = {}
    with connector.engine.connect() as conn:
        for name, table in metadata.tables.items():
            query = select(table).limit(row_limit)
            tables[name] = pd.read_sql(query, conn)
    return tables
