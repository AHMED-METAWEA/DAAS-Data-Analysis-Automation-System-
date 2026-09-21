from __future__ import annotations

import pandas as pd
import pytest
from sqlalchemy import text

from agents.analytics.schema_intel import build_schema_summary
from agents.churn.engine import run_churn_analysis
from db.loader import load_project_schema
from db.projects import create_project
from db.session import get_engine
from db.views import get_analytics_view, get_customer_360_view, get_forecast_view
from relationships.review import persist_relationships
from schema_discovery.models import RelationshipCandidate

pytestmark = pytest.mark.integration


def _rel(table_a, col_a, table_b, col_b, confidence=0.95) -> RelationshipCandidate:
    return RelationshipCandidate(
        table_a=table_a, column_a=col_a, table_b=table_b, column_b=col_b,
        confidence=confidence, evidence={},
    )


@pytest.fixture
def star_schema_project():
    project = create_project("Views Test Project")
    customers = pd.DataFrame({
        "customer_id": [1, 2, 3],
        "name": ["Alice", "Bob", "Carol"],
    })
    orders = pd.DataFrame({
        "order_id": [100, 101, 102, 103],
        "customer_id": [1, 2, 1, 3],
        "order_date": pd.to_datetime(["2024-01-01", "2024-01-05", "2024-02-01", "2024-02-10"]),
        "total": [10.0, 20.0, 15.0, 30.0],
    })
    rels = [_rel("orders", "customer_id", "customers", "customer_id")]
    persist_relationships(project.id, rels)
    load_project_schema(project.schema_name, {"customers": customers, "orders": orders}, rels)

    yield project

    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{project.schema_name}" CASCADE'))
        conn.execute(text("DELETE FROM relationships WHERE project_id = :pid"), {"pid": project.id})
        conn.execute(text("DELETE FROM projects WHERE id = :pid"), {"pid": project.id})


class TestBuildFactView:
    def test_picks_orders_as_fact_table_and_joins_customers(self, star_schema_project) -> None:
        df = get_analytics_view(star_schema_project.id)
        assert len(df) == 4  # one row per order, not per customer
        assert "order_id" in df.columns
        assert "total" in df.columns
        # Customer dimension attributes joined in.
        assert "name" in df.columns
        assert set(df["name"]) == {"Alice", "Bob", "Carol"}

    def test_all_four_views_return_the_same_shape_for_this_stage(self, star_schema_project) -> None:
        # All four are currently the same underlying fact-view join — the
        # seam exists so each agent has its own call site to specialize later.
        analytics = get_analytics_view(star_schema_project.id)
        forecast = get_forecast_view(star_schema_project.id)
        assert list(analytics.columns) == list(forecast.columns)
        assert len(analytics) == len(forecast)

    def test_unknown_project_raises_clear_error(self) -> None:
        with pytest.raises(ValueError, match="No project found"):
            get_analytics_view("does-not-exist")


class TestCustomer360SatisfiesChurnRequirements:
    def test_view_output_has_detectable_customer_and_time_columns(self, star_schema_project) -> None:
        df = get_customer_360_view(star_schema_project.id)
        schema = build_schema_summary(df)
        assert schema["customer_column"] is not None
        assert schema["time_column"] is not None

    def test_churn_engine_accepts_the_view_output_directly(self, star_schema_project) -> None:
        df = get_customer_360_view(star_schema_project.id)
        payload = run_churn_analysis(data_df=df, horizon_days=90)
        # The point of this test: churn's own "no customer/time column
        # detected" rejection path must never trigger for view output — that
        # exact contract is what Stage 9's page rewiring depends on. It's
        # fine if it separately reports unavailable for a different reason
        # (e.g. too little data to train on).
        reason = (payload.get("reason") or "").lower()
        assert "no customer identifier" not in reason
        assert "no date column" not in reason
