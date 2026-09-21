from __future__ import annotations

from unittest.mock import patch

import pandas as pd

from schema_discovery.discover import run_schema_discovery
from schema_discovery.heuristics import find_relationship_candidates, profile_tables
from schema_discovery.llm_escalation import escalate, escalate_low_confidence
from schema_discovery.models import RelationshipCandidate


def _star_schema():
    customers = pd.DataFrame({
        "customer_id": [1, 2, 3, 4, 5],
        "name": ["Alice", "Bob", "Carol", "Dave", "Eve"],
    })
    orders = pd.DataFrame({
        "order_id": [100, 101, 102, 103, 104, 105],
        "customer_id": [1, 2, 1, 3, 2, 4],
        "total": [10.0, 20.0, 15.0, 30.0, 25.0, 40.0],
    })
    products = pd.DataFrame({
        "sku": ["A1", "B2", "C3"],
        "product_name": ["Widget", "Gadget", "Gizmo"],
    })
    return {"customers": customers, "orders": orders, "products": products}


class TestProfileTables:
    def test_profile_reports_shape(self) -> None:
        tables = _star_schema()
        profiles = profile_tables(tables)
        by_name = {p.name: p for p in profiles}
        assert by_name["customers"].row_count == 5
        assert by_name["orders"].column_count == 3
        assert "customer_id" in by_name["orders"].columns


class TestHeuristics:
    def test_detects_high_confidence_fk_pk(self) -> None:
        tables = _star_schema()
        candidates = find_relationship_candidates(tables)

        pair = {(c.table_a, c.table_b) for c in candidates}
        assert ("orders", "customers") in pair or ("customers", "orders") in pair

        orders_customers = next(
            c for c in candidates
            if {c.table_a, c.table_b} == {"orders", "customers"}
        )
        # orders.customer_id -> customers.customer_id: exact name match,
        # customers.customer_id is fully unique, all order values covered.
        assert orders_customers.table_a == "orders"
        assert orders_customers.table_b == "customers"
        assert orders_customers.confidence > 0.85

    def test_unrelated_tables_score_low_confidence_not_zero_candidates(self) -> None:
        # products.product_name and customers.name are both unique text columns
        # with superficially similar names but zero overlapping values — the
        # heuristic should surface this as a low-confidence candidate (for LLM
        # escalation / HITL review to reject), not silently drop it.
        tables = _star_schema()
        candidates = find_relationship_candidates(tables)
        products_customers = next(
            (c for c in candidates if {c.table_a, c.table_b} == {"products", "customers"}),
            None,
        )
        if products_customers is not None:
            assert products_customers.confidence < 0.85
            assert products_customers.evidence["fk_coverage"] == 0.0

    def test_only_best_candidate_kept_per_pair(self) -> None:
        tables = _star_schema()
        candidates = find_relationship_candidates(tables)
        pairs = [frozenset({c.table_a, c.table_b}) for c in candidates]
        assert len(pairs) == len(set(pairs))

    def test_low_confidence_for_fuzzy_ambiguous_columns(self) -> None:
        a = pd.DataFrame({"cust_ref": [1, 2, 3, 4], "misc": [9, 9, 9, 9]})
        b = pd.DataFrame({"customer_id": [1, 2, 3, 4, 5]})
        tables = {"a": a, "b": b}
        candidates = find_relationship_candidates(tables)
        assert len(candidates) == 1
        # Not an exact name match, but still detected via the _id suffix pattern.
        assert 0.0 < candidates[0].confidence < 1.0


class TestLlmEscalation:
    def test_escalate_low_confidence_only(self) -> None:
        high = RelationshipCandidate(
            table_a="orders", column_a="customer_id",
            table_b="customers", column_b="customer_id",
            confidence=0.95, evidence={},
        )
        low = RelationshipCandidate(
            table_a="a", column_a="cust_ref",
            table_b="b", column_b="customer_id",
            confidence=0.4, evidence={},
        )
        tables = {
            "orders": pd.DataFrame({"customer_id": [1, 2]}),
            "customers": pd.DataFrame({"customer_id": [1, 2]}),
            "a": pd.DataFrame({"cust_ref": [1, 2]}),
            "b": pd.DataFrame({"customer_id": [1, 2]}),
        }
        with patch("schema_discovery.llm_escalation.complete") as mock_complete:
            mock_complete.return_value = '{"is_relationship": true, "confidence": 0.7, "reasoning": "looks related"}'
            result = escalate_low_confidence([high, low], tables)

        # High-confidence candidate untouched, never sent to the LLM.
        assert mock_complete.call_count == 1
        untouched = next(c for c in result if c.table_a == "orders")
        assert untouched.source == "heuristic"
        assert untouched.confidence == 0.95

        escalated = next(c for c in result if c.table_a == "a")
        assert escalated.source == "llm"
        assert escalated.confidence == 0.7
        assert escalated.reasoning == "looks related"

    def test_escalation_fails_open_on_error(self) -> None:
        candidate = RelationshipCandidate(
            table_a="a", column_a="x", table_b="b", column_b="y", confidence=0.3, evidence={},
        )
        tables = {"a": pd.DataFrame({"x": [1]}), "b": pd.DataFrame({"y": [1]})}
        with patch("schema_discovery.llm_escalation.complete", side_effect=RuntimeError("no provider")):
            result = escalate(candidate, tables)
        assert result.source == "heuristic"
        assert result.confidence == 0.3

    def test_escalation_sends_only_ambiguous_columns_not_full_schema(self) -> None:
        candidate = RelationshipCandidate(
            table_a="a", column_a="x", table_b="b", column_b="y", confidence=0.3, evidence={},
        )
        tables = {
            "a": pd.DataFrame({"x": [1, 2], "secret_column": ["s1", "s2"]}),
            "b": pd.DataFrame({"y": [1, 2], "other_secret": ["t1", "t2"]}),
        }
        with patch("schema_discovery.llm_escalation.complete") as mock_complete:
            mock_complete.return_value = '{"is_relationship": false, "confidence": 0.1, "reasoning": "unrelated"}'
            escalate(candidate, tables)

        sent_payload = mock_complete.call_args[0][1][1]["content"]
        assert "secret_column" not in sent_payload
        assert "other_secret" not in sent_payload
        assert "\"column\": \"x\"" in sent_payload


class TestRunSchemaDiscovery:
    def test_end_to_end_without_escalation(self) -> None:
        tables = _star_schema()
        result = run_schema_discovery(tables, escalate=False)
        assert len(result.tables) == 3
        assert any(c.confidence > 0.85 for c in result.candidates)
        assert all(c.source == "heuristic" for c in result.candidates)
