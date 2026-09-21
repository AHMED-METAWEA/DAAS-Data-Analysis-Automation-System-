from __future__ import annotations

import io
import json

import pandas as pd
import pytest

from tools.ingestion import (
    Dataset,
    combine_datasets,
    ingest_files,
    load_tabular_file,
    plan_combination,
)


class FakeUpload(io.BytesIO):
    """Mimics a Streamlit UploadedFile (BytesIO with a .name)."""

    def __init__(self, data: bytes, name: str):
        super().__init__(data)
        self.name = name


# ── Single-file loading ──────────────────────────────────────────────────────


def test_load_csv_utf8() -> None:
    f = FakeUpload(b"a,b\n1,x\n2,y\n", "t.csv")
    df = load_tabular_file(f)
    assert list(df.columns) == ["a", "b"]
    assert len(df) == 2


def test_load_csv_semicolon_delimiter() -> None:
    f = FakeUpload(b"a;b\n1;x\n2;y\n", "t.csv")
    df = load_tabular_file(f)
    assert list(df.columns) == ["a", "b"]


def test_load_csv_arabic_cp1256() -> None:
    text = "المدينة,المبلغ\nالقاهرة,100\nجدة,200\n"
    f = FakeUpload(text.encode("cp1256"), "ar.csv")
    df = load_tabular_file(f)
    assert "المدينة" in df.columns
    assert df["المدينة"].iloc[0] == "القاهرة"


def test_load_csv_utf8_bom() -> None:
    f = FakeUpload("a,b\n1,x\n".encode("utf-8-sig"), "bom.csv")
    df = load_tabular_file(f)
    assert list(df.columns) == ["a", "b"]


def test_load_tsv() -> None:
    f = FakeUpload(b"a\tb\n1\tx\n", "t.tsv")
    df = load_tabular_file(f)
    assert list(df.columns) == ["a", "b"]


def test_load_json_records() -> None:
    payload = json.dumps([{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]).encode()
    df = load_tabular_file(FakeUpload(payload, "t.json"))
    assert len(df) == 2


def test_load_json_wrapped() -> None:
    payload = json.dumps({"data": [{"a": 1}, {"a": 2}]}).encode()
    df = load_tabular_file(FakeUpload(payload, "t.json"))
    assert len(df) == 2


def test_load_excel() -> None:
    pytest.importorskip("openpyxl")
    buf = io.BytesIO()
    pd.DataFrame({"a": [1, 2], "b": ["x", "y"]}).to_excel(buf, index=False)
    df = load_tabular_file(FakeUpload(buf.getvalue(), "t.xlsx"))
    assert list(df.columns) == ["a", "b"]


def test_unsupported_extension_raises() -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        load_tabular_file(FakeUpload(b"x", "t.pdf"))


# ── Combination planning ─────────────────────────────────────────────────────


def _tx(n: int = 10) -> pd.DataFrame:
    return pd.DataFrame({
        "order_id": range(n),
        "customer_id": [f"C{i % 3}" for i in range(n)],
        "product_id": [f"P{i % 4}" for i in range(n)],
        "total": [10.0 * i for i in range(n)],
    })


def test_plan_single() -> None:
    plan = plan_combination([Dataset("a.csv", _tx())])
    assert plan.strategy == "single"


def test_append_same_schema() -> None:
    d1 = Dataset("jan.csv", _tx(5))
    d2 = Dataset("feb.csv", _tx(7))
    plan = plan_combination([d1, d2])
    assert plan.strategy == "append"
    df, log = combine_datasets([d1, d2], plan)
    assert len(df) == 12
    assert set(df["_source_file"]) == {"jan.csv", "feb.csv"}


def test_join_dimension_on_key() -> None:
    fact = Dataset("sales.csv", _tx(20))
    dim = Dataset("products.csv", pd.DataFrame({
        "product_id": ["P0", "P1", "P2", "P3"],
        "category": ["A", "A", "B", "B"],
    }))
    plan = plan_combination([fact, dim])
    assert plan.strategy == "join"
    assert plan.primary == "sales.csv"
    assert plan.joins == [{"dimension": "products.csv", "key": "product_id"}]

    df, log = combine_datasets([fact, dim], plan)
    assert "category" in df.columns
    assert len(df) == 20                       # left join keeps fact rows
    assert df["category"].notna().all()


def test_join_drops_overlapping_columns() -> None:
    fact = Dataset("sales.csv", _tx(20))
    dim = Dataset("customers.csv", pd.DataFrame({
        "customer_id": ["C0", "C1", "C2"],
        "total": [1.0, 2.0, 3.0],              # clashes with fact 'total'
        "region": ["N", "S", "E"],
    }))
    plan = plan_combination([fact, dim])
    df, _ = combine_datasets([fact, dim], plan)
    assert "region" in df.columns
    # The fact table's own 'total' must be preserved untouched.
    assert df["total"].tolist() == _tx(20)["total"].tolist()


def test_unrelated_files_fall_back_to_primary() -> None:
    fact = Dataset("sales.csv", _tx(20))
    junk = Dataset("notes.csv", pd.DataFrame({"memo": ["hello", "world"]}))
    plan = plan_combination([fact, junk])
    assert plan.strategy == "separate"
    assert plan.primary == "sales.csv"
    assert plan.warnings
    df, _ = combine_datasets([fact, junk], plan)
    assert len(df) == 20


def test_primary_override() -> None:
    a = Dataset("big.csv", _tx(50))
    b = Dataset("small.csv", pd.DataFrame({"memo": ["x"]}))
    plan = plan_combination([a, b], primary="small.csv")
    assert plan.primary == "small.csv"


def test_ingest_files_end_to_end() -> None:
    f1 = FakeUpload(b"order_id,total\n1,10\n2,20\n", "jan.csv")
    f2 = FakeUpload(b"order_id,total\n3,30\n", "feb.csv")
    df, plan, log = ingest_files([f1, f2])
    assert plan.strategy == "append"
    assert len(df) == 3
