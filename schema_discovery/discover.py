"""Entry point: run full schema discovery over a set of tables.

Heuristics first, LLM escalation only for low-confidence candidates — see
``heuristics.py`` and ``llm_escalation.py`` for the two passes.
"""

from __future__ import annotations

import pandas as pd

from schema_discovery.heuristics import find_relationship_candidates, profile_tables
from schema_discovery.llm_escalation import ESCALATION_THRESHOLD, escalate_low_confidence
from schema_discovery.models import SchemaDiscoveryResult


def run_schema_discovery(
    tables: dict[str, pd.DataFrame],
    *,
    escalate: bool = True,
    threshold: float = ESCALATION_THRESHOLD,
    model: str | None = None,
) -> SchemaDiscoveryResult:
    table_profiles = profile_tables(tables)
    candidates = find_relationship_candidates(tables)
    if escalate and candidates:
        candidates = escalate_low_confidence(candidates, tables, threshold=threshold, model=model)
    return SchemaDiscoveryResult(tables=table_profiles, candidates=candidates)
