"""Schema/DDL generation from cleaned+reconciled tables and their approved
relationships — Stage 7.

Pandas dtype -> PostgreSQL type mapping, PK/FK derivation from the approved
relationship graph (schema_discovery orientation: table_b/column_b is always
the primary-key/"one" side), and a topological ordering so referenced
(dimension) tables are created before the tables that reference them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

from schema_discovery.models import RelationshipCandidate

# A letter or underscore, then letters/digits/underscores — Unicode-aware, so an
# Arabic table or column name is a valid identifier (Postgres accepts any Unicode
# letter inside a double-quoted identifier, and this module always quotes).
# `[^\W\d]` is "a word character that isn't a digit", i.e. a letter or underscore.
# Anything that could escape a quoted identifier still fails to match.
_IDENTIFIER_RE = re.compile(r"^[^\W\d]\w*$", re.UNICODE)


class PrimaryKeyViolationError(ValueError):
    """A table's inferred primary-key column has duplicate values.

    Raised before any DDL/COPY runs, so a bad save fails fast with an
    actionable message instead of surfacing as a raw Postgres COPY error
    (e.g. ``duplicate key value violates unique constraint ... line 175``).
    """


def duplicate_primary_key_values(df: pd.DataFrame, pk_col: str) -> list[str]:
    """Values of ``pk_col`` that appear on more than one row of ``df``."""
    dupe_mask = df[pk_col].duplicated(keep=False)
    if not dupe_mask.any():
        return []
    return sorted(df.loc[dupe_mask, pk_col].astype(str).unique())


def _duplicate_composite_key_values(df: pd.DataFrame, pk_cols: list[str]) -> list[str]:
    """Like ``duplicate_primary_key_values`` but for a multi-column
    (composite) primary key — duplicates are combinations of values, not any
    single column's values (which are expected to repeat individually).
    """
    sub = df[pk_cols]
    dupe_mask = sub.duplicated(keep=False)
    if not dupe_mask.any():
        return []
    combos = sub.loc[dupe_mask].astype(str).agg("|".join, axis=1)
    return sorted(combos.unique())


def check_primary_key_uniqueness(
    tables: dict[str, pd.DataFrame], table_ddls: list["TableDDL"]
) -> None:
    """Raise ``PrimaryKeyViolationError`` if any table's primary-key column(s)
    have duplicate values. This is a cheap, final-line-of-defense re-check —
    ``integrity.validation.run_integrity_validation`` is the primary gate and
    should already have blocked anything this would catch — with a message
    that names the table/column(s)/values.
    """
    for ddl in table_ddls:
        pk_cols = ddl.primary_key_columns
        if not pk_cols:
            continue
        df = tables[ddl.name]
        dupes = (
            duplicate_primary_key_values(df, pk_cols[0])
            if len(pk_cols) == 1
            else _duplicate_composite_key_values(df, pk_cols)
        )
        if dupes:
            pk_label = "+".join(pk_cols)
            shown = ", ".join(dupes[:10])
            more = f" (+{len(dupes) - 10} more)" if len(dupes) > 10 else ""
            raise PrimaryKeyViolationError(
                f"Table '{ddl.name}' has duplicate values in its primary-key "
                f"column '{pk_label}': {shown}{more}. Remove or fix these rows "
                "before saving."
            )


def safe_identifier(name: str) -> str:
    if not _IDENTIFIER_RE.match(str(name)):
        raise ValueError(f"Invalid SQL identifier: '{name}'")
    return str(name)


# Unicode-aware: `\w` covers Arabic (and any other script's) letters as well as
# ASCII, so non-Latin names are preserved rather than stripped. Everything that
# could break out of a quoted identifier — quotes, backslash, semicolon,
# whitespace, punctuation — is still replaced, which is what keeps interpolation
# into DDL safe.
_NON_IDENTIFIER_CHARS_RE = re.compile(r"[^\w]+", re.UNICODE)


def sanitize_identifier(name: str) -> str:
    """Best-effort conversion of an arbitrary table/column name (e.g. a raw
    CSV header like "Transaction ID") into a valid quoted SQL identifier —
    unlike ``safe_identifier``, this converts rather than rejects. Real-world
    headers with spaces/punctuation are the common case, not the exception,
    so callers that generate DDL from user data should sanitize once here
    rather than have every source pretend its column names are already safe.

    Non-Latin names are kept intact: Postgres allows any Unicode letter in a
    double-quoted identifier, and every identifier this module emits is quoted.
    Folding Arabic headers to ASCII used to erase them entirely — a whole table
    of columns collapsing to ``column``, ``column_2``, ``column_3`` — which
    silently destroyed the meaning of Arabic datasets.
    """
    s = _NON_IDENTIFIER_CHARS_RE.sub("_", str(name).strip()).strip("_").lower()
    if not s:
        s = "column"
    if s[0].isdigit():
        s = f"_{s}"
    return s


def sanitize_and_dedupe(names: list) -> list[str]:
    """Sanitize a list of column/table names, positionally, appending
    ``_2``, ``_3``, ... wherever sanitization collapses two distinct
    original names into the same identifier (e.g. "Order #" and "Order!"
    both -> "order"). Returns a list the same length/order as ``names``."""
    counts: dict[str, int] = {}
    result: list[str] = []
    for original in names:
        base = sanitize_identifier(original)
        counts[base] = counts.get(base, 0) + 1
        result.append(base if counts[base] == 1 else f"{base}_{counts[base]}")
    return result


def infer_pg_type(series: pd.Series) -> str:
    """Pandas dtype -> PostgreSQL type, per the source spec's mapping table."""
    if pd.api.types.is_bool_dtype(series):
        return "BOOLEAN"
    if pd.api.types.is_integer_dtype(series):
        return "BIGINT"
    if pd.api.types.is_float_dtype(series):
        return "DOUBLE PRECISION"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "TIMESTAMP"

    # object / string / category -> VARCHAR(n) for short values, TEXT for
    # free text (headroom over the observed max length, capped at 255).
    non_null = series.dropna().astype(str)
    if non_null.empty:
        return "TEXT"
    max_len = int(non_null.str.len().max())
    if max_len <= 255:
        return f"VARCHAR({min(255, max(max_len, 1) + 20)})"
    return "TEXT"


