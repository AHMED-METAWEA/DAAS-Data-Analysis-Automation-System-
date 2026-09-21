"""Shared SQLAlchemy declarative base for platform metadata models.

Only the fixed `public`-schema tables (Project, Relationship, ConnectionConfig)
are registered on this Base and managed by Alembic. Per-project data tables are
generated dynamically at ingestion time (see db/ddl.py) and are intentionally
not part of this metadata.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
