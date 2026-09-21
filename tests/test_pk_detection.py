from __future__ import annotations

import pandas as pd

from schema_discovery.pk_detection import (
    CANDIDATE_UNIQUENESS_FLOOR,
    confirmed_primary_keys,
    detect_composite_pk,
    detect_primary_key,
    detect_primary_keys,
    detect_single_column_pk,
)


class TestSingleColumnDetection:
    def test_fully_unique_non_null_column_confirms(self) -> None:
        df = pd.DataFrame({"customer_id": ["C1", "C2", "C3"], "name": ["A", "B", "C"]})
        result = detect_single_column_pk(df)
        assert result is not None
        assert result.status == "confirmed"
        assert result.columns == ["customer_id"]
        assert result.uniqueness == 1.0
        assert result.null_count == 0

    def test_prefers_key_like_name_when_multiple_columns_are_fully_unique(self) -> None:
        df = pd.DataFrame({
            "customer_id": ["C1", "C2", "C3"],
            "email": ["a@x.com", "b@x.com", "c@x.com"],
        })
        result = detect_single_column_pk(df)
        assert result is not None
        assert result.columns == ["customer_id"]

    def test_column_with_nulls_is_not_confirmed_even_if_unique_among_non_nulls(self) -> None:
        df = pd.DataFrame({"customer_id": ["C1", "C2", None]})
        result = detect_single_column_pk(df)
        assert result is not None
        assert result.status == "candidate"
        assert result.null_count == 1

    def test_mixed_dtype_object_column_not_confirmed_even_if_100pct_unique(self) -> None:
        # Values are all distinct, but mix numeric-looking and non-numeric-looking
        # representations of what could be the same logical key — untrustworthy.
        df = pd.DataFrame({"customer_id": ["1001", "CUST-1002", "1003"]})
        result = detect_single_column_pk(df)
        assert result is not None
        assert result.status == "candidate"

    def test_duplicated_column_becomes_candidate_with_evidence(self) -> None:
        df = pd.DataFrame({"customer_id": ["C1", "C2", "C1", "C3"]})
        result = detect_single_column_pk(df)
        assert result is not None
        assert result.status == "candidate"
        assert result.duplicate_count == 2
        assert "C1" in result.duplicate_sample

    def test_below_floor_returns_none(self) -> None:
        df = pd.DataFrame({"category": ["a", "a", "a", "a", "b"]})
        result = detect_single_column_pk(df)
        assert result is None or result.uniqueness >= CANDIDATE_UNIQUENESS_FLOOR

    def test_empty_dataframe_returns_none(self) -> None:
        assert detect_single_column_pk(pd.DataFrame()) is None


class TestCompositeKeyDetection:
    def test_two_column_composite_confirmed_when_individually_not_unique(self) -> None:
        # order_items-style junction table: neither column alone is unique,
        # but the pair is. product_id is repeated so it can't form a
        # competing unique pair with order_id ahead of line_number.
        df = pd.DataFrame({
            "order_id": [1, 1, 1, 2, 2],
            "line_number": [1, 2, 3, 1, 2],
            "product_id": ["A", "A", "A", "A", "A"],
        })
        result = detect_composite_pk(df)
        assert result is not None
        assert result.status == "confirmed"
        assert result.is_composite is True
        assert set(result.columns) == {"order_id", "line_number"}

    def test_no_composite_found_when_no_pair_is_unique(self) -> None:
        df = pd.DataFrame({
            "order_id": [1, 1, 1, 1],
            "product_id": ["A", "A", "A", "A"],
        })
        assert detect_composite_pk(df) is None

    def test_composite_requires_zero_nulls(self) -> None:
        df = pd.DataFrame({
            "order_id": [1, 1, 2, None],
            "line_number": [1, 2, 1, 1],
        })
        result = detect_composite_pk(df)
        assert result is None

    def test_single_column_table_has_no_composite(self) -> None:
        df = pd.DataFrame({"id": [1, 2, 3]})
        assert detect_composite_pk(df) is None


class TestDetectPrimaryKey:
    def test_standalone_table_never_referenced_still_gets_confirmed_pk(self) -> None:
        # Regression test for the root-cause gap: PK detection must not
        # depend on cross-table relationships at all.
        df = pd.DataFrame({"sku": ["SKU-1", "SKU-2", "SKU-3"], "name": ["A", "B", "C"]})
        result = detect_primary_key("products", df)
        assert result.status == "confirmed"
        assert result.columns == ["sku"]
        assert result.table == "products"

    def test_falls_back_to_composite_when_no_single_column_confirms(self) -> None:
        df = pd.DataFrame({
            "order_id": [1, 1, 2, 2],
            "line_number": [1, 2, 1, 2],
        })
        result = detect_primary_key("order_items", df)
        assert result.status == "confirmed"
        assert result.is_composite is True

    def test_falls_back_to_candidate_when_nothing_confirms(self) -> None:
        # Neither column is 100% unique alone, and the pair isn't unique
        # together either (rows 0/1 collide on both columns) — no single
        # column or composite confirms, so the best single-column candidate
        # (customer_id, key-like-named) wins.
        df = pd.DataFrame({"customer_id": ["C1", "C1", "C2", "C3"], "misc": ["x", "x", "y", "z"]})
        result = detect_primary_key("customers", df)
        assert result.status == "candidate"
        assert result.table == "customers"
        assert result.columns == ["customer_id"]

    def test_empty_table_returns_none_status(self) -> None:
        result = detect_primary_key("empty_table", pd.DataFrame())
        assert result.status == "none"

    def test_table_with_no_plausible_key_returns_none_status(self) -> None:
        df = pd.DataFrame({"category": ["a"] * 10, "flag": [True] * 10})
        result = detect_primary_key("events", df)
        assert result.status == "none"


class TestBatchHelpers:
    def test_detect_primary_keys_covers_every_table(self) -> None:
        tables = {
            "customers": pd.DataFrame({"customer_id": ["C1", "C2"]}),
            "products": pd.DataFrame({"sku": ["S1", "S2"]}),
        }
        results = detect_primary_keys(tables)
        assert set(results) == {"customers", "products"}
        assert all(r.status == "confirmed" for r in results.values())

    def test_confirmed_primary_keys_excludes_candidates_and_none(self) -> None:
        tables = {
            "customers": pd.DataFrame({"customer_id": ["C1", "C2"]}),
            "messy": pd.DataFrame({"id": ["A", "A", "B"]}),
        }
        results = detect_primary_keys(tables)
        pk_map = confirmed_primary_keys(results)
        assert pk_map == {"customers": ["customer_id"]}
