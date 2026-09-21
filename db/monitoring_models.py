"""Autonomous-monitoring platform tables.

The product change these tables encode: the platform stops waiting to be asked.
A :class:`Schedule` says *when* to look and *what to look for*; a
:class:`MonitorRun` records one execution; an :class:`Alert` is a single finding
worth a person's attention, ranked by money at stake; a
:class:`NotificationChannel` is where findings are pushed; a
:class:`DeliveryLog` records whether they actually arrived.

:class:`MetricSnapshot` is the piece that makes any of it meaningful over time.
Comparing today against yesterday requires yesterday to have been *recorded* —
recomputing it from the current dataset would silently answer a different
question every time the data is re-cleaned or backfilled.

Lives in the same ``public`` schema as the other platform tables and is managed
by the same Alembic environment (see alembic/env.py).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

# Side-effect import: ``Schedule.project`` names its target as the string
# "Project", resolved from the mapper registry at configuration time. Importing
# this module without platform_models having been imported first configures the
# mapper with no Project class registered and raises "expression 'Project'
# failed to locate a name" on the first query. In the running app the ordering
# in db/init_platform.py happened to hide that; anything importing this module
# on its own (a test, a script, a worker) hit it.
from db import platform_models  # noqa: F401  (registers Project on Base.metadata)
from db.base import Base

if TYPE_CHECKING:
    from db.platform_models import Project


def _new_id() -> str:
    return uuid.uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(UTC)


# ── Vocabulary, kept in one place so the API, the runner and the UI agree ────

FREQUENCIES = ("daily", "weekdays", "weekly", "monthly", "hourly", "custom")
RUN_STATUSES = ("pending", "running", "success", "failed", "skipped")
ALERT_SEVERITIES = ("critical", "high", "medium", "low", "info")
ALERT_STATUSES = ("new", "acknowledged", "resolved", "muted")
CHANNEL_TYPES = ("in_app", "email", "telegram", "whatsapp")
DELIVERY_STATUSES = ("pending", "sent", "failed", "skipped")


class NotificationChannel(Base):
    """Where a briefing is delivered, and the credentials to get it there.

    Credentials are Fernet-encrypted with the same key and helpers as the
    external-database connections (see db/connection_configs.py) — a Telegram
    bot token and a Postgres password deserve the same treatment, and having two
    encryption stories in one codebase is how one of them ends up unused.
    """

    __tablename__ = "notification_channels"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id"), nullable=False, index=True
    )
    # in_app | email | telegram | whatsapp
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Non-secret display value: the email address, the @handle, the phone number.
    # Shown in the UI so a user can tell two channels apart without decrypting.
    destination: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    encrypted_credentials: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Non-secret settings: whatsapp provider choice, SMTP host/port, etc.
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Result of the last "send me a test message" — the only honest way to know
    # a channel works before a 07:00 briefing depends on it.
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class Schedule(Base):
    """One project's standing instruction: when to look, and what matters."""

    __tablename__ = "monitor_schedules"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    project_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("projects.id"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # ── When ───────────────────────────────────────────────────────────────
    # daily | weekdays | weekly | monthly | hourly | custom
    frequency: Mapped[str] = mapped_column(String(20), nullable=False, default="daily")
    hour: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    minute: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 0 = Monday (weekly); 1-28 (monthly). Ignored otherwise.
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    day_of_month: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # A real cron expression, for the "custom" frequency. Everything else is
    # translated into one at scheduling time, so there is a single code path.
    cron_expression: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # IANA name. Stored per schedule, not per user: an owner with a Cairo shop
    # and a Dubai shop needs 07:00 to mean two different instants.
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")

    # ── What to look for ───────────────────────────────────────────────────
    # Which analyses to run: any of "insights", "root_cause", "forecast", "churn".
    analyses: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # Rule configuration — thresholds, targets, which metrics to watch.
    # See monitoring/rules.py for the shape and the defaults.
    rules: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Only alerts at or above this severity are delivered. Everything found is
    # still stored, so raising the bar never destroys history.
    min_severity: Mapped[str] = mapped_column(String(20), nullable=False, default="medium")
    # Suppress a repeat of the same finding for this many hours. Without it, a
    # standing problem produces an identical alert every morning until the owner
    # stops reading them — the single fastest way to make monitoring useless.
    cooldown_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=24)
    max_alerts_per_run: Mapped[int] = mapped_column(Integer, nullable=False, default=5)

    # ── Where ──────────────────────────────────────────────────────────────
    channel_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    language: Mapped[str] = mapped_column(String(5), nullable=False, default="en")
    # Skip delivery outside these hours (local to `timezone`); alerts are still
    # recorded and are delivered with the next in-window run.
    quiet_hours_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quiet_hours_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Send the briefing even when nothing crossed a threshold. Off by default:
    # a notification that says "nothing happened" trains people to ignore the
    # channel, which costs more than it gives.
    send_when_nothing_found: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    project: Mapped["Project"] = relationship()
    runs: Mapped[list[MonitorRun]] = relationship(
        back_populates="schedule", cascade="all, delete-orphan",
    )


