"""add reports table

Revision ID: 51516295d73b
Revises: e82100c64e7e
Create Date: 2026-07-17 19:06:52.291589

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '51516295d73b'
down_revision: Union[str, None] = 'e82100c64e7e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NOTE: autogenerate also proposed dropping forecast_history, forecasts,
    # forecast_explanations, forecast_evaluations and forecast_runs — those
    # are unmanaged, raw-DDL legacy tables outside this Alembic environment's
    # model metadata, not something this migration should touch. Stripped.
    op.create_table('reports',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('project_id', sa.String(length=32), nullable=False),
    sa.Column('type', sa.String(length=32), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('markdown', sa.Text(), nullable=False),
    sa.Column('grounding', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('reports')
