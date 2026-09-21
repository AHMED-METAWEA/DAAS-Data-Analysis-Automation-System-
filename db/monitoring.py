"""Repository helpers for the autonomous-monitoring tables.

Session-per-call, matching db/reports.py: the scheduler runs outside any HTTP
request, so a repository that depended on a request-scoped session would work in
the API and quietly fail at 07:00.

Objects are returned detached, so ``expire_on_commit=False`` on the sessionmaker
(db/session.py) is what makes their attributes readable after the session
closes — the same contract the rest of the repository layer relies on.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from db.monitoring_models import (
    Alert,
    DeliveryLog,
    MetricSnapshot,
    MonitorRun,
    NotificationChannel,
    Schedule,
)
from db.platform_models import Project
from db.session import get_session


def _utcnow() -> datetime:
    return datetime.now(UTC)


# ── Channels ────────────────────────────────────────────────────────────────

def create_channel(user_id: str, **fields) -> NotificationChannel:
    with get_session() as session:
        channel = NotificationChannel(user_id=user_id, **fields)
        session.add(channel)
        session.commit()
        session.refresh(channel)
        return channel


def list_channels(user_id: str) -> list[NotificationChannel]:
    with get_session() as session:
        stmt = (
            select(NotificationChannel)
            .where(NotificationChannel.user_id == user_id)
            .order_by(NotificationChannel.created_at)
        )
        return list(session.scalars(stmt))


def get_channel(channel_id: str) -> NotificationChannel | None:
    with get_session() as session:
        return session.get(NotificationChannel, channel_id)


def get_channels(channel_ids: list[str]) -> list[NotificationChannel]:
    if not channel_ids:
        return []
    with get_session() as session:
        stmt = select(NotificationChannel).where(NotificationChannel.id.in_(channel_ids))
        by_id = {c.id: c for c in session.scalars(stmt)}
    # Preserve the caller's order — the schedule lists channels in the order the
    # user arranged them, and delivery follows it.
    return [by_id[cid] for cid in channel_ids if cid in by_id]


def update_channel(channel_id: str, **fields) -> NotificationChannel | None:
    """Apply every field given. See :func:`update_schedule` for why not "skip None"."""
    with get_session() as session:
        channel = session.get(NotificationChannel, channel_id)
        if channel is None:
            return None
        for key, value in fields.items():
            setattr(channel, key, value)
        session.commit()
        session.refresh(channel)
        return channel


def delete_channel(channel_id: str) -> bool:
    with get_session() as session:
        channel = session.get(NotificationChannel, channel_id)
        if channel is None:
            return False
        session.delete(channel)
        session.commit()
        return True


# ── Schedules ───────────────────────────────────────────────────────────────

def create_schedule(project_id: str, user_id: str, **fields) -> Schedule:
    with get_session() as session:
        schedule = Schedule(project_id=project_id, user_id=user_id, **fields)
        session.add(schedule)
        session.commit()
        session.refresh(schedule)
        return schedule


def get_schedule(schedule_id: str) -> Schedule | None:
    with get_session() as session:
        return session.get(Schedule, schedule_id)


def list_schedules_for_user(user_id: str) -> list[tuple[Schedule, Project]]:
    with get_session() as session:
        stmt = (
            select(Schedule, Project)
            .join(Project, Schedule.project_id == Project.id)
            .where(Schedule.user_id == user_id)
            .order_by(Schedule.created_at.desc())
        )
        return [(s, p) for s, p in session.execute(stmt)]


def list_active_schedules() -> list[Schedule]:
    """Every schedule the scheduler should have a job for."""
    with get_session() as session:
        stmt = select(Schedule).where(Schedule.is_active.is_(True))
        return list(session.scalars(stmt))


def update_schedule(schedule_id: str, **fields) -> Schedule | None:
    """Apply every field given, ``None`` included.

    Deliberately NOT "skip None". That convention looks defensive and is a
    silent-failure generator: ``None`` is a legitimate value for half of these
    columns — clearing quiet hours, dropping a cron expression when switching
    away from a custom frequency, unsetting ``next_run_at`` — and skipping it
    means the user presses Save, the request succeeds, and nothing changes.

    "The caller didn't supply this field" is already expressed one layer up, by
    Pydantic's ``exclude_unset=True``. Encoding it a second time here, in a way
    that cannot distinguish "not supplied" from "supplied as null", is what
    creates the bug.
    """
    with get_session() as session:
        schedule = session.get(Schedule, schedule_id)
        if schedule is None:
            return None
        for key, value in fields.items():
            setattr(schedule, key, value)
        session.commit()
        session.refresh(schedule)
        return schedule


def mark_schedule_ran(
    schedule_id: str, status: str, next_run_at: datetime | None = None,
) -> None:
    with get_session() as session:
        schedule = session.get(Schedule, schedule_id)
        if schedule is None:
            return
        schedule.last_run_at = _utcnow()
        schedule.last_status = status
        schedule.next_run_at = next_run_at
        session.commit()


def delete_schedule(schedule_id: str) -> bool:
    with get_session() as session:
        schedule = session.get(Schedule, schedule_id)
        if schedule is None:
            return False
        session.delete(schedule)
        session.commit()
        return True


# ── Runs ────────────────────────────────────────────────────────────────────

def start_run(schedule_id: str, project_id: str, trigger: str = "schedule") -> MonitorRun:
    with get_session() as session:
        run = MonitorRun(
            schedule_id=schedule_id, project_id=project_id,
            trigger=trigger, status="running",
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        return run


def finish_run(run_id: str, **fields) -> MonitorRun | None:
    with get_session() as session:
        run = session.get(MonitorRun, run_id)
        if run is None:
            return None
        for key, value in fields.items():
            setattr(run, key, value)
        run.finished_at = _utcnow()
        started = run.started_at
        if started is not None:
            if started.tzinfo is None:
                started = started.replace(tzinfo=UTC)
            run.duration_ms = int((run.finished_at - started).total_seconds() * 1000)
        session.commit()
        session.refresh(run)
        return run


def list_runs(schedule_id: str, limit: int = 50) -> list[MonitorRun]:
    with get_session() as session:
        stmt = (
            select(MonitorRun)
            .where(MonitorRun.schedule_id == schedule_id)
            .order_by(MonitorRun.started_at.desc())
            .limit(limit)
        )
        return list(session.scalars(stmt))


def list_runs_for_user(user_id: str, limit: int = 50) -> list[tuple[MonitorRun, Schedule, Project]]:
    with get_session() as session:
        stmt = (
            select(MonitorRun, Schedule, Project)
            .join(Schedule, MonitorRun.schedule_id == Schedule.id)
            .join(Project, MonitorRun.project_id == Project.id)
            .where(Schedule.user_id == user_id)
            .order_by(MonitorRun.started_at.desc())
            .limit(limit)
        )
        return [(r, s, p) for r, s, p in session.execute(stmt)]


def get_run(run_id: str) -> MonitorRun | None:
    with get_session() as session:
        return session.get(MonitorRun, run_id)


# ── Alerts ──────────────────────────────────────────────────────────────────

def save_alerts(alerts: list[Alert]) -> list[Alert]:
    if not alerts:
        return []
    with get_session() as session:
        session.add_all(alerts)
        session.commit()
        for alert in alerts:
            session.refresh(alert)
        return alerts


def recent_fingerprints(project_id: str, since_hours: int) -> set[str]:
    """Findings already raised for this project inside the cooldown window.

    The cooldown is what separates monitoring from nagging: a margin leak that
    has not been fixed is still true tomorrow, and saying so every morning is
    how a channel gets muted.
    """
    cutoff = _utcnow() - timedelta(hours=max(0, since_hours))
    with get_session() as session:
        stmt = (
            select(Alert.fingerprint)
            .where(Alert.project_id == project_id, Alert.created_at >= cutoff)
            .distinct()
        )
        return set(session.scalars(stmt))


def list_alerts_for_user(
    user_id: str, *, status: str | None = None, limit: int = 100,
) -> list[tuple[Alert, Project]]:
    with get_session() as session:
        stmt = (
            select(Alert, Project)
            .join(Project, Alert.project_id == Project.id)
            .where(Alert.user_id == user_id)
        )
        if status:
            stmt = stmt.where(Alert.status == status)
        stmt = stmt.order_by(Alert.created_at.desc()).limit(limit)
        return [(a, p) for a, p in session.execute(stmt)]


def list_alerts_for_run(run_id: str) -> list[Alert]:
    with get_session() as session:
        stmt = (
            select(Alert)
            .where(Alert.run_id == run_id)
            .order_by(Alert.money_at_stake.desc())
        )
        return list(session.scalars(stmt))


def count_unread_alerts(user_id: str) -> int:
    with get_session() as session:
        stmt = (
            select(func.count())
            .select_from(Alert)
            .where(Alert.user_id == user_id, Alert.status == "new")
        )
        return int(session.scalar(stmt) or 0)


def set_alert_status(alert_id: str, user_id: str, status: str) -> Alert | None:
    with get_session() as session:
        alert = session.get(Alert, alert_id)
        if alert is None or alert.user_id != user_id:
            return None
        alert.status = status
        if status != "new" and alert.read_at is None:
            alert.read_at = _utcnow()
        session.commit()
        session.refresh(alert)
        return alert


def mark_all_alerts_read(user_id: str) -> int:
    with get_session() as session:
        stmt = select(Alert).where(Alert.user_id == user_id, Alert.status == "new")
        alerts = list(session.scalars(stmt))
        for alert in alerts:
            alert.status = "acknowledged"
            alert.read_at = _utcnow()
        session.commit()
        return len(alerts)


# ── Deliveries ──────────────────────────────────────────────────────────────

def save_deliveries(deliveries: list[DeliveryLog]) -> list[DeliveryLog]:
    if not deliveries:
        return []
    with get_session() as session:
        session.add_all(deliveries)
        session.commit()
        for delivery in deliveries:
            session.refresh(delivery)
        return deliveries


def list_deliveries_for_run(run_id: str) -> list[DeliveryLog]:
    with get_session() as session:
        stmt = select(DeliveryLog).where(DeliveryLog.run_id == run_id)
        return list(session.scalars(stmt))


# ── Snapshots ───────────────────────────────────────────────────────────────

def save_snapshot(
    project_id: str, kind: str, metrics: dict, row_count: int, data_last_date: str | None,
) -> MetricSnapshot:
    with get_session() as session:
        snapshot = MetricSnapshot(
            project_id=project_id, kind=kind, metrics=metrics,
            row_count=row_count, data_last_date=data_last_date,
        )
        session.add(snapshot)
        session.commit()
        session.refresh(snapshot)
        return snapshot


def latest_snapshot(project_id: str, kind: str = "kpi") -> MetricSnapshot | None:
    with get_session() as session:
        stmt = (
            select(MetricSnapshot)
            .where(MetricSnapshot.project_id == project_id, MetricSnapshot.kind == kind)
            .order_by(MetricSnapshot.captured_at.desc())
            .limit(1)
        )
        return session.scalars(stmt).first()


def snapshot_before(project_id: str, kind: str, before: datetime) -> MetricSnapshot | None:
    """The most recent snapshot taken *before* a moment — the baseline a
    "vs. last week" comparison needs."""
    with get_session() as session:
        stmt = (
            select(MetricSnapshot)
            .where(
                MetricSnapshot.project_id == project_id,
                MetricSnapshot.kind == kind,
                MetricSnapshot.captured_at < before,
            )
            .order_by(MetricSnapshot.captured_at.desc())
            .limit(1)
        )
        return session.scalars(stmt).first()


def list_snapshots(project_id: str, kind: str = "kpi", limit: int = 90) -> list[MetricSnapshot]:
    with get_session() as session:
        stmt = (
            select(MetricSnapshot)
            .where(MetricSnapshot.project_id == project_id, MetricSnapshot.kind == kind)
            .order_by(MetricSnapshot.captured_at.desc())
            .limit(limit)
        )
        return list(reversed(list(session.scalars(stmt))))
