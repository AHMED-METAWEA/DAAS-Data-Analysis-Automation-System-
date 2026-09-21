from __future__ import annotations

import time

import pandas as pd
import pytest

from data_manager.manager import DataManager


def _counting_view_func(call_log: list, df: pd.DataFrame):
    def _view(project_id: str) -> pd.DataFrame:
        call_log.append(project_id)
        return df

    return _view


class TestCacheHitsAndMisses:
    def test_second_call_is_a_cache_hit(self) -> None:
        calls: list = []
        df = pd.DataFrame({"a": [1, 2, 3]})
        manager = DataManager(ttl_seconds=60, view_funcs={"fake": _counting_view_func(calls, df)})

        first = manager.get_view("proj-1", "fake")
        second = manager.get_view("proj-1", "fake")

        assert len(calls) == 1  # underlying view function only ran once
        assert manager.misses == 1
        assert manager.hits == 1
        pd.testing.assert_frame_equal(first, second)

    def test_different_projects_do_not_share_cache_entries(self) -> None:
        calls: list = []
        df = pd.DataFrame({"a": [1]})
        manager = DataManager(ttl_seconds=60, view_funcs={"fake": _counting_view_func(calls, df)})

        manager.get_view("proj-1", "fake")
        manager.get_view("proj-2", "fake")

        assert calls == ["proj-1", "proj-2"]
        assert manager.misses == 2

    def test_different_filters_are_different_cache_entries(self) -> None:
        calls: list = []
        df = pd.DataFrame({"a": [1]})
        manager = DataManager(ttl_seconds=60, view_funcs={"fake": _counting_view_func(calls, df)})

        manager.get_view("proj-1", "fake", filters={"region": "US"})
        manager.get_view("proj-1", "fake", filters={"region": "EU"})
        manager.get_view("proj-1", "fake", filters={"region": "US"})  # repeat -> hit

        assert len(calls) == 2
        assert manager.hits == 1

    def test_unknown_view_name_raises(self) -> None:
        manager = DataManager(view_funcs={"fake": lambda pid: pd.DataFrame()})
        with pytest.raises(ValueError, match="Unknown view"):
            manager.get_view("proj-1", "not_a_real_view")


class TestFilters:
    def test_filters_applied_after_caching_the_unfiltered_view(self) -> None:
        calls: list = []
        df = pd.DataFrame({"region": ["US", "EU", "US"], "value": [1, 2, 3]})
        manager = DataManager(ttl_seconds=60, view_funcs={"fake": _counting_view_func(calls, df)})

        filtered = manager.get_view("proj-1", "fake", filters={"region": "US"})
        assert len(filtered) == 2
        assert set(filtered["region"]) == {"US"}


class TestTtlExpiry:
    def test_entry_expires_and_refetches_after_ttl(self) -> None:
        calls: list = []
        df = pd.DataFrame({"a": [1]})
        manager = DataManager(ttl_seconds=0.05, view_funcs={"fake": _counting_view_func(calls, df)})

        manager.get_view("proj-1", "fake")
        time.sleep(0.1)
        manager.get_view("proj-1", "fake")

        assert len(calls) == 2  # second call was a genuine re-fetch, not a hit


class TestInvalidate:
    def test_invalidate_one_view_leaves_others_cached(self) -> None:
        calls: list = []
        df = pd.DataFrame({"a": [1]})
        manager = DataManager(
            ttl_seconds=60,
            view_funcs={
                "fake_a": _counting_view_func(calls, df),
                "fake_b": _counting_view_func(calls, df),
            },
        )
        manager.get_view("proj-1", "fake_a")
        manager.get_view("proj-1", "fake_b")

        manager.invalidate("proj-1", "fake_a")

        manager.get_view("proj-1", "fake_a")  # re-fetched
        manager.get_view("proj-1", "fake_b")  # still cached

        assert len(calls) == 3  # a, b, a-again — not b-again

    def test_invalidate_whole_project_drops_all_its_views(self) -> None:
        calls: list = []
        df = pd.DataFrame({"a": [1]})
        manager = DataManager(ttl_seconds=60, view_funcs={"fake": _counting_view_func(calls, df)})

        manager.get_view("proj-1", "fake")
        manager.get_view("proj-2", "fake")
        manager.invalidate("proj-1")

        manager.get_view("proj-1", "fake")  # re-fetched
        manager.get_view("proj-2", "fake")  # still cached

        assert calls == ["proj-1", "proj-2", "proj-1"]
