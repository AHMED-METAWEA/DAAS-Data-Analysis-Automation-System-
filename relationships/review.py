"""Confidence-gated human-in-the-loop review for candidate relationships.

Bucketing follows the source spec:
  - confidence >= AUTO_APPROVE_THRESHOLD  -> auto-approved, shown collapsed,
    the user can still expand and override.
  - REVIEW_THRESHOLD <= confidence < AUTO_APPROVE_THRESHOLD -> shown for
    explicit approval, pre-filled with the best guess (approve).
  - confidence < REVIEW_THRESHOLD -> flagged for manual mapping; NOT
    pre-approved, the user must actively pick a match (or none).
"""

from __future__ import annotations

import pandas as pd

from db.platform_models import Relationship
from db.session import get_session
from schema_discovery.models import RelationshipCandidate

AUTO_APPROVE_THRESHOLD = 0.85
REVIEW_THRESHOLD = 0.5


def bucket_candidates(
    candidates: list[RelationshipCandidate],
) -> dict[str, list[RelationshipCandidate]]:
    auto, review, manual = [], [], []
    for c in candidates:
        if c.confidence >= AUTO_APPROVE_THRESHOLD:
            auto.append(c)
        elif c.confidence >= REVIEW_THRESHOLD:
            review.append(c)
        else:
            manual.append(c)
    return {"auto": auto, "review": review, "manual": manual}


def validate_relationship(candidate: RelationshipCandidate) -> dict:
    """Human-readable validation stats derived from the candidate's evidence
    (already computed during schema discovery — see schema_discovery/heuristics.py),
    covering the spec's four checks: PK uniqueness, FK existence (coverage),
    orphan ratio, and dtype compatibility.
    """
    ev = candidate.evidence
    fk_coverage = ev.get("fk_coverage", 0.0)
    return {
        "pk_uniqueness": ev.get("pk_uniqueness", 0.0),
        "fk_coverage": fk_coverage,
        "orphan_ratio": round(1.0 - fk_coverage, 3),
        "dtype_compatible": ev.get("dtype_compatibility", 0.0) >= 0.6,
    }


def manual_match_options(
    candidate: RelationshipCandidate, tables: dict[str, pd.DataFrame]
) -> list[str]:
    """Columns in table_b the user could pick as the real match for
    candidate.column_a — backs the manual-mapping dropdown."""
    return list(tables[candidate.table_b].columns)


def persist_relationships(project_id: str, approved: list[RelationshipCandidate]) -> None:
    """Replace this project's stored relationships with the approved set."""
    with get_session() as session:
        session.query(Relationship).filter(Relationship.project_id == project_id).delete()
        for c in approved:
            session.add(Relationship(
                project_id=project_id,
                table_a=c.table_a, column_a=c.column_a,
                table_b=c.table_b, column_b=c.column_b,
                confidence=c.confidence,
                status="approved",
                approved_by="auto" if c.confidence >= AUTO_APPROVE_THRESHOLD else "user",
                evidence=c.evidence,
            ))
        session.commit()


def load_relationships(project_id: str) -> list[Relationship]:
    with get_session() as session:
        return list(
            session.query(Relationship)
            .filter(Relationship.project_id == project_id, Relationship.status == "approved")
            .all()
        )
