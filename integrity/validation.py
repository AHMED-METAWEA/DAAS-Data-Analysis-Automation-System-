"""Integrity Validation — the mandatory pre-flight gate between Reconciliation
and Constraint Generation.

Nothing downstream of ``run_integrity_validation`` (Constraint Generation /
``db.ddl``, then the Postgres save in ``db.loader``) should ever be reached
while ``IntegrityReport.passed`` is False. The point of this stage is that a
bad save fails here, with a structured, explained report, instead of failing
inside Postgres (or worse, succeeding with silently wrong constraints).

Six checks, matching the requested scope:
  1. Primary keys — uniqueness / nulls / duplicates (``pk_results``,
     ``pk_blocking_issues``).
  2. Foreign keys — orphaned / invalid references (``relationship_checks``).
  3. Relationships — cardinality (one-to-one / one-to-many / many-to-many)
     (``relationship_checks[*].cardinality``).
  4. Data types — inconsistent/mixed-representation columns (``dtype_issues``).
  5. Constraint violations — anything that would make a generated constraint
     fail at COPY time (folded into ``pk_blocking_issues`` /
     ``relationship_checks[*].blocking``).
  6. Duplicate business rows — whole-row duplicates (``duplicate_row_issues``).
"""

from __future__ import annotations

from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field

from reconciliation.normalize import is_numeric_like_series, normalize_key_series
from reconciliation.orphans import DEFAULT_ORPHAN_TOLERANCE
from schema_discovery.models import RelationshipCandidate
from schema_discovery.pk_detection import (
    PrimaryKeyDetectionResult,
    confirmed_primary_keys,
    detect_primary_keys,
)

_SAMPLE_LIMIT = 10

Cardinality = Literal["one_to_one", "one_to_many", "many_to_one", "many_to_many"]


class PkBlockingIssue(BaseModel):
    """A confirmed primary key that (re-checked independently of
    ``pk_detection``) turns out to have duplicates or nulls after all —
    should be structurally unreachable, but this is the safety net that
    doesn't just trust the upstream detector.
    """

    table: str
    columns: list[str]
    duplicate_count: int
    null_count: int


class RelationshipIntegrityResult(BaseModel):
    table_a: str
    column_a: str
    table_b: str
    column_b: str
    orphan_count: int
    orphan_rate: float
    orphan_sample: list = Field(default_factory=list)
    cardinality: Cardinality
    needs_bridge_table: bool = False
    fk_will_be_enforced: bool = False
    blocking: bool = False
    reason: str = ""


class DtypeIssue(BaseModel):
    table: str
    column: str
    issue: str
    sample_values: list = Field(default_factory=list)


class DuplicateRowIssue(BaseModel):
    table: str
    duplicate_row_count: int
    sample_index: list = Field(default_factory=list)


class IntegrityReport(BaseModel):
    passed: bool
    pk_results: dict[str, PrimaryKeyDetectionResult] = Field(default_factory=dict)
    pk_blocking_issues: list[PkBlockingIssue] = Field(default_factory=list)
    relationship_checks: list[RelationshipIntegrityResult] = Field(default_factory=list)
    dtype_issues: list[DtypeIssue] = Field(default_factory=list)
    duplicate_row_issues: list[DuplicateRowIssue] = Field(default_factory=list)
    confirmed_primary_keys: dict[str, list[str]] = Field(default_factory=dict)


def _pk_blocking_issues(
    tables: dict[str, pd.DataFrame], pk_map: dict[str, list[str]]
) -> list[PkBlockingIssue]:
    """Recompute duplicate/null status straight from the DataFrame for
    whatever ended up in ``pk_map`` — deliberately not trusting
    ``pk_results``, so a future regression in ``pk_detection.py`` can't
    silently defeat this gate.
    """
    issues = []
    for table, cols in pk_map.items():
        df = tables.get(table)
        if df is None or not cols or not all(c in df.columns for c in cols):
            continue
        sub = df[cols]
        dup = int(sub.duplicated(keep=False).sum())
        nulls = int(sub.isna().any(axis=1).sum())
        if dup or nulls:
            issues.append(
                PkBlockingIssue(table=table, columns=cols, duplicate_count=dup, null_count=nulls)
            )
    return issues


