"""Semantic, per-agent views over a project's stored tables — Stage 8.

Each function returns ONE flat pandas DataFrame — the exact seam that lets
every engine entry point (``run_analytics``, ``generate_dashboard``,
``ForecastPipeline.run``, ``run_marketing_analytics``, ``run_churn_analysis``)
stay 100% unchanged: they already accept a plain DataFrame via their
``data_df`` parameter.

For a single-table project the view is just the whole table. For
multi-table projects, the fact table (whichever table is the "many"/FK side
of the most approved relationships, or the only table if there are none) is
left-joined with every dimension table it's directly related to, built with
SQLAlchemy Core `select()`/`join()` over the tables reflected in
``db/reflect.py``.
"""

from __future__ import annotations

from collections import Counter

import pandas as pd
from sqlalchemy import func, select

from db.projects import get_project
from db.reflect import reflect_project_schema
from db.session import get_engine
from relationships.review import load_relationships


def _rank_fact_candidates(table_names: list[str], relationships) -> list[str]:
    """Table names ordered by how well they'd serve as the "fact" table:
    most relationship-referenced first (the old ``_pick_fact_table`` rule),
    then every other table as a fallback, in stable ``table_names`` order.
    """
    counts = Counter(r.table_a for r in relationships if r.table_a in table_names)
    ranked = [name for name, _ in counts.most_common()]
    ranked += [name for name in table_names if name not in ranked]
    return ranked


def _pick_fact_table(table_names: list[str], relationships) -> str:
    return _rank_fact_candidates(table_names, relationships)[0]


def _schema_name(project_id: str) -> str:
    project = get_project(project_id)
    if project is None:
        raise ValueError(f"No project found with id '{project_id}'")
    return project.schema_name


def build_fact_view(project_id: str) -> pd.DataFrame:
    """The shared implementation behind all four per-agent views below."""
    schema_name = _schema_name(project_id)
    metadata = reflect_project_schema(schema_name)
    table_names = sorted(name.split(".", 1)[1] for name in metadata.tables)
    if not table_names:
        return pd.DataFrame()

    relationships = load_relationships(project_id)
    engine = get_engine()
    with engine.connect() as conn:
        # The top relationship-ranked candidate is often right, but a table
        # can be the most-referenced "fact" side and still hold zero rows
        # (e.g. a stalled multi-table decomposition) while real data sits in
        # an unrelated table. Skip empty candidates rather than silently
        # returning an empty view when the project clearly has data.
        candidates = _rank_fact_candidates(table_names, relationships)
        fact_name = next(
            (
                name for name in candidates
                if conn.execute(
                    select(func.count()).select_from(metadata.tables[f"{schema_name}.{name}"])
                ).scalar()
            ),
            candidates[0],
        )
        fact_table = metadata.tables[f"{schema_name}.{fact_name}"]

        query = select(fact_table)
        fact_cols = {c.name for c in fact_table.columns}
        joined = {fact_name}

        for rel in relationships:
            if rel.table_a != fact_name or rel.table_b in joined:
                continue
            dim_table = metadata.tables.get(f"{schema_name}.{rel.table_b}")
            if dim_table is None:
                continue
            fk_col = fact_table.columns.get(rel.column_a)
            pk_col = dim_table.columns.get(rel.column_b)
            if fk_col is None or pk_col is None:
                continue

            dim_cols = [
                col.label(col.name if col.name not in fact_cols else f"{rel.table_b}_{col.name}")
                for col in dim_table.columns
                if col.name != rel.column_b  # the join key is already on the fact side
            ]
            query = query.add_columns(*dim_cols).join(dim_table, fk_col == pk_col, isouter=True)
            joined.add(rel.table_b)

        return pd.read_sql(query, conn)


def get_analytics_view(project_id: str) -> pd.DataFrame:
    return build_fact_view(project_id)


def get_forecast_view(project_id: str) -> pd.DataFrame:
    return build_fact_view(project_id)


def get_marketing_view(project_id: str) -> pd.DataFrame:
    return build_fact_view(project_id)


def get_customer_360_view(project_id: str) -> pd.DataFrame:
    return build_fact_view(project_id)
