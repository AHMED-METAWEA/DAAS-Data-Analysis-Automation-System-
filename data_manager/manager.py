"""Caching layer over the semantic views — Stage 8.

The Data Manager is the ONLY component that talks to Postgres for agent-data
reads (ingestion/DDL code is separate, used only during cleaning/storage).
Every page reads through here instead of ``st.session_state.clean_df``
directly (Stage 9), so storage can be swapped later without touching any
agent.
"""

from __future__ import annotations

import hashlib
import json

import pandas as pd
from cachetools import TTLCache

from db import views as _views

DEFAULT_TTL_SECONDS = 300

_VIEW_FUNCS = {
    "analytics": _views.get_analytics_view,
    "forecast": _views.get_forecast_view,
    "marketing": _views.get_marketing_view,
    "customer_360": _views.get_customer_360_view,
}


def _cache_key(project_id: str, view_name: str, filters: dict | None) -> str:
    filters_repr = json.dumps(filters or {}, sort_keys=True, default=str)
    digest = hashlib.sha256(filters_repr.encode()).hexdigest()[:16]
    return f"{project_id}:{view_name}:{digest}"


class DataManager:
    """Thin TTL-cached wrapper around the view functions in db/views.py.

    A module-level default instance is exported below for app code to use;
    tests can instantiate their own with a short TTL / mocked view functions.
    """

    def __init__(self, ttl_seconds: float = DEFAULT_TTL_SECONDS, view_funcs: dict | None = None):
        self._cache: TTLCache = TTLCache(maxsize=256, ttl=ttl_seconds)
        self._view_funcs = dict(view_funcs) if view_funcs is not None else dict(_VIEW_FUNCS)
        self.hits = 0
        self.misses = 0

    def get_view(
        self, project_id: str, view_name: str, filters: dict | None = None
    ) -> pd.DataFrame:
        if view_name not in self._view_funcs:
            raise ValueError(f"Unknown view '{view_name}'. Known views: {sorted(self._view_funcs)}")

        key = _cache_key(project_id, view_name, filters)
        cached = self._cache.get(key)
        if cached is not None:
            self.hits += 1
            return cached

        self.misses += 1
        df = self._view_funcs[view_name](project_id)
        if filters:
            for col, value in filters.items():
                if col in df.columns:
                    df = df[df[col] == value]
        self._cache[key] = df
        return df

    def invalidate(self, project_id: str, view_name: str | None = None) -> None:
        """Drop cached entries for a project — all views, or just one.
        Call this after re-cleaning or re-syncing a project's data.
        """
        prefix = f"{project_id}:{view_name}:" if view_name else f"{project_id}:"
        for key in [k for k in list(self._cache.keys()) if k.startswith(prefix)]:
            del self._cache[key]


default_manager = DataManager()


def get_view(project_id: str, view_name: str, filters: dict | None = None) -> pd.DataFrame:
    return default_manager.get_view(project_id, view_name, filters)


def invalidate(project_id: str, view_name: str | None = None) -> None:
    default_manager.invalidate(project_id, view_name)
