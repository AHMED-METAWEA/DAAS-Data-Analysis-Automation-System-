"""Monitoring API — user control over the autonomous analyst.

The feature is only trustworthy if the user is plainly in charge of it, so every
part of the schedule is editable, previewable and reversible from here: when it
runs, what it looks for, how loud a finding must be, how often it may repeat
itself, where it goes, in which language, and when it must stay quiet.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from agents.constants import DEFAULT_INSIGHTS_MODEL
from backend.app.api.deps import get_current_user, get_db, get_owned_project
from backend.app.schemas.monitoring import (
    AlertOut,
    AlertSummaryResponse,
    ChannelOut,
    ChannelTestResponse,
    ChannelTypeInfo,
    CreateChannelRequest,
    CreateScheduleRequest,
    PreviewResponse,
    RunDetailOut,
    RunOut,
    ScheduleOut,
    SchedulerStatusResponse,
    UpdateAlertRequest,
    UpdateChannelRequest,
    UpdateScheduleRequest,
)
from backend.app.services.monitoring_service import (
    alert_out,
    channel_out,
    delivery_out,
    merge_credentials,
    run_out,
    schedule_out,
)
from db import monitoring as repo
from db.auth_models import User
from db.platform_models import Project
from monitoring.runner import preview_schedule, run_schedule
from monitoring.scheduler import build_trigger, next_fire_time, scheduler_status, sync_jobs
from notifications.registry import ChannelConfigError, build_channel, describe_channel_types

router = APIRouter(prefix="/monitoring", tags=["monitoring"])


def _owned_schedule(schedule_id: str, current_user: User):
    schedule = repo.get_schedule(schedule_id)
    if schedule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Schedule not found")
    if schedule.user_id != current_user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not your schedule")
    return schedule


def _owned_channel(channel_id: str, current_user: User):
    channel = repo.get_channel(channel_id)
    if channel is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Channel not found")
    if channel.user_id != current_user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not your channel")
    return channel


def _project_name(db: Session, project_id: str) -> str:
    project = db.get(Project, project_id)
    return project.name if project else ""


def _validate_recurrence(schedule) -> None:
    """Reject a recurrence that cannot be scheduled, at the point of saving it.

    Without this the failure is silent and late: an unparseable cron expression
    is stored happily, `sync_jobs` logs a warning nobody reads, and the user
    discovers their schedule never fired sometime after the morning it should
    have. `next_fire_time` deliberately stays lenient — it feeds a display
    field — so the strict check belongs here.
    """
    try:
        build_trigger(schedule)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"This recurrence cannot be scheduled: {exc}",
        ) from exc


# ── Channel types and channels ──────────────────────────────────────────────

@router.get("/channel-types", response_model=list[ChannelTypeInfo])
def list_channel_types() -> list[ChannelTypeInfo]:
    """Every delivery channel and the fields its setup form needs.

    Served from the backend so the form and the transport cannot drift apart —
    a UI that asks for "token" while the channel reads "bot_token" fails only at
    07:00, in a place nobody is watching.
    """
    return [ChannelTypeInfo(**info) for info in describe_channel_types()]


@router.get("/channels", response_model=list[ChannelOut])
def list_channels(current_user: User = Depends(get_current_user)) -> list[ChannelOut]:
    return [ChannelOut(**channel_out(c)) for c in repo.list_channels(current_user.id)]


@router.post("/channels", response_model=ChannelOut, status_code=status.HTTP_201_CREATED)
def create_channel(
    payload: CreateChannelRequest, current_user: User = Depends(get_current_user),
) -> ChannelOut:
    try:
        encrypted = merge_credentials(None, payload.credentials)
    except Exception as exc:
        # Almost always a missing FERNET_KEY. Saying so is far more useful than
        # the cryptography library's own message.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Could not encrypt the credentials: {exc}",
        ) from exc
    channel = repo.create_channel(
        current_user.id,
        type=payload.type, name=payload.name, destination=payload.destination,
        encrypted_credentials=encrypted, config=payload.config, is_active=payload.is_active,
    )
    return ChannelOut(**channel_out(channel))


@router.patch("/channels/{channel_id}", response_model=ChannelOut)
def update_channel(
    channel_id: str,
    payload: UpdateChannelRequest,
    current_user: User = Depends(get_current_user),
) -> ChannelOut:
    existing = _owned_channel(channel_id, current_user)
    fields: dict[str, Any] = payload.model_dump(exclude_unset=True, exclude={"credentials"})
    if "credentials" in payload.model_fields_set:
        fields["encrypted_credentials"] = merge_credentials(existing, payload.credentials)
    channel = repo.update_channel(channel_id, **fields)
    if channel is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Channel not found")
    return ChannelOut(**channel_out(channel))


@router.post("/channels/{channel_id}/test", response_model=ChannelTestResponse)
def test_channel(
    channel_id: str, current_user: User = Depends(get_current_user),
) -> ChannelTestResponse:
    """Send a real test message. The only honest proof a channel works."""
    record = _owned_channel(channel_id, current_user)
    try:
        channel = build_channel(record)
    except ChannelConfigError as exc:
        repo.update_channel(channel_id, last_error=str(exc))
        return ChannelTestResponse(ok=False, channel_type=record.type, error=str(exc))

    result = channel.verify()
    from datetime import UTC, datetime

    repo.update_channel(
        channel_id,
        last_error=None if result.ok else result.error,
        verified_at=datetime.now(UTC) if result.ok else None,
    )
    return ChannelTestResponse(
        ok=result.ok, channel_type=result.channel_type, destination=result.destination,
        error=result.error, provider_message_id=result.provider_message_id,
    )


@router.delete("/channels/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_channel(channel_id: str, current_user: User = Depends(get_current_user)) -> None:
    _owned_channel(channel_id, current_user)
    repo.delete_channel(channel_id)


# ── Schedules ───────────────────────────────────────────────────────────────

@router.get("/schedules", response_model=list[ScheduleOut])
def list_schedules(current_user: User = Depends(get_current_user)) -> list[ScheduleOut]:
    return [
        ScheduleOut(**schedule_out(schedule, project.name))
        for schedule, project in repo.list_schedules_for_user(current_user.id)
    ]


@router.post(
    "/projects/{project_id}/schedules",
    response_model=ScheduleOut,
    status_code=status.HTTP_201_CREATED,
)
def create_schedule(
    payload: CreateScheduleRequest,
    project: Project = Depends(get_owned_project),
    current_user: User = Depends(get_current_user),
) -> ScheduleOut:
    schedule = repo.create_schedule(
        project.id, current_user.id, **payload.model_dump(),
    )
    try:
        _validate_recurrence(schedule)
    except HTTPException:
        # Never leave an unschedulable row behind for the reconcile loop to
        # complain about on every tick.
        repo.delete_schedule(schedule.id)
        raise
    # Register the job immediately rather than waiting up to a minute for the
    # reconcile tick — a schedule saved at 06:59 for 07:00 has to fire.
    schedule = repo.update_schedule(schedule.id, next_run_at=next_fire_time(schedule))
    sync_jobs()
    return ScheduleOut(**schedule_out(schedule, project.name))


@router.get("/schedules/{schedule_id}", response_model=ScheduleOut)
def get_schedule(
    schedule_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ScheduleOut:
    schedule = _owned_schedule(schedule_id, current_user)
    return ScheduleOut(**schedule_out(schedule, _project_name(db, schedule.project_id)))


@router.patch("/schedules/{schedule_id}", response_model=ScheduleOut)
def update_schedule(
    schedule_id: str,
    payload: UpdateScheduleRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ScheduleOut:
    _owned_schedule(schedule_id, current_user)
    # `exclude_unset` is the whole contract: the payload carries exactly the
    # fields the user touched, and every one of them is applied — including the
    # ones set to null (clearing quiet hours, dropping a cron expression when
    # switching away from a custom frequency). See db.monitoring.update_schedule.
    schedule = repo.update_schedule(schedule_id, **payload.model_dump(exclude_unset=True))
    if schedule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Schedule not found")

    _validate_recurrence(schedule)
    # Recomputing the next fire time needs the *updated* recurrence, so it is a
    # second write rather than part of the one above.
    schedule = repo.update_schedule(schedule_id, next_run_at=next_fire_time(schedule))
    sync_jobs()
    return ScheduleOut(**schedule_out(schedule, _project_name(db, schedule.project_id)))


@router.delete("/schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_schedule(schedule_id: str, current_user: User = Depends(get_current_user)) -> None:
    _owned_schedule(schedule_id, current_user)
    repo.delete_schedule(schedule_id)
    sync_jobs()


@router.post("/schedules/{schedule_id}/preview", response_model=PreviewResponse)
def preview(
    schedule_id: str, current_user: User = Depends(get_current_user),
) -> PreviewResponse:
    """Show what this schedule would send, without sending or recording anything."""
    _owned_schedule(schedule_id, current_user)
    model = current_user.model_preferences.get("insights") or DEFAULT_INSIGHTS_MODEL
    return PreviewResponse(**preview_schedule(schedule_id, model=model))


@router.post("/schedules/{schedule_id}/run", response_model=RunOut)
def run_now(
    schedule_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RunOut:
    """Execute a schedule immediately — a real run, delivery included.

    Synchronous on purpose: the caller pressed a button and needs the outcome,
    and FastAPI runs a `def` endpoint in its threadpool, so the event loop is
    never blocked while the analysis runs.
    """
    schedule = _owned_schedule(schedule_id, current_user)
    model = current_user.model_preferences.get("insights") or DEFAULT_INSIGHTS_MODEL
    run = run_schedule(schedule_id, trigger="manual", model=model)
    if run is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="This schedule is already running. Try again once it finishes.",
        )
    return RunOut(**run_out(run, schedule.name, _project_name(db, schedule.project_id)))


# ── Runs ────────────────────────────────────────────────────────────────────

@router.get("/runs", response_model=list[RunOut])
def list_runs(
    limit: int = 50, current_user: User = Depends(get_current_user),
) -> list[RunOut]:
    return [
        RunOut(**run_out(run, schedule.name, project.name))
        for run, schedule, project in repo.list_runs_for_user(current_user.id, limit=limit)
    ]


@router.get("/runs/{run_id}", response_model=RunDetailOut)
def get_run(
    run_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RunDetailOut:
    run = repo.get_run(run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Run not found")
    schedule = repo.get_schedule(run.schedule_id)
    if schedule is None or schedule.user_id != current_user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not your run")
    project_name = _project_name(db, run.project_id)
    return RunDetailOut(
        **run_out(run, schedule.name, project_name),
        briefing_md=run.briefing_md,
        detail=run.detail or {},
        alerts=[
            AlertOut(**alert_out(a, project_name)) for a in repo.list_alerts_for_run(run_id)
        ],
        deliveries=[delivery_out(d) for d in repo.list_deliveries_for_run(run_id)],
    )


# ── Alerts ──────────────────────────────────────────────────────────────────

@router.get("/alerts", response_model=list[AlertOut])
def list_alerts(
    status_filter: str | None = None,
    limit: int = 100,
    current_user: User = Depends(get_current_user),
) -> list[AlertOut]:
    return [
        AlertOut(**alert_out(alert, project.name))
        for alert, project in repo.list_alerts_for_user(
            current_user.id, status=status_filter, limit=limit,
        )
    ]


@router.get("/alerts/summary", response_model=AlertSummaryResponse)
def alerts_summary(current_user: User = Depends(get_current_user)) -> AlertSummaryResponse:
    """Unread count plus the newest few — what the header bell needs in one call."""
    recent = repo.list_alerts_for_user(current_user.id, limit=8)
    return AlertSummaryResponse(
        unread=repo.count_unread_alerts(current_user.id),
        recent=[AlertOut(**alert_out(a, p.name)) for a, p in recent],
    )


@router.patch("/alerts/{alert_id}", response_model=AlertOut)
def update_alert(
    alert_id: str,
    payload: UpdateAlertRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AlertOut:
    alert = repo.set_alert_status(alert_id, current_user.id, payload.status)
    if alert is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Alert not found")
    return AlertOut(**alert_out(alert, _project_name(db, alert.project_id)))


@router.post("/alerts/read-all", response_model=AlertSummaryResponse)
def mark_all_read(current_user: User = Depends(get_current_user)) -> AlertSummaryResponse:
    repo.mark_all_alerts_read(current_user.id)
    recent = repo.list_alerts_for_user(current_user.id, limit=8)
    return AlertSummaryResponse(
        unread=repo.count_unread_alerts(current_user.id),
        recent=[AlertOut(**alert_out(a, p.name)) for a, p in recent],
    )


# ── Scheduler health ────────────────────────────────────────────────────────

@router.get("/scheduler", response_model=SchedulerStatusResponse)
def get_scheduler_status(
    current_user: User = Depends(get_current_user),
) -> SchedulerStatusResponse:
    """Is the scheduler actually running, and what is queued?

    A monitoring feature whose own health is invisible has the same problem it
    exists to solve.
    """
    return SchedulerStatusResponse(**scheduler_status())
