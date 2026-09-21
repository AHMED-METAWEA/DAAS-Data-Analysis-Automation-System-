"""One scheduled run, start to finish.

    load data → analytics → decision metrics → figure registry → evidence
                                                     │
                          snapshot history ──────────┤
                          owner's targets ───────────┤
                                                     ▼
                                              ranked findings
                                                     │
                              cooldown + severity floor + cap
                                                     │
                              drill into the largest finding
                                                     │
                                       compose briefing (EN/AR)
                                                     │
                                    deliver → record what arrived

Three properties this is built to guarantee, in order of how badly their absence
would hurt:

* **A run always records itself.**  Success, failure, and "found nothing" are
  all written to :class:`~db.monitoring_models.MonitorRun`.  "The monitor has
  not alerted" and "the monitor has not run since Tuesday" look identical from
  the outside, and only one of them is good news.

* **One failure never takes the run with it.**  A dead LLM provider costs the
  narrative; a dead channel costs that channel.  Neither costs the analysis,
  the alerts, or the other channels.

* **Nothing is invented on the way out.**  The figures in the briefing come from
  the same computation the Insights page publishes, through the same evidence
  ranking and the same citation registry.  What arrives on WhatsApp and what
  appears in the app are the same numbers, because they are literally the same
  objects.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

from agents.analytics.engine import run_analytics
from agents.insights.decision_metrics import compute_decision_metrics
from agents.insights.evidence import build_evidence
from agents.insights.figures import build_registry
from data_manager.manager import get_view, invalidate
from db.monitoring import (
    finish_run,
    get_channels,
    get_schedule,
    mark_schedule_ran,
    recent_fingerprints,
    save_alerts,
    save_deliveries,
    start_run,
    update_channel,
)
from db.monitoring_models import Alert, DeliveryLog, MonitorRun, Schedule
from db.platform_models import Project
from db.session import get_session
from monitoring import history
from monitoring.briefing import BriefingContext, compose
from monitoring.rules import RuleContext, evaluate, filter_findings
from notifications.base import DeliveryResult, Message
from notifications.registry import ChannelConfigError, build_channel

logger = logging.getLogger(__name__)

# A schedule that somehow fires twice (two workers, a manual run racing the
# cron) must not double-deliver. A Postgres advisory lock is the cheapest
# correct answer: it needs no extra table, and it is released automatically if
# the process dies holding it.
_ADVISORY_LOCK_NAMESPACE = 0x4441_4153  # "DAAS"

# Delivery is retried once for failures a retry could plausibly fix. More than
# that and a schedule that fires every hour spends its life retrying.
_DELIVERY_ATTEMPTS = 2


def _lock_key(schedule_id: str) -> int:
    """A stable 63-bit key for the advisory lock on this schedule."""
    return (_ADVISORY_LOCK_NAMESPACE << 31) | (hash(schedule_id) & 0x7FFF_FFFF)


class _ScheduleLock:
    """Postgres advisory lock, held for the duration of one run.

    ``try_advisory_lock`` rather than the blocking form: a second attempt at a
    schedule already running should be *skipped*, not queued behind it. Queuing
    would mean the 08:00 run starts the moment the 07:00 one finishes, which is
    the opposite of what a schedule means.
    """

    def __init__(self, schedule_id: str) -> None:
        self.key = _lock_key(schedule_id)
        self._session = None
        self.acquired = False

    def __enter__(self) -> _ScheduleLock:
        from sqlalchemy import text

        try:
            self._session = get_session()
            self.acquired = bool(
                self._session.execute(
                    text("SELECT pg_try_advisory_lock(:key)"), {"key": self.key},
                ).scalar()
            )
        except Exception:
            # Not Postgres, or the database is unreachable. Locking is a
            # safeguard against duplicate delivery, not a precondition for
            # analysis — degrade to unlocked rather than refusing to run.
            logger.warning("Could not take the schedule advisory lock; running unlocked",
                           exc_info=True)
            self.acquired = True
            self._session = None
        return self

    def __exit__(self, *exc: object) -> None:
        if self._session is None:
            return
        from sqlalchemy import text

        try:
            if self.acquired:
                self._session.execute(
                    text("SELECT pg_advisory_unlock(:key)"), {"key": self.key},
                )
                self._session.commit()
        except Exception:  # pragma: no cover - best effort
            logger.warning("Could not release the schedule advisory lock", exc_info=True)
        finally:
            self._session.close()


def _app_link(project_id: str) -> str:
    base = (os.environ.get("APP_PUBLIC_URL") or os.environ.get("FRONTEND_ORIGIN") or "").split(",")[0]
    base = base.strip().rstrip("/")
    return f"{base}/monitoring?project={project_id}" if base else ""


def _detect_currency(df) -> str | None:
    """The dataset's currency, when it records one.

    A briefing that writes "EGP 40,000" against data with no currency column has
    invented the currency. The amount is real; the unit is the model's guess.
    """
    for column in df.columns:
        if "currency" in str(column).lower():
            values = df[column].dropna().astype(str)
            if not values.empty:
                top = values.value_counts()
                # Mixed currencies summed together would be worse than none.
                if len(top) == 1:
                    return str(top.index[0])[:8]
    return None


def _in_quiet_hours(schedule: Schedule, now: datetime) -> bool:
    """Whether delivery should be held back right now.

    Alerts are still found, stored and visible in the app during quiet hours —
    only the push is suppressed. Suppressing the *analysis* would mean the
    morning briefing had no idea what happened overnight.
    """
    start, end = schedule.quiet_hours_start, schedule.quiet_hours_end
    if start is None or end is None or start == end:
        return False
    try:
        from zoneinfo import ZoneInfo

        local = now.astimezone(ZoneInfo(schedule.timezone or "UTC"))
    except Exception:
        local = now
    hour = local.hour
    if start < end:
        return start <= hour < end
    # A window that wraps midnight (22:00 → 07:00).
    return hour >= start or hour < end


def _run_root_cause(
    df, schedule: Schedule, finding_metric: str, model: str | None,
) -> dict[str, Any]:
    """Drill into the run's largest finding.

    Wrapped end to end: a drill-down that fails must cost the *drill-down*. The
    alert it was going to explain is already computed, already correct, and
    still worth sending.
    """
    from agents.rootcause.engine import run_root_cause

    measure = "revenue" if finding_metric not in ("orders", "customers") else finding_metric
    try:
        result = run_root_cause(
            df, measure=measure, window_mode="auto",
            with_narrative=True, model=model, language=schedule.language or "en",
        )
        return result.as_dict()
    except Exception as exc:
        logger.exception("Root-cause drill-down failed during scheduled run")
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}


def _deliver(
    schedule: Schedule, run_id: str, message: Message, alert_id: str | None,
) -> list[DeliveryLog]:
    """Push the briefing to every configured channel, and record each outcome."""
    channels = get_channels(list(schedule.channel_ids or []))
    if not channels:
        # An in-app-only schedule is a legitimate configuration: the alerts are
        # already persisted and the inbox reads them from the database.
        return [DeliveryLog(
            run_id=run_id, alert_id=alert_id, channel_id=None, channel_type="in_app",
            status="sent", destination="in-app inbox", attempts=1,
        )]

    logs: list[DeliveryLog] = []
    for record in channels:
        if not record.is_active:
            logs.append(DeliveryLog(
                run_id=run_id, alert_id=alert_id, channel_id=record.id,
                channel_type=record.type, status="skipped",
                destination=record.destination, error="channel is disabled",
            ))
            continue
        try:
            channel = build_channel(record)
        except ChannelConfigError as exc:
            logs.append(DeliveryLog(
                run_id=run_id, alert_id=alert_id, channel_id=record.id,
                channel_type=record.type, status="failed",
                destination=record.destination, error=str(exc), attempts=0,
            ))
            update_channel(record.id, last_error=str(exc))
            continue

        result: DeliveryResult | None = None
        for attempt in range(1, _DELIVERY_ATTEMPTS + 1):
            try:
                result = channel.send(message)
            except Exception as exc:  # pragma: no cover - defensive
                result = DeliveryResult(
                    ok=False, channel_type=record.type, destination=record.destination,
                    error=f"{type(exc).__name__}: {exc}", retryable=False,
                )
            result.attempts = attempt
            if result.ok or not result.retryable:
                break

        logs.append(DeliveryLog(
            run_id=run_id, alert_id=alert_id, channel_id=record.id,
            channel_type=record.type, status=result.status,
            destination=result.destination or record.destination,
            provider_message_id=result.provider_message_id,
            error=result.error, attempts=result.attempts,
        ))
        # A send that failed today does not erase the fact that this channel was
        # verified last week — that history is what tells a user "it worked once,
        # something changed" rather than "it never worked".
        updates: dict[str, object] = {"last_error": None if result.ok else result.error}
        if result.ok:
            updates["verified_at"] = datetime.now(UTC)
        update_channel(record.id, **updates)
    return logs


def _analyse(project_id: str, schedule: Schedule) -> dict[str, Any]:
    """The analysis half of a run — everything before findings become alerts."""
    # A scheduled run must see the data as it is now, not as a five-minute-old
    # cache entry left by whoever last opened the app.
    invalidate(project_id)
    df = get_view(project_id, "analytics")
    if df is None or df.empty:
        raise ValueError(
            "This project has no saved data — finish the Data Workspace pipeline and save "
            "before scheduling checks against it."
        )

    payload = run_analytics(df, "")
    decision = compute_decision_metrics(df, payload.get("schema"))
    registry = build_registry(payload, decision)
    evidence = build_evidence(payload, decision, registry)
    return {
        "df": df, "payload": payload, "decision": decision,
        "registry": registry, "evidence": evidence,
    }


def _execute(
    schedule: Schedule,
    project: Project,
    run: MonitorRun | None,
    *,
    dry_run: bool,
    model: str | None,
) -> dict[str, Any]:
    """Shared body of a real run and a preview.

    A preview that used a different code path would be a preview of something
    else — which is exactly when a user discovers their schedule does not do
    what the preview showed.
    """
    analysis = _analyse(project.id, schedule)
    df, payload, decision = analysis["df"], analysis["payload"], analysis["decision"]

    if dry_run:
        # A preview must not move the history forward: capturing a snapshot here
        # would make the next real run compare against the preview.
        snapshot = {"previous_metrics": {}, "previous_captured_at": None}
        previous = history.latest_snapshot_metrics(project.id)
        snapshot.update(previous)
    else:
        snapshot = history.capture(project.id, payload, decision)

    ctx = RuleContext(
        project_id=project.id,
        payload=payload,
        decision=decision,
        registry=analysis["registry"],
        evidence=analysis["evidence"],
        previous_metrics=snapshot["previous_metrics"],
        previous_captured_at=snapshot["previous_captured_at"],
        config=dict(schedule.rules or {}),
        data_last_date=history.data_last_date(decision),
        language=schedule.language or "en",
    )
    all_findings = evaluate(ctx)
    suppressed = (
        set() if dry_run
        else recent_fingerprints(project.id, schedule.cooldown_hours or 0)
    )
    findings, held = filter_findings(
        all_findings,
        min_severity=schedule.min_severity or "medium",
        suppressed=suppressed,
        limit=schedule.max_alerts_per_run or 5,
    )

    root_cause: dict[str, Any] = {}
    if findings and "root_cause" in (schedule.analyses or []):
        root_cause = _run_root_cause(df, schedule, findings[0].metric, model)

    markdown, message = compose(findings, BriefingContext(
        project_name=project.name,
        language=schedule.language or "en",
        currency=_detect_currency(df),
        row_count=int((payload.get("metadata") or {}).get("row_count") or 0),
        data_last_date=history.data_last_date(decision),
        app_link=_app_link(project.id),
        root_cause=root_cause or None,
        suppressed_count=len(held),
        # Names the baseline the report's percentages are measured against.
        previous_captured_at=snapshot["previous_captured_at"],
    ))

    return {
        "findings": findings,
        "held": held,
        "all_findings": all_findings,
        "root_cause": root_cause,
        "markdown": markdown,
        "message": message,
        "payload": payload,
        "money_at_stake": sum(abs(f.money_at_stake or 0.0) for f in findings),
    }


def run_schedule(
    schedule_id: str,
    *,
    trigger: str = "schedule",
    model: str | None = None,
) -> MonitorRun | None:
    """Execute one schedule. Returns the recorded run, or None if it was skipped."""
    schedule = get_schedule(schedule_id)
    if schedule is None:
        logger.warning("Schedule %s no longer exists; nothing to run", schedule_id)
        return None

    with _ScheduleLock(schedule_id) as lock:
        if not lock.acquired:
            logger.info("Schedule %s is already running elsewhere; skipping", schedule_id)
            return None

        with get_session() as session:
            project = session.get(Project, schedule.project_id)
        if project is None:
            mark_schedule_ran(schedule_id, "failed")
            return None

        run = start_run(schedule_id, project.id, trigger=trigger)
        try:
            outcome = _execute(schedule, project, run, dry_run=False, model=model)
        except Exception as exc:
            logger.exception("Scheduled run failed for schedule %s", schedule_id)
            mark_schedule_ran(schedule_id, "failed")
            return finish_run(
                run.id, status="failed", error=f"{type(exc).__name__}: {exc}",
            )

        findings = outcome["findings"]
        alerts = [
            Alert(
                run_id=run.id, project_id=project.id, user_id=schedule.user_id,
                root_cause=outcome["root_cause"] if index == 0 else {},
                **finding.as_alert_fields(),
            )
            for index, finding in enumerate(findings)
        ]
        save_alerts(alerts)

        now = datetime.now(UTC)
        should_send = bool(findings) or schedule.send_when_nothing_found
        quiet = _in_quiet_hours(schedule, now)
        logs: list[DeliveryLog] = []
        if should_send and not quiet:
            logs = _deliver(
                schedule, run.id, outcome["message"],
                alerts[0].id if alerts else None,
            )
        elif should_send and quiet:
            logs = [DeliveryLog(
                run_id=run.id, channel_id=None, channel_type="in_app", status="skipped",
                destination="", error="inside the schedule's quiet hours",
            )]
        save_deliveries(logs)

        delivered = sum(1 for log in logs if log.status == "sent")
        mark_schedule_ran(schedule_id, "success")
        return finish_run(
            run.id,
            status="success",
            alerts_found=len(findings),
            alerts_delivered=delivered,
            money_at_stake=outcome["money_at_stake"],
            briefing_md=outcome["markdown"],
            detail={
                "analyses": list(schedule.analyses or []),
                "findings_total": len(outcome["all_findings"]),
                "findings_suppressed": len(outcome["held"]),
                "suppressed_reasons": [
                    {"rule": f.rule, "severity": f.severity, "title": f.title}
                    for f in outcome["held"][:10]
                ],
                "root_cause_available": bool(outcome["root_cause"].get("available")),
                "quiet_hours_skipped": bool(should_send and quiet),
                "delivery": [
                    {"channel": log.channel_type, "status": log.status, "error": log.error}
                    for log in logs
                ],
            },
        )


def preview_schedule(schedule_id: str, model: str | None = None) -> dict[str, Any]:
    """Run the analysis and compose the briefing without delivering or recording.

    Same code path as a real run, minus the three side effects: no snapshot is
    captured, no alert is stored, nothing is sent. It answers the question a
    user actually has before turning a schedule on — "what would this have sent
    me?" — rather than describing it.
    """
    schedule = get_schedule(schedule_id)
    if schedule is None:
        return {"available": False, "reason": "This schedule no longer exists."}
    with get_session() as session:
        project = session.get(Project, schedule.project_id)
    if project is None:
        return {"available": False, "reason": "This schedule's project no longer exists."}

    try:
        outcome = _execute(schedule, project, None, dry_run=True, model=model)
    except Exception as exc:
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}

    return {
        "available": True,
        "briefing_md": outcome["markdown"],
        "text": outcome["message"].text,
        "subject": outcome["message"].subject,
        "money_at_stake": round(outcome["money_at_stake"], 2),
        "findings": [
            {
                "rule": f.rule, "severity": f.severity, "title": f.title,
                "body": f.body, "money_at_stake": round(f.money_at_stake, 2),
                "metric": f.metric,
            }
            for f in outcome["findings"]
        ],
        "suppressed": [
            {"rule": f.rule, "severity": f.severity, "title": f.title}
            for f in outcome["held"]
        ],
        "root_cause": outcome["root_cause"],
        "would_deliver": bool(outcome["findings"]) or schedule.send_when_nothing_found,
        "channels": [
            {"id": c.id, "type": c.type, "name": c.name, "active": c.is_active}
            for c in get_channels(list(schedule.channel_ids or []))
        ],
    }