def _orphan_count_rate_sample(
    fk_values: pd.Series, pk_values: pd.Series, sample_size: int = _SAMPLE_LIMIT
) -> tuple[int, float, list]:
    fk_norm = normalize_key_series(fk_values)
    fk_norm_nonnull = fk_norm.dropna()
    pk_set = set(normalize_key_series(pk_values).dropna())
    if fk_norm_nonnull.empty:
        return 0, 0.0, []
    is_orphan = ~fk_norm_nonnull.isin(pk_set)
    count = int(is_orphan.sum())
    rate = float(is_orphan.mean())
    orphan_idx = fk_norm_nonnull[is_orphan].index
    sample = fk_values.loc[orphan_idx].drop_duplicates().head(sample_size).tolist()
    return count, rate, sample


def _exact_orphans(
    fk_values: pd.Series, pk_values: pd.Series, sample_size: int = _SAMPLE_LIMIT
) -> tuple[int, float, list] | None:
    """Orphans by exact value equality — the comparison a real FOREIGN KEY makes.

    Returns ``None`` for numeric-like keys, where the differing int/float/str
    spellings of the same id are a representation detail that the DDL type
    mapping settles rather than a genuine mismatch; the normalized check is the
    right model there.
    """
    if is_numeric_like_series(fk_values) or is_numeric_like_series(pk_values):
        return None
    fk_nonnull = fk_values.dropna()
    if fk_nonnull.empty:
        return 0, 0.0, []
    pk_set = set(pk_values.dropna())
    is_orphan = ~fk_nonnull.isin(pk_set)
    count = int(is_orphan.sum())
    rate = float(is_orphan.mean())
    sample = fk_nonnull[is_orphan].drop_duplicates().head(sample_size).tolist()
    return count, rate, sample


def _check_relationship(
    r: RelationshipCandidate,
    tables: dict[str, pd.DataFrame],
    pk_map: dict[str, list[str]],
) -> RelationshipIntegrityResult:
    series_a = tables[r.table_a][r.column_a]
    series_b = tables[r.table_b][r.column_b]

    orphan_count, orphan_rate, orphan_sample = _orphan_count_rate_sample(series_a, series_b)

    a_unique = not series_a.dropna().duplicated().any()
    b_unique = not series_b.dropna().duplicated().any()
    if a_unique and b_unique:
        cardinality: Cardinality = "one_to_one"
    elif b_unique:
        cardinality = "one_to_many"
    elif a_unique:
        cardinality = "many_to_one"
    else:
        cardinality = "many_to_many"

    fk_will_be_enforced = pk_map.get(r.table_b) == [r.column_b]
    # A real Postgres FOREIGN KEY constraint has no "tolerance" — a single
    # orphaned row is enough to fail the COPY, so blocking is strict (not
    # rate-based) whenever a constraint is actually about to be generated.
    #
    # It is also enforced on the RAW stored values, which is stricter than the
    # normalized comparison above: keys differing only by case or whitespace
    # count as matching there but are rejected at COPY time. Cleaning runs per
    # table, so a plan that standardises casing on one side of a relationship and
    # not the other produces exactly that. Judging by the normalized count let
    # the gate pass a save that could not possibly succeed, turning a check that
    # exists to prevent DB errors into a ForeignKeyViolation from Postgres.
    blocking = fk_will_be_enforced and orphan_count > 0
    if fk_will_be_enforced:
        exact = _exact_orphans(series_a, series_b)
        if exact is not None and exact[0] > 0:
            # Report the figures the constraint will actually act on.
            orphan_count, orphan_rate, orphan_sample = exact
            blocking = True

    reason = ""
    if blocking:
        reason = (
            f"{orphan_count} row(s) in '{r.table_a}.{r.column_a}' have no matching "
            f"'{r.table_b}.{r.column_b}' value — a foreign-key constraint will be "
            "generated for this relationship and Postgres will reject these rows."
        )
    elif cardinality == "many_to_many":
        reason = (
            f"Neither '{r.table_a}.{r.column_a}' nor '{r.table_b}.{r.column_b}' is "
            "unique — this looks like a many-to-many relationship, which a simple "
            "foreign key can't express. Consider a bridge/junction table."
        )

    return RelationshipIntegrityResult(
        table_a=r.table_a, column_a=r.column_a,
        table_b=r.table_b, column_b=r.column_b,
        orphan_count=orphan_count, orphan_rate=round(orphan_rate, 4),
        orphan_sample=orphan_sample,
        cardinality=cardinality,
        needs_bridge_table=(cardinality == "many_to_many"),
        fk_will_be_enforced=fk_will_be_enforced,
        blocking=blocking,
        reason=reason,
    )


