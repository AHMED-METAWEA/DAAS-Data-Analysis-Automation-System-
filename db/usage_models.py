"""LLM token accounting — the fact table behind the usage dashboard.

One row per provider *attempt*, not per logical call: when the fallback chain
tries Groq, gets rate-limited, and succeeds on Anthropic, that is two rows. A
chain quietly running on its second choice is a bill nobody predicted, and
collapsing the attempt into a single "successful" row is exactly what would hide
it. ``fallback_depth`` is what makes that visible.

Deliberately **no cost column.** Token counts are ground truth — the provider
reported them and they never change. A cost is a token count multiplied by a
price that drifts, and a stored cost silently becomes a claim the system can no
longer support. DAAS's whole credibility argument is that it does not state
numbers it cannot back, so cost is computed at read time from a dated price list
(``tools/llm_pricing.py``) and labelled as an estimate, while the thing we can
prove — tokens — is what gets persisted.

Lives in the shared ``public`` schema with the other platform tables and is
managed by the same Alembic environment (see alembic/env.py).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

# Side-effect import: this module's ForeignKey("users.id") and
# ForeignKey("projects.id") strings are resolved from the mapper registry at
# configure time, so both tables must already be registered on Base.metadata.
from db import platform_models  # noqa: F401  (registers Project, and auth_models with it)
from db.base import Base

# Where the call came from. A scheduled briefing spends the schedule owner's
# tokens with nobody watching, so telling it apart from an interactive request
# is the difference between "why is my usage climbing overnight" being
# answerable or not.
SOURCES = ("user", "schedule")


def _new_id() -> str:
    return uuid.uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(UTC)


class LlmUsageEvent(Base):
    """One LLM provider attempt, as billed."""

    __tablename__ = "llm_usage_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id"), nullable=False, index=True
    )
    # Not every call belongs to a project — schema discovery runs before one
    # exists, and account-level calls never have one.
    project_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("projects.id"), nullable=True, index=True
    )
    # Which agent spent it: "insights", "copilot", "forecast", … Matches the
    # purposes in backend/app/api/v1/settings.py so the dashboard can group by
    # the same labels the model picker uses.
    purpose: Mapped[str] = mapped_column(String(40), nullable=False)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)

    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    streamed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # ok | error | cancelled
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ok")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 0 = the first provider in the chain served it.
    fallback_depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="user")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, index=True
    )

    __table_args__ = (
        # Every dashboard query is "this user, this time window", so the
        # composite is what actually gets used; the single-column indexes above
        # serve the FK lookups.
        Index("ix_llm_usage_user_created", "user_id", "created_at"),
    )

    @property
    def counted(self) -> bool:
        """True when the provider actually reported token counts.

        Rows where this is False are real calls whose cost is unknown, not free
        calls — the dashboard has to say so rather than sum them as zero.
        """
        return self.total_tokens > 0
