"""Platform metadata tables — Project, Relationship, ConnectionConfig.

These live in the shared `public` schema (one Postgres instance, schema-per-
project for actual data). This is the only part of the schema Alembic manages;
per-project generated tables evolve via direct ALTER TABLE diffing instead
(see db/schema_evolution.py).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import auth_models  # noqa: F401  (Project.owner_id FKs to users.id — must be
# registered on Base.metadata before this module's FK string 'users.id' is resolved)
from db.base import Base

if TYPE_CHECKING:
    from db.report_models import Report


def _new_id() -> str:
    return uuid.uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(63), nullable=False, unique=True)
    # Nullable for migration safety against any pre-auth rows; new projects
    # always set this from the authenticated request.
    owner_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("users.id"), nullable=True
    )
    # "files" | "database" | "google_sheet"
    data_source_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="files")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    relationships: Mapped[list[Relationship]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    connection_configs: Mapped[list[ConnectionConfig]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    reports: Mapped[list["Report"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )

    @property
    def schema_name(self) -> str:
        return f"project_{self.slug}"


class Relationship(Base):
    """An approved or candidate cross-table relationship for one project."""

    __tablename__ = "relationships"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    project_id: Mapped[str] = mapped_column(String(32), ForeignKey("projects.id"), nullable=False)
    table_a: Mapped[str] = mapped_column(String(255), nullable=False)
    column_a: Mapped[str] = mapped_column(String(255), nullable=False)
    table_b: Mapped[str] = mapped_column(String(255), nullable=False)
    column_b: Mapped[str] = mapped_column(String(255), nullable=False)
    confidence: Mapped[float] = mapped_column(nullable=False, default=0.0)
    # "pending" | "approved" | "rejected"
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    # "auto" | "user"
    approved_by: Mapped[str] = mapped_column(String(20), nullable=False, default="auto")
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    project: Mapped[Project] = relationship(back_populates="relationships")


class ConnectionConfig(Base):
    """Encrypted connection details for a Path B (database) or Path C (sheet) source."""

    __tablename__ = "connection_configs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    project_id: Mapped[str] = mapped_column(String(32), ForeignKey("projects.id"), nullable=False)
    # "postgres" | "mysql" | "mssql" | "google_sheet"
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    encrypted_credentials: Mapped[str] = mapped_column(Text, nullable=False)
    extra: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    project: Mapped[Project] = relationship(back_populates="connection_configs")


# Imported down here (not at the top) so Project already exists in the mapper
# class registry when db/report_models.py's `relationship(back_populates=...)`
# looks up "Project" by name — importing it above would be circular, since
# report_models.py doesn't import this module at runtime (only under
# TYPE_CHECKING). This mirrors why auth_models is imported at the top instead:
# that one only needs to exist before Project's own `ForeignKey("users.id")`
# string resolves, which happens at mapper-configure time, not import time —
# but Report's relationship needs the Project *class* itself registered,
# which requires this module's execution to have already reached this line.
from db import report_models  # noqa: E402,F401  (registers Report on Base.metadata)
