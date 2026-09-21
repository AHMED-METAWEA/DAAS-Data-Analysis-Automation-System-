"""add CRM customer-state tables

The stateful customer layer — Pillar II P0. `crm_snapshots` records one
refresh; `customer_state` records one customer as they stood in that refresh.

Revision ID: b3e91c47d208
Revises: a7d4f2b91c30
Create Date: 2026-08-16

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b3e91c47d208'
down_revision: Union[str, None] = 'a7d4f2b91c30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'crm_snapshots',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('project_id', sa.String(length=32), nullable=False),
        sa.Column('snapshot_date', sa.Date(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('trigger', sa.String(length=20), nullable=False),
        sa.Column('customers', sa.Integer(), nullable=False),
        sa.Column('components', sa.JSON(), nullable=False),
        sa.Column('grain', sa.JSON(), nullable=False),
        sa.Column('row_count', sa.Integer(), nullable=False),
        sa.Column('history_days', sa.Integer(), nullable=False),
        sa.Column('stage_rules', sa.JSON(), nullable=False),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('project_id', 'snapshot_date', name='uq_crm_snapshot_project_date'),
    )
    op.create_index('ix_crm_snapshots_project_id', 'crm_snapshots', ['project_id'])
    op.create_index('ix_crm_snapshots_snapshot_date', 'crm_snapshots', ['snapshot_date'])
    op.create_index('ix_crm_snapshots_created_at', 'crm_snapshots', ['created_at'])

    op.create_table(
        'customer_state',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('snapshot_id', sa.String(length=32), nullable=False),
        sa.Column('project_id', sa.String(length=32), nullable=False),
        sa.Column('customer_id', sa.String(length=255), nullable=False),
        sa.Column('snapshot_date', sa.Date(), nullable=False),

        # Identity (PII — isolated here, never sent to an LLM provider)
        sa.Column('display_name', sa.String(length=255), nullable=True),
        sa.Column('contact', sa.JSON(), nullable=False),

        # Observed
        sa.Column('recency_days', sa.Integer(), nullable=True),
        sa.Column('frequency', sa.Integer(), nullable=True),
        sa.Column('monetary', sa.Float(), nullable=True),
        sa.Column('tenure_days', sa.Integer(), nullable=True),
        sa.Column('avg_order_value', sa.Float(), nullable=True),
        sa.Column('first_order_date', sa.Date(), nullable=True),
        sa.Column('last_order_date', sa.Date(), nullable=True),

        # Derived
        sa.Column('rfm_segment', sa.String(length=64), nullable=True),
        sa.Column('r_score', sa.Integer(), nullable=True),
        sa.Column('f_score', sa.Integer(), nullable=True),
        sa.Column('m_score', sa.Integer(), nullable=True),
        sa.Column('lifecycle_stage', sa.String(length=20), nullable=True),

        # Predicted
        sa.Column('churn_probability', sa.Float(), nullable=True),
        sa.Column('risk_tier', sa.String(length=10), nullable=True),
        sa.Column('predicted_clv', sa.Float(), nullable=True),
        sa.Column('clv_horizon_days', sa.Integer(), nullable=True),
        sa.Column('predicted_purchases', sa.Float(), nullable=True),

        # Prioritisation
        sa.Column('value_at_risk', sa.Float(), nullable=True),
        sa.Column('value_basis', sa.String(length=24), nullable=True),
        sa.Column('drivers', sa.JSON(), nullable=False),

        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['snapshot_id'], ['crm_snapshots.id'], ),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'project_id', 'customer_id', 'snapshot_date', name='uq_customer_state_identity'
        ),
    )
    op.create_index('ix_customer_state_snapshot_id', 'customer_state', ['snapshot_id'])
    op.create_index('ix_customer_state_project_id', 'customer_state', ['project_id'])
    op.create_index('ix_customer_state_customer_id', 'customer_state', ['customer_id'])
    op.create_index(
        'ix_customer_state_project_date', 'customer_state', ['project_id', 'snapshot_date']
    )
    # The prioritised call list — the CRM's primary read path.
    op.create_index(
        'ix_customer_state_value_at_risk',
        'customer_state',
        ['project_id', 'snapshot_date', sa.text('value_at_risk DESC')],
    )
    # The Customer 360 timeline.
    op.create_index(
        'ix_customer_state_timeline',
        'customer_state',
        ['project_id', 'customer_id', sa.text('snapshot_date DESC')],
    )


def downgrade() -> None:
    op.drop_index('ix_customer_state_timeline', table_name='customer_state')
    op.drop_index('ix_customer_state_value_at_risk', table_name='customer_state')
    op.drop_index('ix_customer_state_project_date', table_name='customer_state')
    op.drop_index('ix_customer_state_customer_id', table_name='customer_state')
    op.drop_index('ix_customer_state_project_id', table_name='customer_state')
    op.drop_index('ix_customer_state_snapshot_id', table_name='customer_state')
    op.drop_table('customer_state')

    op.drop_index('ix_crm_snapshots_created_at', table_name='crm_snapshots')
    op.drop_index('ix_crm_snapshots_snapshot_date', table_name='crm_snapshots')
    op.drop_index('ix_crm_snapshots_project_id', table_name='crm_snapshots')
    op.drop_table('crm_snapshots')
