from __future__ import annotations

import pandas as pd

from tools.profiler_tools import build_column_profile, build_dataset_profile


def test_build_column_profile_defaults_to_not_a_key() -> None:
    profile = build_column_profile(pd.Series([1, 2, 3], name="total"))
    assert profile.is_relationship_key is False


def test_build_column_profile_marks_relationship_key() -> None:
    profile = build_column_profile(pd.Series([1, 2, 3], name="customer_id"), is_relationship_key=True)
    assert profile.is_relationship_key is True


def test_build_dataset_profile_without_key_columns_is_unchanged() -> None:
    df = pd.DataFrame({"customer_id": [1, 2, 3], "total": [10.0, 20.0, 30.0]})
    profile = build_dataset_profile(df)
    assert all(not c.is_relationship_key for c in profile.columns)


def test_build_dataset_profile_flags_only_named_key_columns() -> None:
    df = pd.DataFrame({"customer_id": [1, 2, 3], "total": [10.0, 20.0, 30.0]})
    profile = build_dataset_profile(df, key_columns={"customer_id"})
    by_name = {c.name: c for c in profile.columns}
    assert by_name["customer_id"].is_relationship_key is True
    assert by_name["total"].is_relationship_key is False
