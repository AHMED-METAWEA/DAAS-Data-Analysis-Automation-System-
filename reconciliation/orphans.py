"""Before/after orphan-rate checks for approved cross-table relationships.

An "orphan" is a foreign-key value that doesn't exist among the primary-key
side's values. Cleaning each table independently can silently introduce new
orphans (e.g. one side's ID gets reformatted, the other doesn't) — this
measures that delta and gates on a tolerance, surfacing a sample of the
actual unmatched values for human review rather than a bare percentage.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from reconciliation.normalize import normalize_key_series, normalize_relationship_keys
from schema_discovery.models import RelationshipCandidate

DEFAULT_ORPHAN_TOLERANCE = 0.02  # allow up to a 2-percentage-point increase


def orphan_rate_and_sample(
    fk_values: pd.Series, pk_values: pd.Series, sample_size: int = 10
) -> tuple[float, list]:
    fk_norm = normalize_key_series(fk_values)
    fk_norm_nonnull = fk_norm.dropna()
    pk_set = set(normalize_key_series(pk_values).dropna())
    if fk_norm_nonnull.empty:
        return 0.0, []
    is_orphan = ~fk_norm_nonnull.isin(pk_set)
    rate = float(is_orphan.mean())
    orphan_idx = fk_norm_nonnull[is_orphan].index
    sample = fk_values.loc[orphan_idx].drop_duplicates().head(sample_size).tolist()
    return rate, sample


@dataclass
class ReconciliationCheck:
    relationship: RelationshipCandidate
    orphan_rate_before: float
    orphan_rate_after: float
    orphan_sample_after: list = field(default_factory=list)

    @property
    def delta(self) -> float:
        return self.orphan_rate_after - self.orphan_rate_before

    def within_tolerance(self, tolerance: float = DEFAULT_ORPHAN_TOLERANCE) -> bool:
        return self.delta <= tolerance


def check_relationship(
    relationship: RelationshipCandidate,
    raw_tables: dict[str, pd.DataFrame],
    cleaned_tables: dict[str, pd.DataFrame],
) -> ReconciliationCheck:
    """Compare orphan rate before (raw) vs after (cleaned+normalized) for one
    approved relationship."""
    before_rate, _ = orphan_rate_and_sample(
        raw_tables[relationship.table_a][relationship.column_a],
        raw_tables[relationship.table_b][relationship.column_b],
    )
    after_rate, after_sample = orphan_rate_and_sample(
        cleaned_tables[relationship.table_a][relationship.column_a],
        cleaned_tables[relationship.table_b][relationship.column_b],
    )
    return ReconciliationCheck(
        relationship=relationship,
        orphan_rate_before=before_rate,
        orphan_rate_after=after_rate,
        orphan_sample_after=after_sample,
    )


def reconcile(
    relationships: list[RelationshipCandidate],
    raw_tables: dict[str, pd.DataFrame],
    cleaned_tables: dict[str, pd.DataFrame],
) -> tuple[dict[str, pd.DataFrame], list[ReconciliationCheck]]:
    """Normalize every approved relationship's key columns identically across
    both sides, then re-check orphan rates before vs. after cleaning.

    Returns ``(normalized_tables, checks)`` — ``normalized_tables`` is
    ``cleaned_tables`` with key columns overwritten by their canonical
    normalized form (ready for Stage 7 storage); ``checks`` reports, per
    relationship, whether cleaning introduced new orphans.
    """
    normalized = dict(cleaned_tables)
    applicable = [
        rel for rel in relationships
        if rel.table_a in normalized and rel.table_b in normalized
    ]
    for rel in applicable:
        normalized = normalize_relationship_keys(normalized, rel)

    checks = [
        check_relationship(rel, raw_tables, normalized)
        for rel in applicable
        if rel.table_a in raw_tables and rel.table_b in raw_tables
    ]
    return normalized, checks


def failing_checks(
    checks: list[ReconciliationCheck], tolerance: float = DEFAULT_ORPHAN_TOLERANCE
) -> list[ReconciliationCheck]:
    return [c for c in checks if not c.within_tolerance(tolerance)]
