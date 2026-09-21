"""Engine/session helpers for the platform metadata tables.

Reuses tools.db_tools's connection-string logic so the existing PG_* env vars
stay the single source of truth for how to reach Postgres — no renaming.
"""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from tools.db_tools import get_engine as _get_engine

_sessionmaker: sessionmaker | None = None


def get_engine():
    return _get_engine()


def get_sessionmaker() -> sessionmaker:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)
    return _sessionmaker


def get_session() -> Session:
    return get_sessionmaker()()
