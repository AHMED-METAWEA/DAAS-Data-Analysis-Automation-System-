"""Saved-report platform table — persists a run's markdown narrative from any
of the report-generating agents (Insights, Forecasting, Marketing, Churn) so
it shows up in the Reports library instead of only living in the browser tab
that generated it.

Lives in the same `public` schema as db/platform_models.py and db/auth_models.py
and is managed by the same Alembic environment (see alembic/env.py).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base

if TYPE_CHECKING:
    # Only for the type hint below — NOT imported at runtime, since
    # db.platform_models imports this module (to register Report on
    # Base.metadata right after defining Project); importing it back here
    # would be circular. The "Project" name is resolved via SQLAlchemy's
    # class registry at mapper-configure time instead.
    from db.platform_models import Project


def _new_id() -> str:
    return uuid.uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Report(Base):
    """A saved markdown report for one project.

    ``type`` is one of "insights" | "forecast" | "marketing" | "churn" —
    matching the LLM-`purpose` strings used elsewhere, not a display label.
    """

    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    project_id: Mapped[str] = mapped_column(String(32), ForeignKey("projects.id"), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    markdown: Mapped[str] = mapped_column(Text, nullable=False)
    grounding: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    project: Mapped["Project"] = relationship(back_populates="reports")
