from __future__ import annotations

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import relationships.review as review
from db import platform_models  # noqa: F401 (registers models on Base.metadata)
from db.base import Base
from schema_discovery.models import RelationshipCandidate


@pytest.fixture()
def sqlite_session_factory(monkeypatch):
    """Fast, hermetic round-trip tests against an in-memory sqlite DB instead
    of the real docker-compose Postgres — the platform models are portable
    enough (no Postgres-only JSON/array types beyond what sqlite's JSON
    variant supports) to make this a reliable unit-test substitute."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(review, "get_session", lambda: factory())
    return factory


def _candidate(table_a, col_a, table_b, col_b, confidence, **evidence) -> RelationshipCandidate:
    return RelationshipCandidate(
        table_a=table_a, column_a=col_a, table_b=table_b, column_b=col_b,
        confidence=confidence, evidence=evidence,
    )


class TestBucketCandidates:
    def test_buckets_by_threshold(self) -> None:
        auto = _candidate("orders", "customer_id", "customers", "customer_id", 0.95)
        review_bucket = _candidate("a", "x", "b", "y", 0.6)
        manual = _candidate("c", "z", "d", "w", 0.2)

        buckets = review.bucket_candidates([auto, review_bucket, manual])

        assert buckets["auto"] == [auto]
        assert buckets["review"] == [review_bucket]
        assert buckets["manual"] == [manual]

    def test_boundary_values(self) -> None:
        at_auto_floor = _candidate("a", "x", "b", "y", 0.85)
        at_review_floor = _candidate("c", "z", "d", "w", 0.5)

        buckets = review.bucket_candidates([at_auto_floor, at_review_floor])

        assert buckets["auto"] == [at_auto_floor]
        assert buckets["review"] == [at_review_floor]


class TestValidateRelationship:
    def test_reports_orphan_ratio_from_coverage(self) -> None:
        c = _candidate(
            "orders", "customer_id", "customers", "customer_id", 0.9,
            pk_uniqueness=1.0, fk_coverage=0.8, dtype_compatibility=1.0, name_similarity=1.0,
        )
        stats = review.validate_relationship(c)
        assert stats["orphan_ratio"] == pytest.approx(0.2)
        assert stats["dtype_compatible"] is True

    def test_flags_dtype_incompatible(self) -> None:
        c = _candidate("a", "x", "b", "y", 0.6, dtype_compatibility=0.0, fk_coverage=0.5, pk_uniqueness=0.9)
        stats = review.validate_relationship(c)
        assert stats["dtype_compatible"] is False


class TestManualMatchOptions:
    def test_lists_other_table_columns(self) -> None:
        c = _candidate("a", "x", "b", "y", 0.2)
        tables = {"a": pd.DataFrame({"x": [1]}), "b": pd.DataFrame({"y": [1], "z": [2]})}
        assert review.manual_match_options(c, tables) == ["y", "z"]


class TestPersistAndLoad:
    def test_persist_then_load_round_trip(self, sqlite_session_factory) -> None:
        candidates = [
            _candidate("orders", "customer_id", "customers", "customer_id", 0.95),
            _candidate("a", "x", "b", "y", 0.6),
        ]
        review.persist_relationships("proj-1", candidates)

        loaded = review.load_relationships("proj-1")
        assert len(loaded) == 2
        pairs = {(r.table_a, r.column_a, r.table_b, r.column_b) for r in loaded}
        assert ("orders", "customer_id", "customers", "customer_id") in pairs
        assert all(r.status == "approved" for r in loaded)

    def test_persist_replaces_previous_set(self, sqlite_session_factory) -> None:
        review.persist_relationships("proj-1", [_candidate("a", "x", "b", "y", 0.9)])
        review.persist_relationships("proj-1", [_candidate("c", "z", "d", "w", 0.9)])

        loaded = review.load_relationships("proj-1")
        assert len(loaded) == 1
        assert loaded[0].table_a == "c"

    def test_approved_by_reflects_confidence_tier(self, sqlite_session_factory) -> None:
        review.persist_relationships("proj-1", [
            _candidate("orders", "customer_id", "customers", "customer_id", 0.95),
            _candidate("a", "x", "b", "y", 0.6),
        ])
        loaded = {r.table_a: r for r in review.load_relationships("proj-1")}
        assert loaded["orders"].approved_by == "auto"
        assert loaded["a"].approved_by == "user"

    def test_projects_are_isolated(self, sqlite_session_factory) -> None:
        review.persist_relationships("proj-1", [_candidate("a", "x", "b", "y", 0.9)])
        review.persist_relationships("proj-2", [_candidate("c", "z", "d", "w", 0.9)])

        assert len(review.load_relationships("proj-1")) == 1
        assert len(review.load_relationships("proj-2")) == 1
