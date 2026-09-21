"""Keeping every active schedule firing.

## Why APScheduler and not Celery

Celery beat is the reflexive answer and would have been the wrong one here.  It
needs a broker (Redis), a beat process and at least one worker process — three
things that must all be alive for a single 07:00 briefing to be sent, and three
things that can be down without anybody noticing until the briefing is missing.
APScheduler runs inside the API process with no broker at all, which is the
difference between "start the app" and "start four things in the right order".

The trade-off is real and worth stating: with APScheduler, schedules fire only
while a process is running.  That is handled here rather than waved away — see
*Missed runs* below — and a deployment that wants the scheduler off the web
process gets :mod:`monitoring.worker`, the same code with a blocking loop.

## Where the truth lives

The :class:`~db.monitoring_models.Schedule` table is the source of truth, not
APScheduler's job store.  A persistent job store would mean two records of the
same fact that can disagree — the classic failure being a schedule the user
deleted that keeps firing because its pickled job outlived it.  So the job store
is in memory, and :func:`sync_jobs` reconciles it against the database on a
short interval.  Losing the process loses nothing: the next start rebuilds every
job from the table.

## Safety under more than one process

A schedule that fires in the API process *and* in a standalone worker would
deliver twice.  :func:`monitoring.runner.run_schedule` takes a Postgres advisory
lock for the schedule it is about to run and skips if another process holds it,
so running both is safe — wasteful, but not wrong.

## Missed runs

``misfire_grace_time`` bounds how late a run may still be useful.  A briefing
that should have gone at 07:00 and goes at 07:04 because the process was
restarting is still the morning briefing; the same one delivered at 16:00 is
noise about a morning that has already happened, and is dropped instead.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime
from typing import Any

from db.monitoring import list_active_schedules, update_schedule
from db.monitoring_models import Schedule
from monitoring.runner import run_schedule

logger = logging.getLogger(__name__)

# How often the in-memory jobs are reconciled against the schedules table. Short
# enough that a schedule created in the UI starts firing without a restart.
SYNC_INTERVAL_SECONDS = 60
# A run more than this late is no longer the run that was wanted.
MISFIRE_GRACE_SECONDS = 900
# One schedule, one concurrent execution. The advisory lock enforces this across
# processes; this enforces it within one.
MAX_INSTANCES = 1

# Two disjoint job-id namespaces, and they must stay disjoint. `sync_jobs`
# removes every job under JOB_PREFIX that has no matching row in the schedules
# table, so anything sharing that prefix is deleted on the first reconcile — the
# reconcile job included, which is a loop that silently stops looping after one
# tick. Keeping the housekeeping job outside the prefix makes that unexpressible
# rather than merely fixed.
JOB_PREFIX = "monitor:"
RECONCILE_JOB_ID = "monitoring:reconcile"

_scheduler: Any = None
_lock = threading.Lock()
# Job id → the schedule fingerprint the job was built from, so `sync_jobs` can
# tell a changed schedule from an unchanged one without rebuilding everything.
_job_signatures: dict[str, str] = {}


class SchedulerUnavailableError(RuntimeError):
    """APScheduler is not installed, so schedules cannot fire."""


def _import_apscheduler():
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise SchedulerUnavailableError(
            "APScheduler is not installed. Run `pip install -r requirements.txt` — "
            "scheduled monitoring cannot run without it."
        ) from exc
    return BackgroundScheduler, CronTrigger


def _timezone(name: str | None):
    """Resolve an IANA zone, falling back to UTC rather than refusing to schedule.

    On Windows there is no system zone database, so this needs the ``tzdata``
    package (it is in requirements.txt for exactly this reason). A missing zone
    must not stop every schedule in the system from running — it degrades to UTC
    and says so.
    """
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name or "UTC")
    except Exception:
        logger.warning(
            "Unknown timezone %r — falling back to UTC. On Windows this usually means the "
            "`tzdata` package is missing.", name,
        )
        from zoneinfo import ZoneInfo

        return ZoneInfo("UTC")


def build_trigger(schedule: Schedule):
    """Translate a schedule's plain-language recurrence into a cron trigger.

    Every frequency ends up as one ``CronTrigger``, including ``custom``, so
    there is a single execution path and "what does this schedule actually do?"
    has one answer: :func:`describe_schedule`.
    """
    _, CronTrigger = _import_apscheduler()
    tz = _timezone(schedule.timezone)
    hour = max(0, min(int(schedule.hour or 0), 23))
    minute = max(0, min(int(schedule.minute or 0), 59))
    frequency = (schedule.frequency or "daily").lower()

    if frequency == "custom":
        expression = (schedule.cron_expression or "").strip()
        if not expression:
            raise ValueError("A custom schedule needs a cron expression.")
        return CronTrigger.from_crontab(expression, timezone=tz)
    if frequency == "hourly":
        return CronTrigger(minute=minute, timezone=tz)
    if frequency == "weekdays":
        return CronTrigger(day_of_week="mon-fri", hour=hour, minute=minute, timezone=tz)
    if frequency == "weekly":
        day = max(0, min(int(schedule.day_of_week or 0), 6))
        return CronTrigger(day_of_week=day, hour=hour, minute=minute, timezone=tz)
    if frequency == "monthly":
        # Capped at 28: a schedule set for the 31st would silently never fire in
        # February, which is the kind of bug that is found six months later.
        day = max(1, min(int(schedule.day_of_month or 1), 28))
        return CronTrigger(day=day, hour=hour, minute=minute, timezone=tz)
    return CronTrigger(hour=hour, minute=minute, timezone=tz)


_WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def describe_schedule(schedule: Schedule) -> str:
    """The recurrence in a sentence, for the UI and the API.

    Shown next to the controls that produced it: a user who set "weekly, day 2,
    07:30" should be able to read back "Every Wednesday at 07:30 (Africa/Cairo)"
    and catch their own off-by-one before it costs them a week.
    """
    hour, minute = int(schedule.hour or 0), int(schedule.minute or 0)
    at = f"{hour:02d}:{minute:02d}"
    tz = schedule.timezone or "UTC"
    frequency = (schedule.frequency or "daily").lower()
    if frequency == "custom":
        return f"Cron `{schedule.cron_expression}` ({tz})"
    if frequency == "hourly":
        return f"Every hour at :{minute:02d} ({tz})"
    if frequency == "weekdays":
        return f"Every weekday at {at} ({tz})"
    if frequency == "weekly":
        day = _WEEKDAY_NAMES[max(0, min(int(schedule.day_of_week or 0), 6))]
        return f"Every {day} at {at} ({tz})"
    if frequency == "monthly":
        day = max(1, min(int(schedule.day_of_month or 1), 28))
        return f"On day {day} of each month at {at} ({tz})"
    return f"Every day at {at} ({tz})"


def next_fire_time(schedule: Schedule) -> datetime | None:
    """When this schedule would next run — computed without registering a job."""
    try:
        from apscheduler.util import astimezone

        trigger = build_trigger(schedule)
        now = datetime.now(astimezone(_timezone(schedule.timezone)))
        return trigger.get_next_fire_time(None, now)
    except Exception:
        return None


def _signature(schedule: Schedule) -> str:
    """Everything about a schedule that changes *when* it fires."""
    return "|".join(str(x) for x in (
        schedule.frequency, schedule.hour, schedule.minute, schedule.day_of_week,
        schedule.day_of_month, schedule.cron_expression, schedule.timezone,
        schedule.is_active,
    ))


def _job_id(schedule_id: str) -> str:
    return f"{JOB_PREFIX}{schedule_id}"


def _execute(schedule_id: str) -> None:
    """The job body. Never raises — a scheduler that dies on a bad run is worse
    than a run that fails."""
    try:
        run_schedule(schedule_id, trigger="schedule")
    except Exception:  # pragma: no cover - defensive
        logger.exception("Scheduled monitoring run raised for schedule %s", schedule_id)


def sync_jobs() -> dict[str, int]:
    """Reconcile the in-memory jobs against the schedules table.

    Returns counts of what changed, which is what makes "my new schedule is not
    firing" a question with an answer rather than a guess.
    """
    if _scheduler is None:
        return {"added": 0, "updated": 0, "removed": 0, "failed": 0}

    added = updated = removed = failed = 0
    try:
        schedules = list_active_schedules()
    except Exception:
        logger.exception("Could not read schedules; leaving the current jobs in place")
        return {"added": 0, "updated": 0, "removed": 0, "failed": 1}

    wanted: dict[str, Schedule] = {_job_id(s.id): s for s in schedules}

    for job_id, schedule in wanted.items():
        signature = _signature(schedule)
        if _job_signatures.get(job_id) == signature and _scheduler.get_job(job_id):
            continue
        try:
            trigger = build_trigger(schedule)
        except Exception as exc:
            logger.warning("Schedule %s has an unusable recurrence: %s", schedule.id, exc)
            failed += 1
            continue
        existing = _scheduler.get_job(job_id)
        _scheduler.add_job(
            _execute,
            trigger=trigger,
            id=job_id,
            args=[schedule.id],
            replace_existing=True,
            max_instances=MAX_INSTANCES,
            misfire_grace_time=MISFIRE_GRACE_SECONDS,
            coalesce=True,  # three missed fires are still one briefing
            name=f"{schedule.name} ({schedule.project_id})",
        )
        _job_signatures[job_id] = signature
        if existing:
            updated += 1
        else:
            added += 1
        job = _scheduler.get_job(job_id)
        if job is not None:
            try:
                update_schedule(schedule.id, next_run_at=job.next_run_time)
            except Exception:  # pragma: no cover - cosmetic field
                logger.debug("Could not record next_run_at for %s", schedule.id)

    for job in list(_scheduler.get_jobs()):
        if job.id.startswith(JOB_PREFIX) and job.id not in wanted:
            _scheduler.remove_job(job.id)
            _job_signatures.pop(job.id, None)
            removed += 1

    if added or updated or removed or failed:
        logger.info(
            "Monitoring jobs synced: +%d ~%d -%d (%d unusable)", added, updated, removed, failed,
        )
    return {"added": added, "updated": updated, "removed": removed, "failed": failed}


def start_scheduler(*, blocking: bool = False) -> Any:
    """Start the scheduler and register every active schedule.

    Idempotent: calling it twice returns the running scheduler rather than
    starting a second one, which matters because a reloading dev server calls
    application startup more than once.
    """
    global _scheduler
    with _lock:
        if _scheduler is not None and _scheduler.running:
            return _scheduler

        BackgroundScheduler, _ = _import_apscheduler()
        if blocking:
            from apscheduler.schedulers.blocking import BlockingScheduler

            _scheduler = BlockingScheduler(timezone=_timezone("UTC"))
        else:
            _scheduler = BackgroundScheduler(timezone=_timezone("UTC"))

        _scheduler.add_job(
            sync_jobs,
            "interval",
            seconds=SYNC_INTERVAL_SECONDS,
            id=RECONCILE_JOB_ID,
            replace_existing=True,
            max_instances=1,
            name="Reconcile monitoring jobs with the database",
        )
        if blocking:
            # A blocking scheduler does not return, so the first sync has to
            # happen before start() rather than after it.
            sync_jobs_result = sync_jobs()
            logger.info("Monitoring worker starting with %s", sync_jobs_result)
            _scheduler.start()
            return _scheduler

        _scheduler.start()
        sync_jobs()
        logger.info("Monitoring scheduler started (%d schedule(s))", _schedule_job_count())
        return _scheduler


def _schedule_job_count() -> int:
    """How many *schedules* have a job — housekeeping jobs are not schedules."""
    if _scheduler is None:
        return 0
    return sum(1 for job in _scheduler.get_jobs() if job.id.startswith(JOB_PREFIX))


def shutdown_scheduler(wait: bool = False) -> None:
    global _scheduler
    with _lock:
        if _scheduler is not None and _scheduler.running:
            _scheduler.shutdown(wait=wait)
        _scheduler = None
        _job_signatures.clear()


def scheduler_status() -> dict[str, Any]:
    """What the scheduler is doing right now — surfaced in the UI.

    A monitoring feature whose own health is invisible has the same problem it
    was built to solve.
    """
    if _scheduler is None or not _scheduler.running:
        return {
            "running": False,
            "jobs": [],
            "reason": (
                "The scheduler is not running in this process. Set MONITORING_SCHEDULER=1 "
                "and restart the API, or run `python -m monitoring.worker` alongside it."
            ),
        }
    jobs = []
    for job in _scheduler.get_jobs():
        if not job.id.startswith(JOB_PREFIX):
            continue
        jobs.append({
            "id": job.id.removeprefix(JOB_PREFIX),
            "name": job.name,
            "next_run_at": job.next_run_time.isoformat() if job.next_run_time else None,
        })
    return {"running": True, "jobs": jobs, "reason": ""}


def scheduler_enabled() -> bool:
    """Whether this process should host the scheduler.

    Defaults to on: a graduation-project deployment is one process, and a
    monitoring feature that silently does not run because an env var was not set
    is worse than one that occasionally runs in two places (which the advisory
    lock already makes safe). Set ``MONITORING_SCHEDULER=0`` to host it
    elsewhere.
    """
    return (os.environ.get("MONITORING_SCHEDULER", "1") or "1").strip().lower() not in (
        "0", "false", "no", "off",
    )
