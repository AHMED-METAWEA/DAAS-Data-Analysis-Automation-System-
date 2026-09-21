"""LLM escalation for low-confidence relationship candidates.

Only candidates scoring below ``ESCALATION_THRESHOLD`` are sent to the LLM,
and only the ambiguous columns themselves (names, dtypes, a small sample of
values) — never the full schema — to control token cost.
"""

from __future__ import annotations

import json

import pandas as pd

from agents.constants import DEFAULT_SCHEMA_DISCOVERY_MODEL
from schema_discovery.models import RelationshipCandidate
from tools.llm_client import complete

ESCALATION_THRESHOLD = 0.85

_SYSTEM_PROMPT = """You are a database schema expert. You will be shown two \
columns from two different tables that a heuristic scored as a POSSIBLE \
foreign-key relationship, but with low confidence. Decide whether they \
really represent a relationship (one column referencing the other).

Return valid JSON only, no other text:
{
  "is_relationship": true or false,
  "confidence": <float 0-1>,
  "reasoning": "<one sentence>"
}
"""


def _column_summary(df: pd.DataFrame, col: str) -> dict:
    series = df[col]
    sample = series.dropna().astype(str).unique()[:5].tolist()
    return {
        "column": col,
        "dtype": str(series.dtype),
        "sample_values": sample,
        "null_ratio": round(float(series.isna().mean()), 3) if len(series) else 0.0,
        "unique_ratio": round(float(series.nunique() / len(series)), 3) if len(series) else 0.0,
    }


def escalate(
    candidate: RelationshipCandidate,
    tables: dict[str, pd.DataFrame],
    *,
    model: str | None = None,
) -> RelationshipCandidate:
    """Ask the LLM to judge one ambiguous candidate.

    Returns a new candidate with ``source="llm"`` if the LLM responds
    usefully. On any failure (no provider configured, bad JSON, etc.) this
    fails open and returns the original heuristic candidate unchanged.
    """
    payload = {
        "table_a": candidate.table_a,
        "table_b": candidate.table_b,
        "column_a": _column_summary(tables[candidate.table_a], candidate.column_a),
        "column_b": _column_summary(tables[candidate.table_b], candidate.column_b),
        "heuristic_confidence": candidate.confidence,
        "heuristic_evidence": candidate.evidence,
    }
    try:
        raw = complete(
            "schema_discovery",
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, indent=2, default=str)},
            ],
            model=model or DEFAULT_SCHEMA_DISCOVERY_MODEL,
            temperature=0.1,
            max_tokens=300,
            json_mode=True,
        )
        parsed = json.loads(raw)
        confidence = float(parsed.get("confidence", candidate.confidence))
        if not parsed.get("is_relationship", True):
            confidence = min(candidate.confidence, confidence)
        return candidate.model_copy(update={
            "confidence": max(0.0, min(1.0, confidence)),
            "source": "llm",
            "reasoning": str(parsed.get("reasoning", "")),
        })
    except Exception:
        return candidate


def escalate_low_confidence(
    candidates: list[RelationshipCandidate],
    tables: dict[str, pd.DataFrame],
    *,
    threshold: float = ESCALATION_THRESHOLD,
    model: str | None = None,
) -> list[RelationshipCandidate]:
    """Escalate every candidate scoring below ``threshold``; leave the rest as-is."""
    result = [
        escalate(c, tables, model=model) if c.confidence < threshold else c
        for c in candidates
    ]
    result.sort(key=lambda c: c.confidence, reverse=True)
    return result