def _is_numeric_like(v: object) -> bool:
    try:
        float(str(v).strip())
        return True
    except (ValueError, TypeError):
        return False


def _dtype_issues(tables: dict[str, pd.DataFrame]) -> list[DtypeIssue]:
    """Flag object-dtype columns that are mostly-but-not-entirely
    numeric-looking — a messy near-numeric ID column like this can hide
    duplicate keys across representations (``1001`` vs ``"1001"``) since
    Postgres will still accept it as text.
    """
    issues = []
    for table, df in tables.items():
        for col in df.columns:
            series = df[col]
            if not (pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series)):
                continue
            vals = series.dropna()
            if vals.empty:
                continue
            looks_numeric = vals.map(_is_numeric_like)
            frac = looks_numeric.mean()
            if 0.9 <= frac < 1.0:
                non_numeric = vals[~looks_numeric]
                issues.append(
                    DtypeIssue(
                        table=table,
                        column=str(col),
                        issue=(
                            f"{frac:.0%} of values look numeric but the column is "
                            f"stored as text; {len(non_numeric)} value(s) don't match."
                        ),
                        sample_values=sorted(non_numeric.astype(str).unique())[:_SAMPLE_LIMIT],
                    )
                )
    return issues


def _duplicate_row_issues(tables: dict[str, pd.DataFrame]) -> list[DuplicateRowIssue]:
    issues = []
    for table, df in tables.items():
        dup_mask = df.duplicated(keep=False)
        count = int(dup_mask.sum())
        if count:
            issues.append(
                DuplicateRowIssue(
                    table=table,
                    duplicate_row_count=count,
                    sample_index=df.index[dup_mask][:_SAMPLE_LIMIT].tolist(),
                )
            )
    return issues


def run_integrity_validation(
    tables: dict[str, pd.DataFrame],
    relationships: list[RelationshipCandidate] | None,
    *,
    orphan_tolerance: float = DEFAULT_ORPHAN_TOLERANCE,
) -> IntegrityReport:
    """Run every integrity check on the final, cleaned+reconciled tables and
    return one structured report. ``orphan_tolerance`` is accepted for API
    symmetry with ``reconciliation.orphans`` but does not loosen blocking —
    see ``_check_relationship`` for why FK enforcement can't be rate-based.
    """
    del orphan_tolerance  # accepted for symmetry; blocking is intentionally strict, see docstring
    relationships = relationships or []

    pk_results = detect_primary_keys(tables)
    pk_map = confirmed_primary_keys(pk_results)
    pk_blocking_issues = _pk_blocking_issues(tables, pk_map)

    relationship_checks = [
        _check_relationship(r, tables, pk_map)
        for r in relationships
        if r.table_a in tables and r.table_b in tables
    ]

    dtype_issues = _dtype_issues(tables)
    duplicate_row_issues = _duplicate_row_issues(tables)

    passed = not pk_blocking_issues and not any(c.blocking for c in relationship_checks)

    return IntegrityReport(
        passed=passed,
        pk_results=pk_results,
        pk_blocking_issues=pk_blocking_issues,
        relationship_checks=relationship_checks,
        dtype_issues=dtype_issues,
        duplicate_row_issues=duplicate_row_issues,
        confirmed_primary_keys=pk_map,
    )
