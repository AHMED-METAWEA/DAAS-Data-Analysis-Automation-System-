"""add user model_preferences

Revision ID: e82100c64e7e
Revises: c5fde4b1e753
Create Date: 2026-07-17 18:55:34.240388

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e82100c64e7e'
down_revision: Union[str, None] = 'c5fde4b1e753'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NOTE: autogenerate also proposed dropping forecast_history,
    # forecast_evaluations, forecasts, forecast_explanations and
    # forecast_runs — those are unmanaged, raw-DDL legacy tables outside
    # this Alembic environment's model metadata, not something this
    # migration should touch. Stripped; see alembic/env.py's notes.
    op.add_column('users', sa.Column('model_preferences', sa.JSON(), nullable=False, server_default='{}'))
    op.alter_column('users', 'model_preferences', server_default=None)


def downgrade() -> None:
    op.drop_column('users', 'model_preferences')
