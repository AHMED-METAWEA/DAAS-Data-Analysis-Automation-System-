"""add autonomous-monitoring tables

Schedules, runs, alerts, notification channels, delivery logs and metric
snapshots — Pillar III-1.

Revision ID: a7d4f2b91c30
Revises: 51516295d73b
Create Date: 2026-08-09

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a7d4f2b91c30'
down_revision: Union[str, None] = '51516295d73b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'notification_channels',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('type', sa.String(length=20), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('destination', sa.String(length=255), nullable=False),
        sa.Column('encrypted_credentials', sa.Text(), nullable=False),
        sa.Column('config', sa.JSON(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_notification_channels_user_id', 'notification_channels', ['user_id'])

    op.create_table(
        'monitor_schedules',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('project_id', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('frequency', sa.String(length=20), nullable=False),
        sa.Column('hour', sa.Integer(), nullable=False),
        sa.Column('minute', sa.Integer(), nullable=False),
        sa.Column('day_of_week', sa.Integer(), nullable=False),
        sa.Column('day_of_month', sa.Integer(), nullable=False),
        sa.Column('cron_expression', sa.String(length=120), nullable=True),
        sa.Column('timezone', sa.String(length=64), nullable=False),
        sa.Column('analyses', sa.JSON(), nullable=False),
        sa.Column('rules', sa.JSON(), nullable=False),
        sa.Column('min_severity', sa.String(length=20), nullable=False),
        sa.Column('cooldown_hours', sa.Integer(), nullable=False),
        sa.Column('max_alerts_per_run', sa.Integer(), nullable=False),
        sa.Column('channel_ids', sa.JSON(), nullable=False),
        sa.Column('language', sa.String(length=5), nullable=False),
        sa.Column('quiet_hours_start', sa.Integer(), nullable=True),
        sa.Column('quiet_hours_end', sa.Integer(), nullable=True),
        sa.Column('send_when_nothing_found', sa.Boolean(), nullable=False),
        sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_status', sa.String(length=20), nullable=True),
        sa.Column('next_run_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_monitor_schedules_project_id', 'monitor_schedules', ['project_id'])
    op.create_index('ix_monitor_schedules_user_id', 'monitor_schedules', ['user_id'])

    op.create_table(
        'monitor_runs',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('schedule_id', sa.String(length=32), nullable=False),
        sa.Column('project_id', sa.String(length=32), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('trigger', sa.String(length=20), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('alerts_found', sa.Integer(), nullable=False),
        sa.Column('alerts_delivered', sa.Integer(), nullable=False),
        sa.Column('money_at_stake', sa.Float(), nullable=False),
        sa.Column('briefing_md', sa.Text(), nullable=False),
        sa.Column('detail', sa.JSON(), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
        sa.ForeignKeyConstraint(['schedule_id'], ['monitor_schedules.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_monitor_runs_schedule_id', 'monitor_runs', ['schedule_id'])

    op.create_table(
        'monitor_alerts',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('run_id', sa.String(length=32), nullable=False),
        sa.Column('project_id', sa.String(length=32), nullable=False),
        sa.Column('user_id', sa.String(length=32), nullable=False),
        sa.Column('fingerprint', sa.String(length=120), nullable=False),
        sa.Column('rule', sa.String(length=64), nullable=False),
        sa.Column('severity', sa.String(length=20), nullable=False),
        sa.Column('title', sa.String(length=500), nullable=False),
        sa.Column('body_md', sa.Text(), nullable=False),
        sa.Column('metric', sa.String(length=64), nullable=False),
        sa.Column('money_at_stake', sa.Float(), nullable=False),
        sa.Column('current_value', sa.Float(), nullable=True),
        sa.Column('prior_value', sa.Float(), nullable=True),
        sa.Column('change_pct', sa.Float(), nullable=True),
        sa.Column('evidence', sa.JSON(), nullable=False),
        sa.Column('root_cause', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
        sa.ForeignKeyConstraint(['run_id'], ['monitor_runs.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_monitor_alerts_run_id', 'monitor_alerts', ['run_id'])
    op.create_index('ix_monitor_alerts_project_id', 'monitor_alerts', ['project_id'])
    op.create_index('ix_monitor_alerts_user_id', 'monitor_alerts', ['user_id'])
    op.create_index('ix_monitor_alerts_fingerprint', 'monitor_alerts', ['fingerprint'])
    op.create_index('ix_monitor_alerts_created_at', 'monitor_alerts', ['created_at'])
    op.create_index(
        'ix_alert_user_status_time', 'monitor_alerts', ['user_id', 'status', 'created_at']
    )
    op.create_index('ix_alert_fingerprint_time', 'monitor_alerts', ['fingerprint', 'created_at'])

    op.create_table(
        'monitor_deliveries',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('run_id', sa.String(length=32), nullable=False),
        sa.Column('alert_id', sa.String(length=32), nullable=True),
        sa.Column('channel_id', sa.String(length=32), nullable=True),
        sa.Column('channel_type', sa.String(length=20), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('destination', sa.String(length=255), nullable=False),
        sa.Column('provider_message_id', sa.String(length=255), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['alert_id'], ['monitor_alerts.id'], ),
        sa.ForeignKeyConstraint(['channel_id'], ['notification_channels.id'], ),
        sa.ForeignKeyConstraint(['run_id'], ['monitor_runs.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_monitor_deliveries_run_id', 'monitor_deliveries', ['run_id'])

    op.create_table(
        'metric_snapshots',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('project_id', sa.String(length=32), nullable=False),
        sa.Column('captured_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('metrics', sa.JSON(), nullable=False),
        sa.Column('row_count', sa.Integer(), nullable=False),
        sa.Column('data_last_date', sa.String(length=32), nullable=True),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_metric_snapshots_project_id', 'metric_snapshots', ['project_id'])
    op.create_index('ix_metric_snapshots_captured_at', 'metric_snapshots', ['captured_at'])
    op.create_index(
        'ix_snapshot_project_kind_time', 'metric_snapshots',
        ['project_id', 'kind', 'captured_at'],
    )


def downgrade() -> None:
    op.drop_table('metric_snapshots')
    op.drop_table('monitor_deliveries')
    op.drop_table('monitor_alerts')
    op.drop_table('monitor_runs')
    op.drop_table('monitor_schedules')
    op.drop_table('notification_channels')
