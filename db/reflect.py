"""Reflect a project's generated schema back into SQLAlchemy Table objects.

Plain ``MetaData().reflect()`` — not ``automap``. ``db/views.py`` (Stage 8)
builds its own ``select()``/``join()`` calls and never needs automap's ORM
relationship-traversal, so automap would add fragility (naming collisions,
relationship auto-detection edge cases) for a benefit nothing consumes.
"""

from __future__ import annotations

from sqlalchemy import MetaData, Table

from db.session import get_engine


def reflect_project_schema(schema_name: str) -> MetaData:
    engine = get_engine()
    metadata = MetaData(schema=schema_name)
    metadata.reflect(bind=engine, schema=schema_name)
    return metadata


def get_table(schema_name: str, table_name: str) -> Table:
    metadata = reflect_project_schema(schema_name)
    return metadata.tables[f"{schema_name}.{table_name}"]
