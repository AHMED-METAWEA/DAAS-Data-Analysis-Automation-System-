"""In-memory Analyst Copilot conversation sessions.

Mirrors ``pipeline_sessions.py``'s pattern exactly: a lightweight,
single-process, TTL-evicted in-memory store — exactly as durable as the
per-uvicorn-worker deployment this app documents (see that module's
docstring for the full reasoning).

Holds the last tool call's structured result so a follow-up turn ("why is
that", "explain that number", "that doesn't match what I expected") can be
answered by re-examining already-computed data instead of either
(a) blindly re-routing to a fresh, possibly expensive tool call, or
(b) round-tripping the full structured payload through the client on every
request just so the server can hand it back to itself next turn.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

_SESSION_TTL = timedelta(hours=1)


@dataclass
class CopilotSession:
    id: str
    project_id: str
    last_route: str | None = None
    last_tool_result: dict[str, Any] | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_touched_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class _CopilotSessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, CopilotSession] = {}
        self._lock = threading.Lock()

    def _evict_expired(self) -> None:
        cutoff = datetime.now(UTC) - _SESSION_TTL
        expired = [sid for sid, s in self._sessions.items() if s.last_touched_at < cutoff]
        for sid in expired:
            del self._sessions[sid]

    def get_or_create(self, session_id: str | None, project_id: str) -> CopilotSession:
        with self._lock:
            self._evict_expired()
            if session_id:
                existing = self._sessions.get(session_id)
                # A session that expired, was never created, or (defensively)
                # belongs to a different project is never trusted — start a
                # fresh one under the same id's absence rather than serving
                # another project's cached data.
                if existing is not None and existing.project_id == project_id:
                    existing.last_touched_at = datetime.now(UTC)
                    return existing
            session = CopilotSession(id=session_id or uuid.uuid4().hex, project_id=project_id)
            self._sessions[session.id] = session
            return session

    def update(self, session_id: str, *, route: str, tool_result: dict[str, Any] | None) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return
            session.last_route = route
            session.last_tool_result = tool_result
            session.last_touched_at = datetime.now(UTC)


_store = _CopilotSessionStore()


def get_or_create_session(session_id: str | None, project_id: str) -> CopilotSession:
    return _store.get_or_create(session_id, project_id)


def update_session(session_id: str, *, route: str, tool_result: dict[str, Any] | None) -> None:
    _store.update(session_id, route=route, tool_result=tool_result)
