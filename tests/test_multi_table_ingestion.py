from __future__ import annotations

import pandas as pd

from ingestion.multi_table import choose_primary_table, tables_from_datasets
from tools.ingestion import Dataset


def test_each_file_stays_a_separate_table() -> None:
    datasets = [
        Dataset(name="customers.csv", df=pd.DataFrame({"customer_id": [1, 2, 3]})),
        Dataset(name="orders.csv", df=pd.DataFrame({"order_id": [10, 11], "customer_id": [1, 2]})),
    ]
    tables = tables_from_datasets(datasets)

    assert set(tables) == {"customers", "orders"}
    assert len(tables["customers"]) == 3
    assert len(tables["orders"]) == 2
    # No join/append happened — columns are untouched.
    assert list(tables["orders"].columns) == ["order_id", "customer_id"]


def test_duplicate_stem_names_are_deduplicated() -> None:
    datasets = [
        Dataset(name="data.csv", df=pd.DataFrame({"a": [1]})),
        Dataset(name="data.xlsx", df=pd.DataFrame({"b": [2]})),
    ]
    tables = tables_from_datasets(datasets)

    assert set(tables) == {"data", "data_2"}


def test_choose_primary_table_picks_largest() -> None:
    tables = {
        "customers": pd.DataFrame({"id": [1, 2, 3]}),
        "orders": pd.DataFrame({"id": range(100)}),
    }
    assert choose_primary_table(tables) == "orders"


def test_choose_primary_table_empty_raises() -> None:
    import pytest

    with pytest.raises(ValueError):
        choose_primary_table({})
