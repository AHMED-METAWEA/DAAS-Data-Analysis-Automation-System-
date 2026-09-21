from __future__ import annotations

import pytest
from sqlalchemy import text

from ingestion.db_link import (
    ReadOnlyConnectionError,
    build_connection_url,
    check_connection,
    connect,
    introspect_relationships,
    sample_tables,
)
from tools.db_tools import _conn_str

pytestmark = pytest.mark.integration


@pytest.fixture
def external_schema():
    """Uses our own docker-compose Postgres as a stand-in "external"
    database to link to — a throwaway schema with a real FK constraint,
    exercised through the exact same ReadOnlyConnector path Path B uses for
    any Postgres/MySQL/SQL Server source."""
    from db.session import get_engine

    engine = get_engine()
    schema = "external_db_link_test"
    with engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        conn.execute(text(
            f'CREATE TABLE "{schema}".customers (customer_id BIGINT PRIMARY KEY, name TEXT)'
        ))
        conn.execute(text(
            f'CREATE TABLE "{schema}".orders ('
            "order_id BIGINT PRIMARY KEY, "
            f'customer_id BIGINT REFERENCES "{schema}".customers(customer_id), '
            "total DOUBLE PRECISION)"
        ))
        conn.execute(text(
            f"INSERT INTO \"{schema}\".customers VALUES (1, 'Alice'), (2, 'Bob')"
        ))
        conn.execute(text(
            f'INSERT INTO "{schema}".orders VALUES (100, 1, 10.5), (101, 2, 20.0)'
        ))

    yield schema

    with engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


def _connection_url_for_schema(schema: str) -> str:
    # Reuse the same PG_* env vars the rest of the app uses; the `options`
    # query param sets the connection's search_path so unqualified table
    # names resolve to our throwaway test schema, simulating "some other
    # database" without needing a second Postgres instance.
    return f"{_conn_str()}?options=-csearch_path%3D{schema}"


class TestReadOnlyConnector:
    def test_rejects_a_crafted_write_statement(self, external_schema) -> None:
        connector = connect(_connection_url_for_schema(external_schema))
        try:
            with pytest.raises(ReadOnlyConnectionError, match="Only SELECT"):
                connector.execute_select("DELETE FROM customers WHERE customer_id = 1")
        finally:
            connector.dispose()

    def test_allows_a_real_select(self, external_schema) -> None:
        connector = connect(_connection_url_for_schema(external_schema))
        try:
            df = connector.execute_select("SELECT * FROM customers")
            assert len(df) == 2
        finally:
            connector.dispose()


class TestBuildConnectionUrl:
    def test_unsupported_dialect_raises(self) -> None:
        with pytest.raises(ReadOnlyConnectionError, match="Unsupported"):
            build_connection_url("oracle", "host", 1234, "db", "user", "pass")

    def test_postgres_dialect_builds_expected_url(self) -> None:
        url = build_connection_url("postgresql", "localhost", 5432, "mydb", "user", "pass")
        assert url.render_as_string(hide_password=False) == "postgresql+psycopg2://user:pass@localhost:5432/mydb"

    def test_password_with_special_characters_is_escaped(self) -> None:
        # A literal `@`/`:`/`/` in the password must not be confused with the
        # URL's own credentials/host separators (previously built via a raw
        # f-string, which corrupted the connection string for any password
        # containing them — extremely common in real password policies).
        url = build_connection_url("postgresql", "localhost", 5432, "mydb", "user", "p@ss:w/ord")
        assert url.password == "p@ss:w/ord"
        assert url.host == "localhost"
        assert url.database == "mydb"

    def test_mssql_dialect_specifies_odbc_driver_and_trust_cert(self) -> None:
        # mssql+pyodbc has no implicit default driver — omitting this query
        # param fails with "Data source name not found" before a single byte
        # reaches the network, regardless of how correct the credentials are.
        url = build_connection_url("mssql", "localhost", 1433, "mydb", "sa", "pass")
        assert url.query.get("driver") == "ODBC Driver 17 for SQL Server"
        assert url.query.get("TrustServerCertificate") == "yes"


class TestCheckConnection:
    def test_healthy_database_does_not_raise(self, external_schema) -> None:
        connector = connect(_connection_url_for_schema(external_schema))
        try:
            check_connection(connector)  # should not raise
        finally:
            connector.dispose()

    def test_empty_database_raises(self) -> None:
        from db.session import get_engine

        engine = get_engine()
        schema = "external_db_link_empty_test"
        with engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        try:
            connector = connect(_connection_url_for_schema(schema))
            try:
                with pytest.raises(ReadOnlyConnectionError, match="no tables"):
                    check_connection(connector)
            finally:
                connector.dispose()
        finally:
            with engine.begin() as conn:
                conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


class TestIntrospectRelationships:
    def test_trusts_existing_fk_constraint_directly(self, external_schema) -> None:
        connector = connect(_connection_url_for_schema(external_schema))
        try:
            candidates = introspect_relationships(connector)
        finally:
            connector.dispose()

        assert len(candidates) == 1
        rel = candidates[0]
        assert (rel.table_a, rel.column_a, rel.table_b, rel.column_b) == (
            "orders", "customer_id", "customers", "customer_id",
        )
        assert rel.confidence == 1.0
        assert rel.source == "database"
        # Evidence must be populated, not left empty — relationships/review.py's
        # validate_relationship() defaults every missing stat to 0.0, which
        # would otherwise display a real, DB-enforced FK constraint as "100%
        # orphaned, incompatible types" in the relationship-review UI.
        assert rel.evidence == {"pk_uniqueness": 1.0, "fk_coverage": 1.0, "dtype_compatibility": 1.0}


class TestSampleTables:
    def test_samples_every_table(self, external_schema) -> None:
        connector = connect(_connection_url_for_schema(external_schema))
        try:
            tables = sample_tables(connector)
        finally:
            connector.dispose()

        assert set(tables) == {"customers", "orders"}
        assert len(tables["customers"]) == 2
        assert len(tables["orders"]) == 2

    def test_row_limit_is_applied(self, external_schema) -> None:
        connector = connect(_connection_url_for_schema(external_schema))
        try:
            tables = sample_tables(connector, row_limit=1)
        finally:
            connector.dispose()
        assert len(tables["orders"]) == 1

    def test_subset_excludes_fk_referenced_tables(self, external_schema) -> None:
        # Selecting a subset must ingest *exactly* that subset. "orders" has an
        # FK to "customers"; SQLAlchemy's reflection would auto-pull the
        # referenced parent table unless FK resolution is disabled — which
        # would silently ingest a table the user did not select.
        connector = connect(_connection_url_for_schema(external_schema))
        try:
            tables = sample_tables(connector, ["orders"])
        finally:
            connector.dispose()
        assert set(tables) == {"orders"}
        assert len(tables["orders"]) == 2
