"""Pydantic data contracts for cross-table schema discovery.

Follows the same convention as ``models/profiler_models.py``: plain
``BaseModel`` + ``Field(description=...)`` for data passed through prompts
and state, not for LLM structured-output parsing.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class TableProfile(BaseModel):
    """Lightweight per-table summary used for the discovery overview."""

    name: str
    row_count: int
    column_count: int
    columns: list[str] = Field(default_factory=list)


class RelationshipCandidate(BaseModel):
    """One candidate foreign-key -> primary-key relationship between two tables."""

    table_a: str = Field(description="Table on the many / foreign-key side")
    column_a: str
    table_b: str = Field(description="Table on the one / primary-key side")
    column_b: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: dict[str, float] = Field(default_factory=dict)
    # "heuristic" (scored deterministically) or "llm" (escalated, low-confidence
    # heuristic score refined/confirmed by an LLM judgment).
    source: str = Field(default="heuristic")
    reasoning: str = Field(default="", description="LLM's justification, when source='llm'")


class SchemaDiscoveryResult(BaseModel):
    """Full output of a schema-discovery pass over a set of tables."""

    tables: list[TableProfile] = Field(default_factory=list)
    candidates: list[RelationshipCandidate] = Field(default_factory=list)
