from __future__ import annotations

import pandas as pd

from agents.analytics.schema_intel import (
    build_schema_summary,
    detect_category_columns,
    detect_customer_column,
    detect_monetary_columns,
    detect_order_column,
    detect_product_column,
    detect_quantity_column,
    detect_time_column,
)


def test_detect_time_column_datetime_dtype() -> None:
    df = pd.DataFrame({"order_date": pd.date_range("2024-01-01", periods=5), "sales": [1, 2, 3, 4, 5]})
    assert detect_time_column(df) == "order_date"


def test_detect_time_column_string_parsable() -> None:
    df = pd.DataFrame({"date": ["2024-01-01", "2024-01-02", "2024-01-03"], "value": [1, 2, 3]})
    assert detect_time_column(df) == "date"


def test_detect_time_column_keyword_match() -> None:
    df = pd.DataFrame({"created_at": ["2024-01-01"] * 3, "x": [1, 2, 3]})
    col = detect_time_column(df)
    assert col == "created_at"


def test_detect_time_column_none() -> None:
    df = pd.DataFrame({"a": [1, 2, 3], "b": [4.0, 5.0, 6.0]})
    assert detect_time_column(df) is None


def test_detect_monetary_columns_revenue_keywords() -> None:
    df = pd.DataFrame({"revenue": [100, 200], "cost": [50, 60], "orders": [5, 10]})
    cols = detect_monetary_columns(df)
    assert "revenue" in cols


def test_detect_monetary_columns_non_numeric() -> None:
    df = pd.DataFrame({"name": ["a", "b"], "desc": ["x", "y"]})
    assert detect_monetary_columns(df) == []


def test_detect_quantity_column() -> None:
    df = pd.DataFrame({"quantity": [1, 2, 3], "price": [10, 20, 30]})
    assert detect_quantity_column(df) == "quantity"


def test_detect_quantity_column_none() -> None:
    df = pd.DataFrame({"name": ["a", "b"], "score": [0.5, 0.8]})
    result = detect_quantity_column(df)
    assert result is None or result == ""


def test_detect_product_column() -> None:
    df = pd.DataFrame({"product_name": ["foo", "bar"], "sales": [1, 2]})
    assert detect_product_column(df) == "product_name"


def test_detect_customer_column() -> None:
    df = pd.DataFrame({"customer_id": [1, 2, 3], "value": [10, 20, 30]})
    assert detect_customer_column(df) == "customer_id"


def test_detect_order_column() -> None:
    df = pd.DataFrame({"order_id": [1, 2], "amount": [10, 20]})
    assert detect_order_column(df) == "order_id"


def test_detect_category_columns() -> None:
    df = pd.DataFrame({"category": ["A", "B"], "product": ["X", "Y"], "price": [1, 2]})
    cats = detect_category_columns(df)
    assert "category" in cats


def test_build_schema_summary() -> None:
    df = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=5),
        "revenue": [100, 200, 300, 400, 500],
        "quantity": [1, 2, 3, 4, 5],
        "product": ["A", "B", "C", "D", "E"],
        "customer_id": [10, 20, 30, 40, 50],
        "order_id": [1000, 1001, 1002, 1003, 1004],
        "category": ["X", "Y", "Z", "X", "Y"],
    })
    s = build_schema_summary(df)
    assert s["time_column"] == "date"
    assert "revenue" in s["monetary_columns"]
    assert s["quantity_column"] == "quantity"
    assert s["product_column"] == "product"
    assert s["customer_column"] == "customer_id"
    assert s["order_column"] == "order_id"
    assert "category" in s["category_columns"]
    assert s["row_count"] == 5
    assert s["column_count"] == 7
