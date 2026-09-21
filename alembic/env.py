"""Alembic environment for the platform metadata tables only.

Deliberately does NOT set include_schemas=True: dynamically-created
project_<slug> schemas (see db/ddl.py) must stay invisible to autogenerate so
Alembic never proposes dropping or altering per-project data tables. Only the
`public`-schema tables registered on db.base.Base are managed here.
"""

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from db import auth_models  # noqa: F401  (registers models on Base.metadata)
from db import crm_models  # noqa: F401  (registers models on Base.metadata)
from db import monitoring_models  # noqa: F401  (registers models on Base.metadata)
from db import platform_models  # noqa: F401  (registers models on Base.metadata)
from db import report_models  # noqa: F401  (registers models on Base.metadata)
from db import usage_models  # noqa: F401  (registers models on Base.metadata)
from db.base import Base
from tools.db_tools import _conn_str

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", _conn_str())

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