@dataclass
class ColumnDDL:
    name: str
    pg_type: str
    primary_key: bool = False
    nullable: bool = True


@dataclass
class ForeignKeyDDL:
    column: str
    ref_table: str
    ref_column: str


@dataclass
class TableDDL:
    name: str
    columns: list[ColumnDDL] = field(default_factory=list)
    foreign_keys: list[ForeignKeyDDL] = field(default_factory=list)
    primary_key_columns: list[str] = field(default_factory=list)

    def to_sql(self, schema: str | None = None) -> str:
        table_ref = (
            f'"{safe_identifier(schema)}"."{safe_identifier(self.name)}"'
            if schema else f'"{safe_identifier(self.name)}"'
        )
        composite_pk = len(self.primary_key_columns) > 1
        lines = []
        for col in self.columns:
            parts = [f'"{safe_identifier(col.name)}"', col.pg_type]
            if col.primary_key and not composite_pk:
                parts.append("PRIMARY KEY")
            elif not col.nullable:
                parts.append("NOT NULL")
            lines.append("    " + " ".join(parts))
        if composite_pk:
            pk_cols_sql = ", ".join(f'"{safe_identifier(c)}"' for c in self.primary_key_columns)
            lines.append(f"    PRIMARY KEY ({pk_cols_sql})")
        for fk in self.foreign_keys:
            ref_table = (
                f'"{safe_identifier(schema)}"."{safe_identifier(fk.ref_table)}"'
                if schema else f'"{safe_identifier(fk.ref_table)}"'
            )
            lines.append(
                f'    FOREIGN KEY ("{safe_identifier(fk.column)}") '
                f'REFERENCES {ref_table} ("{safe_identifier(fk.ref_column)}")'
            )
        cols_sql = ",\n".join(lines)
        return f"CREATE TABLE {table_ref} (\n{cols_sql}\n)"


def primary_keys_from_relationships(
    relationships: list[RelationshipCandidate],
) -> dict[str, str]:
    """table_name -> primary-key column name, derived from the table_b side
    of every approved relationship (table_b is the PK/"one" side per the
    schema-discovery orientation — see schema_discovery/heuristics.py).
    Tables that never appear as table_b get no auto-detected primary key.
    If a table appears as table_b under more than one distinct column (rare),
    the highest-confidence one wins.
    """
    best: dict[str, tuple[float, str]] = {}
    for r in relationships:
        current = best.get(r.table_b)
        if current is None or r.confidence > current[0]:
            best[r.table_b] = (r.confidence, r.column_b)
    return {table: col for table, (_, col) in best.items()}


def _normalize_pk_columns(value: str | list[str] | None) -> list[str]:
    """A table's primary key may be recorded as a bare column name (legacy /
    ``primary_keys_from_relationships`` shape) or a column list (composite
    keys from ``schema_discovery.pk_detection``) — normalize to a list.
    """
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return list(value)


def build_table_ddl(
    name: str,
    df: pd.DataFrame,
    primary_keys: dict[str, str | list[str]],
    relationships: list[RelationshipCandidate],
) -> TableDDL:
    pk_cols = _normalize_pk_columns(primary_keys.get(name))
    columns = [
        ColumnDDL(
            name=str(col),
            pg_type=infer_pg_type(df[col]),
            primary_key=(col in pk_cols),
            nullable=bool(df[col].isna().any()) and col not in pk_cols,
        )
        for col in df.columns
    ]
    foreign_keys = [
        ForeignKeyDDL(column=r.column_a, ref_table=r.table_b, ref_column=r.column_b)
        for r in relationships
        if r.table_a == name
        and _normalize_pk_columns(primary_keys.get(r.table_b)) == [r.column_b]
    ]
    return TableDDL(
        name=name, columns=columns, foreign_keys=foreign_keys, primary_key_columns=pk_cols,
    )


