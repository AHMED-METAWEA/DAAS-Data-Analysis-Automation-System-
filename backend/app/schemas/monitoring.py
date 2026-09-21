from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from db.monitoring_models import (
    ALERT_SEVERITIES,
    ALERT_STATUSES,
    CHANNEL_TYPES,
    FREQUENCIES,
)


# ── Channels ────────────────────────────────────────────────────────────────

class ChannelTypeInfo(BaseModel):
    type: str
    label: str
    description: str
    destination_label: str = ""
    destination_placeholder: str = ""
    needs_destination: bool = True
    secret_fields: list[dict[str, Any]] = Field(default_factory=list)
    config_fields: list[dict[str, Any]] = Field(default_factory=list)


class ChannelOut(BaseModel):
    """A saved channel. Credentials are never echoed back — only whether some
    are stored, which is all the UI needs to render "configured"."""

    id: str
    type: str
    name: str
    destination: str
    config: dict[str, Any] = Field(default_factory=dict)
    is_active: bool
    has_credentials: bool
    verified_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime


class CreateChannelRequest(BaseModel):
    type: str
    name: str
    destination: str = ""
    credentials: dict[str, str] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True

    @field_validator("type")
    @classmethod
    def _known_type(cls, value: str) -> str:
        if value not in CHANNEL_TYPES:
            raise ValueError(f"Unknown channel type '{value}'. One of: {', '.join(CHANNEL_TYPES)}")
        return value


class UpdateChannelRequest(BaseModel):
    name: str | None = None
    destination: str | None = None
    # Omitted leaves the stored credentials untouched; sending {} clears them.
    credentials: dict[str, str] | None = None
    config: dict[str, Any] | None = None
    is_active: bool | None = None


class ChannelTestResponse(BaseModel):
    ok: bool
    channel_type: str
    destination: str = ""
    error: str | None = None
    provider_message_id: str | None = None


# ── Schedules ───────────────────────────────────────────────────────────────

class ScheduleOut(BaseModel):
    id: str
    project_id: str
    project_name: str
    name: str
    is_active: bool
    frequency: str
    hour: int
    minute: int
    day_of_week: int
    day_of_month: int
    cron_expression: str | None = None
    timezone: str
    analyses: list[str] = Field(default_factory=list)
    rules: dict[str, Any] = Field(default_factory=dict)
    min_severity: str
    cooldown_hours: int
    max_alerts_per_run: int
    channel_ids: list[str] = Field(default_factory=list)
    language: str
    quiet_hours_start: int | None = None
    quiet_hours_end: int | None = None
    send_when_nothing_found: bool
    last_run_at: datetime | None = None
    last_status: str | None = None
    next_run_at: datetime | None = None
    created_at: datetime
    # Rendered by the backend so the UI never has to reimplement cron semantics
    # — and so what the user reads back is what will actually fire.
    description: str = ""


class CreateScheduleRequest(BaseModel):
    name: str = "Daily check"
    is_active: bool = True
    frequency: str = "daily"
    hour: int = Field(default=7, ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)
    day_of_week: int = Field(default=0, ge=0, le=6)
    day_of_month: int = Field(default=1, ge=1, le=28)
    cron_expression: str | None = None
    timezone: str = "UTC"
    analyses: list[str] = Field(default_factory=lambda: ["insights", "root_cause"])
    rules: dict[str, Any] = Field(default_factory=dict)
    min_severity: str = "medium"
    cooldown_hours: int = Field(default=24, ge=0, le=720)
    max_alerts_per_run: int = Field(default=5, ge=1, le=25)
    channel_ids: list[str] = Field(default_factory=list)
    language: str = "en"
    quiet_hours_start: int | None = Field(default=None, ge=0, le=23)
    quiet_hours_end: int | None = Field(default=None, ge=0, le=23)
    send_when_nothing_found: bool = False

    @field_validator("frequency")
    @classmethod
    def _known_frequency(cls, value: str) -> str:
        if value not in FREQUENCIES:
            raise ValueError(f"Unknown frequency '{value}'. One of: {', '.join(FREQUENCIES)}")
        return value

    @field_validator("min_severity")
    @classmethod
    def _known_severity(cls, value: str) -> str:
        if value not in ALERT_SEVERITIES:
            raise ValueError(
                f"Unknown severity '{value}'. One of: {', '.join(ALERT_SEVERITIES)}"
            )
        return value


class UpdateScheduleRequest(BaseModel):
    name: str | None = None
    is_active: bool | None = None
    frequency: str | None = None
    hour: int | None = Field(default=None, ge=0, le=23)
    minute: int | None = Field(default=None, ge=0, le=59)
    day_of_week: int | None = Field(default=None, ge=0, le=6)
    day_of_month: int | None = Field(default=None, ge=1, le=28)
    cron_expression: str | None = None
    timezone: str | None = None
    analyses: list[str] | None = None
    rules: dict[str, Any] | None = None
    min_severity: str | None = None
    cooldown_hours: int | None = Field(default=None, ge=0, le=720)
    max_alerts_per_run: int | None = Field(default=None, ge=1, le=25)
    channel_ids: list[str] | None = None
    language: str | None = None
    quiet_hours_start: int | None = Field(default=None, ge=0, le=23)
    quiet_hours_end: int | None = Field(default=None, ge=0, le=23)
    send_when_nothing_found: bool | None = None


# ── Runs and alerts ─────────────────────────────────────────────────────────

class RunOut(BaseModel):
    id: str
    schedule_id: str
    schedule_name: str = ""
    project_id: str
    project_name: str = ""
    status: str
    trigger: str
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int | None = None
    alerts_found: int
    alerts_delivered: int
    money_at_stake: float
    error: str | None = None


class RunDetailOut(RunOut):
    briefing_md: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)
    alerts: list[AlertOut] = Field(default_factory=list)
    deliveries: list[dict[str, Any]] = Field(default_factory=list)


class AlertOut(BaseModel):
    id: str
    run_id: str
    project_id: str
    project_name: str = ""
    fingerprint: str
    rule: str
    severity: str
    title: str
    body_md: str
    metric: str
    money_at_stake: float
    current_value: float | None = None
    prior_value: float | None = None
    change_pct: float | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    root_cause: dict[str, Any] = Field(default_factory=dict)
    status: str
    read_at: datetime | None = None
    created_at: datetime


class UpdateAlertRequest(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def _known_status(cls, value: str) -> str:
        if value not in ALERT_STATUSES:
            raise ValueError(f"Unknown status '{value}'. One of: {', '.join(ALERT_STATUSES)}")
        return value


class AlertSummaryResponse(BaseModel):
    unread: int
    recent: list[AlertOut] = Field(default_factory=list)


class PreviewResponse(BaseModel):
    """What a schedule *would* have sent, without sending or recording it."""

    available: bool
    reason: str = ""
    subject: str = ""
    briefing_md: str = ""
    text: str = ""
    money_at_stake: float = 0.0
    findings: list[dict[str, Any]] = Field(default_factory=list)
    suppressed: list[dict[str, Any]] = Field(default_factory=list)
    root_cause: dict[str, Any] = Field(default_factory=dict)
    would_deliver: bool = False
    channels: list[dict[str, Any]] = Field(default_factory=list)


class SchedulerStatusResponse(BaseModel):
    running: bool
    reason: str = ""
    jobs: list[dict[str, Any]] = Field(default_factory=list)


RunDetailOut.model_rebuild()
