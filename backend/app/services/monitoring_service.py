"""Mapping between the monitoring tables and the API's shapes.

Two responsibilities worth naming, because getting either wrong is a real bug
rather than an inconvenience:

* **Credentials never leave.**  A channel's stored secrets are decrypted only
  inside :mod:`notifications`, at send time.  The API returns
  ``has_credentials`` and nothing else — an endpoint that echoed a bot token
  back to the browser would put it in every proxy log and browser cache between
  here and the user.

* **A partial credential update must not silently wipe the rest.**  The UI
  cannot render existing secrets (it never receives them), so a form that
  submits only the field the user changed would otherwise blank the others.
  :func:`merge_credentials` merges into what is stored, and an explicit empty
  object is the only way to clear.
"""

from __future__ import annotations

from typing import Any

from db.connection_configs import decrypt_credentials, encrypt_credentials
from db.monitoring_models import Alert, DeliveryLog, MonitorRun, NotificationChannel, Schedule
from monitoring.scheduler import describe_schedule


def channel_out(record: NotificationChannel) -> dict[str, Any]:
    return {
        "id": record.id,
        "type": record.type,
        "name": record.name,
        "destination": record.destination,
        "config": record.config or {},
        "is_active": record.is_active,
        "has_credentials": bool(record.encrypted_credentials),
        "verified_at": record.verified_at,
        "last_error": record.last_error,
        "created_at": record.created_at,
    }


def merge_credentials(record: NotificationChannel | None, incoming: dict[str, str] | None) -> str:
    """Encrypt the new credential set, preserving fields the caller omitted.

    ``None`` means "leave everything as it was"; ``{}`` means "clear them".
    Anything else is merged over the stored values, and blank strings are
    treated as "not supplied" rather than as "set this to empty" — a form that
    renders an empty password box must not erase a working password.
    """
    if incoming is None:
        return record.encrypted_credentials if record else ""
    if not incoming:
        return ""
    existing: dict[str, str] = {}
    if record and record.encrypted_credentials:
        try:
            existing = decrypt_credentials(record.encrypted_credentials)
        except Exception:
            existing = {}
    merged = {**existing, **{k: v for k, v in incoming.items() if v not in (None, "")}}
    return encrypt_credentials(merged) if merged else ""


def schedule_out(schedule: Schedule, project_name: str) -> dict[str, Any]:
    return {
        "id": schedule.id,
        "project_id": schedule.project_id,
        "project_name": project_name,
        "name": schedule.name,
        "is_active": schedule.is_active,
        "frequency": schedule.frequency,
        "hour": schedule.hour,
        "minute": schedule.minute,
        "day_of_week": schedule.day_of_week,
        "day_of_month": schedule.day_of_month,
        "cron_expression": schedule.cron_expression,
        "timezone": schedule.timezone,
        "analyses": list(schedule.analyses or []),
        "rules": schedule.rules or {},
        "min_severity": schedule.min_severity,
        "cooldown_hours": schedule.cooldown_hours,
        "max_alerts_per_run": schedule.max_alerts_per_run,
        "channel_ids": list(schedule.channel_ids or []),
        "language": schedule.language,
        "quiet_hours_start": schedule.quiet_hours_start,
        "quiet_hours_end": schedule.quiet_hours_end,
        "send_when_nothing_found": schedule.send_when_nothing_found,
        "last_run_at": schedule.last_run_at,
        "last_status": schedule.last_status,
        "next_run_at": schedule.next_run_at,
        "created_at": schedule.created_at,
        "description": describe_schedule(schedule),
    }


def alert_out(alert: Alert, project_name: str = "") -> dict[str, Any]:
    return {
        "id": alert.id,
        "run_id": alert.run_id,
        "project_id": alert.project_id,
        "project_name": project_name,
        "fingerprint": alert.fingerprint,
        "rule": alert.rule,
        "severity": alert.severity,
        "title": alert.title,
        "body_md": alert.body_md,
        "metric": alert.metric,
        "money_at_stake": alert.money_at_stake,
        "current_value": alert.current_value,
        "prior_value": alert.prior_value,
        "change_pct": alert.change_pct,
        "evidence": alert.evidence or {},
        "root_cause": alert.root_cause or {},
        "status": alert.status,
        "read_at": alert.read_at,
        "created_at": alert.created_at,
    }


def run_out(run: MonitorRun, schedule_name: str = "", project_name: str = "") -> dict[str, Any]:
    return {
        "id": run.id,
        "schedule_id": run.schedule_id,
        "schedule_name": schedule_name,
        "project_id": run.project_id,
        "project_name": project_name,
        "status": run.status,
        "trigger": run.trigger,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "duration_ms": run.duration_ms,
        "alerts_found": run.alerts_found,
        "alerts_delivered": run.alerts_delivered,
        "money_at_stake": run.money_at_stake,
        "error": run.error,
    }


def delivery_out(log: DeliveryLog) -> dict[str, Any]:
    return {
        "id": log.id,
        "channel_type": log.channel_type,
        "status": log.status,
        "destination": log.destination,
        "error": log.error,
        "attempts": log.attempts,
        "created_at": log.created_at.isoformat() if log.created_at else None,
    }
