"""In-memory Data Cleaning pipeline sessions.

The Data Cleaning workflow (source -> profile -> schema discovery ->
relationships -> plan -> clean -> validate -> reconcile -> integrity -> save)
is a multi-request HITL flow. Streamlit holds all of this in
``st.session_state`` — also single-process, in-memory. This module is the
API's equivalent: nothing is durable until the explicit "save" step writes to
Postgres via db.loader.load_project_schema, exactly matching today's
durability guarantee.

Known limitation (documented, not hidden): this is a single-process store.
A multi-instance deployment would need to swap this dict for Redis (or
similar) keyed the same way. For a single-uvicorn-worker deployment (the
documented run command), this is exactly as durable as the Streamlit app it
replaces.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd

from schema_discovery.models import RelationshipCandidate

_SESSION_TTL = timedelta(hours=2)


@dataclass
class TableCleaningState:
    """Per-table HITL cleaning state (Data Cleaning page's phase machine)."""

    phase: str = "idle"  # idle -> profiled -> planned -> cleaned -> validated -> done
    profile: dict[str, Any] | None = None
    cleaning_plan: list[dict[str, Any]] | None = None
    generated_code: str | None = None
    cleaned_df: pd.DataFrame | None = None
    validation_report: dict[str, Any] | None = None
    transformation_log: list[str] = field(default_factory=list)
    retry_count: int = 0
    last_error: str | None = None
    # Measured per-step audit trail from tools.cleaning_ops.apply_plan.
    ledger: dict[str, Any] | None = None


@dataclass
class PipelineSession:
    id: str
    project_id: str
    raw_tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    primary_table: str = ""
    # Relationships already known with certainty before Schema Discovery runs
    # — e.g. real FK constraints read directly off a linked external database
    # (ingestion/db_link.py::introspect_relationships). Per the trust
    # hierarchy (existing DB constraints > heuristics > LLM), these are
    # merged into the discovery result at confidence 1.0 rather than
    # re-derived heuristically, which would score them lower (or miss them
    # entirely) for oddly-named FK columns.
    known_relationships: list[RelationshipCandidate] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_touched_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    schema_discovery: dict[str, Any] | None = None
    relationship_decisions: list[dict[str, Any]] = field(default_factory=list)
    cleaning: dict[str, TableCleaningState] = field(default_factory=dict)
    reconciliation: dict[str, Any] | None = None
    integrity: dict[str, Any] | None = None
    saved: bool = False


class PipelineSessionNotFoundError(KeyError):
    pass


class _PipelineSessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, PipelineSession] = {}
        self._lock = threading.Lock()

    def _evict_expired(self) -> None:
        cutoff = datetime.now(UTC) - _SESSION_TTL
        expired = [sid for sid, s in self._sessions.items() if s.last_touched_at < cutoff]
        for sid in expired:
            del self._sessions[sid]

    def create(
        self,
        project_id: str,
        tables: dict[str, pd.DataFrame],
        primary_table: str,
        known_relationships: list[RelationshipCandidate] | None = None,
    ) -> PipelineSession:
        with self._lock:
            self._evict_expired()
            session = PipelineSession(
                id=uuid.uuid4().hex,
                project_id=project_id,
                raw_tables=tables,
                primary_table=primary_table,
                known_relationships=known_relationships or [],
                cleaning={name: TableCleaningState() for name in tables},
            )
            self._sessions[session.id] = session
            return session

    def get(self, session_id: str) -> PipelineSession:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise PipelineSessionNotFoundError(session_id)
            session.last_touched_at = datetime.now(UTC)
            return session

    def delete(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)


_store = _PipelineSessionStore()


def create_session(
    project_id: str,
    tables: dict[str, pd.DataFrame],
    primary_table: str,
    known_relationships: list[RelationshipCandidate] | None = None,
) -> PipelineSession:
    return _store.create(project_id, tables, primary_table, known_relationships)


def get_session(session_id: str) -> PipelineSession:
    return _store.get(session_id)


def delete_session(session_id: str) -> None:
    _store.delete(session_id)