def topological_order(
    table_names: list[str], relationships: list[RelationshipCandidate]
) -> list[str]:
    """Referenced (dimension) tables before tables that reference them —
    Kahn's algorithm on the "table_a depends on table_b" edges.
    """
    deps: dict[str, set[str]] = {name: set() for name in table_names}
    for r in relationships:
        if r.table_a in deps and r.table_b in deps and r.table_a != r.table_b:
            deps[r.table_a].add(r.table_b)

    ordered: list[str] = []
    remaining = {name: set(dep_set) for name, dep_set in deps.items()}
    while remaining:
        ready = sorted(name for name, dep_set in remaining.items() if not dep_set)
        if not ready:
            # A dependency cycle (or something we can't resolve) — break
            # deterministically rather than looping forever.
            ready = [sorted(remaining)[0]]
        for name in ready:
            ordered.append(name)
            del remaining[name]
        for dep_set in remaining.values():
            dep_set.difference_update(ready)
    return ordered


def generate_schema_ddl(
    tables: dict[str, pd.DataFrame],
    relationships: list[RelationshipCandidate] | None,
    *,
    primary_keys: dict[str, str | list[str]] | None = None,
) -> list[TableDDL]:
    """Full schema DDL for a project's tables, in dependency-safe creation order.

    ``primary_keys`` should normally come from Integrity Validation
    (``integrity.validation.IntegrityReport.confirmed_primary_keys``) — only
    columns that passed the 100%-unique / non-null / stable-dtype check
    become a real constraint. If omitted, falls back to the legacy
    relationship-derived heuristic (``primary_keys_from_relationships``) for
    backward compatibility with direct callers/tests.
    """
    relationships = relationships or []
    if primary_keys is None:
        primary_keys = primary_keys_from_relationships(relationships)
    order = topological_order(list(tables), relationships)
    return [build_table_ddl(name, tables[name], primary_keys, relationships) for name in order]


def sanitize_schema_columns(
    tables: dict[str, pd.DataFrame],
    relationships: list[RelationshipCandidate] | None,
    primary_keys: dict[str, str | list[str]] | None,
) -> tuple[dict[str, pd.DataFrame], list[RelationshipCandidate], dict[str, str | list[str]] | None]:
    """Rename every table *and* its columns to valid SQL identifiers and rewrite
    the relationships/primary-keys that reference them to match — the one choke
    point before DDL generation (see ``db.loader.load_project_schema``).

    Real-world column headers ("Transaction ID", "Order #") are the normal
    case, not an edge case, and the cleaning pipeline upstream legitimately
    keeps referencing the *original* names throughout profiling/planning/
    generated code — sanitizing any earlier would break that. This runs once,
    right before the columns become permanent Postgres identifiers.

    Table names are sanitized here too, rather than trusting each ingestion
    source to have done it: uploads go through
    ``ingestion.multi_table._table_name``, but a linked external database keeps
    its own table names verbatim (``ingestion.db_link``), so a table named with
    a space or a non-Latin script used to reach ``safe_identifier`` unsanitized
    and abort the whole save with an unhandled ``ValueError``.
    """
    table_names = list(tables)
    table_rename = dict(zip(table_names, sanitize_and_dedupe(table_names)))

    def _table(name: str) -> str:
        # Relationships/PKs may name a table that isn't in `tables`; sanitize it
        # the same way so the result is still a legal identifier.
        return table_rename.get(name, sanitize_identifier(name))

    # Keyed by ORIGINAL table name, since callers reference columns by the names
    # they knew before this rename.
    rename_maps: dict[str, dict[str, str]] = {}
    renamed_tables: dict[str, pd.DataFrame] = {}
    for name, df in tables.items():
        original_cols = [str(c) for c in df.columns]
        final_cols = sanitize_and_dedupe(original_cols)
        mapping = dict(zip(original_cols, final_cols))
        rename_maps[name] = mapping
        renamed_tables[table_rename[name]] = df.rename(columns=mapping)

    def _rename_pk_cols(table: str, cols: str | list[str]) -> str | list[str]:
        mapping = rename_maps.get(table, {})
        if isinstance(cols, str):
            return mapping.get(cols, cols)
        return [mapping.get(c, c) for c in cols]

    # None means "let generate_schema_ddl derive primary keys from
    # relationships" — must stay None, not become {}, or that fallback
    # silently stops triggering (an empty dict looks like "no PKs at all").
    renamed_pks = (
        None if primary_keys is None
        else {_table(table): _rename_pk_cols(table, cols) for table, cols in primary_keys.items()}
    )

    renamed_rels = [
        r.model_copy(update={
            "table_a": _table(r.table_a),
            "column_a": rename_maps.get(r.table_a, {}).get(r.column_a, r.column_a),
            "table_b": _table(r.table_b),
            "column_b": rename_maps.get(r.table_b, {}).get(r.column_b, r.column_b),
        })
        for r in (relationships or [])
    ]

    return renamed_tables, renamed_rels, renamed_pks
