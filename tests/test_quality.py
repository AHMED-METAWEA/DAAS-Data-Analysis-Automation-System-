from __future__ import annotations

import pandas as pd

from agents.analytics.quality import compute_quality


def test_compute_quality_empty_df() -> None:
    df = pd.DataFrame()
    notes = compute_quality(df)
    assert isinstance(notes, list)


def test_compute_quality_missing_values() -> None:
    df = pd.DataFrame({"a": [1, None, 3, None, 5], "b": [1, 2, 3, 4, 5]})
    notes = compute_quality(df)
    missing_notes = [n for n in notes if "missing" in n.get("check", "").lower()]
    assert len(missing_notes) > 0
    for n in missing_notes:
        if n.get("column") == "a":
            assert n["count"] == 2
            assert n["percentage"] == 40.0


def test_compute_quality_no_missing() -> None:
    df = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})
    notes = compute_quality(df)
    missing_notes = [n for n in notes if "missing" in n.get("check", "").lower()]
    for n in missing_notes:
        assert n["total_missing"] == 0


def test_compute_quality_duplicates() -> None:
    df = pd.DataFrame({"a": [1, 1, 2, 2, 2], "b": [10, 10, 20, 20, 30]})
    notes = compute_quality(df)
    dup_notes = [n for n in notes if "duplicate" in n.get("check", "").lower()]
    assert len(dup_notes) > 0
    assert dup_notes[0]["count"] >= 2


def test_compute_quality_outliers() -> None:
    df = pd.DataFrame({"value": [1, 2, 3, 4, 5, 100, 6, 7, 8, 9]})
    notes = compute_quality(df)
    outlier_notes = [n for n in notes if "outlier" in n.get("check", "").lower()]
    assert len(outlier_notes) > 0


def test_compute_quality_column_types() -> None:
    df = pd.DataFrame({
        "int_col": [1, 2, 3],
        "float_col": [1.0, 2.5, 3.3],
        "str_col": ["a", "b", "c"],
        "date_col": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
    })
    notes = compute_quality(df)
    type_notes = [n for n in notes if n.get("check") == "column_types"]
    assert len(type_notes) == 1