class MonitorRun(Base):
    """One execution of a schedule — including the ones that found nothing.

    Failed and empty runs are recorded as deliberately as successful ones. "The
    monitor has not alerted" and "the monitor has not run for six days" look
    identical to a user otherwise, and only one of them is good news.
    """

    __tablename__ = "monitor_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    schedule_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("monitor_schedules.id"), nullable=False, index=True
    )
    project_id: Mapped[str] = mapped_column(String(32), ForeignKey("projects.id"), nullable=False)
    # pending | running | success | failed | skipped
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    # "schedule" | "manual" — a run-now must be distinguishable in the history.
    trigger: Mapped[str] = mapped_column(String(20), nullable=False, default="schedule")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    alerts_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    alerts_delivered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Total money at stake across this run's alerts — the headline the run
    # history is sorted and skimmed by.
    money_at_stake: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    briefing_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Per-analysis notes: what ran, what was skipped and why.
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    schedule: Mapped[Schedule] = relationship(back_populates="runs")
    alerts: Mapped[list[Alert]] = relationship(
        back_populates="run", cascade="all, delete-orphan",
    )


class Alert(Base):
    """One finding worth someone's attention."""

    __tablename__ = "monitor_alerts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    run_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("monitor_runs.id"), nullable=False, index=True
    )
    project_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("projects.id"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id"), nullable=False, index=True
    )
    # Stable identity of the *finding*, not of this instance: same rule, same
    # metric, same slice. Cooldown and de-duplication key off this, which is
    # what stops one standing problem from generating a daily identical alert.
    fingerprint: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    rule: Mapped[str] = mapped_column(String(64), nullable=False)
    # critical | high | medium | low | info
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="medium")
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    body_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    metric: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    # The ranking key. Deliberately the same currency-denominated quantity the
    # insights evidence engine ranks by, so "what the platform thinks matters"
    # means one thing across the product rather than three.
    money_at_stake: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    current_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    prior_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    change_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Every figure quoted in the alert, with its formula — the same audit trail
    # the insights report carries, so an alert can be checked, not just believed.
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Attached root-cause result, when the drill-down found a segment.
    root_cause: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # new | acknowledged | resolved | muted
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="new")
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )

    run: Mapped[MonitorRun] = relationship(back_populates="alerts")
    deliveries: Mapped[list[DeliveryLog]] = relationship(
        back_populates="alert", cascade="all, delete-orphan",
    )


class DeliveryLog(Base):
    """Whether a briefing actually arrived, per channel.

    A monitoring system that cannot say "the WhatsApp send failed at 07:00" is
    indistinguishable from one that had nothing to say.
    """

    __tablename__ = "monitor_deliveries"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    run_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("monitor_runs.id"), nullable=False, index=True
    )
    alert_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("monitor_alerts.id"), nullable=True
    )
    channel_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("notification_channels.id"), nullable=True
    )
    channel_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # pending | sent | failed | skipped
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    destination: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    alert: Mapped[Alert | None] = relationship(back_populates="deliveries")


class MetricSnapshot(Base):
    """One project's headline metrics as they stood at one point in time.

    This is what turns a report generator into a monitor.  "Revenue is down 12%
    on last week" needs last week's number to have been *recorded* — recomputing
    it from the current dataset answers a subtly different question every time
    the data is re-cleaned, backfilled or re-joined, and the difference shows up
    as phantom alerts nobody can reproduce.
    """

    __tablename__ = "metric_snapshots"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    project_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("projects.id"), nullable=False, index=True
    )
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
    # The metric family: "kpi" (revenue, orders, aov…), "quality", "churn".
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="kpi")
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Row count and date span of the data the snapshot was taken from, so a
    # jump caused by an import can be told apart from a jump in the business.
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    data_last_date: Mapped[str | None] = mapped_column(String(32), nullable=True)


Index("ix_snapshot_project_kind_time", MetricSnapshot.project_id, MetricSnapshot.kind,
      MetricSnapshot.captured_at)
Index("ix_alert_user_status_time", Alert.user_id, Alert.status, Alert.created_at)
Index("ix_alert_fingerprint_time", Alert.fingerprint, Alert.created_at)
