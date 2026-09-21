"""Resolves a project's semantic view (analytics/forecast/marketing/
customer_360) for the analytics routers — the API's equivalent of
tools/page_data.py's `resolve_view`, minus the `st.session_state` coupling:
`project_id` is an explicit parameter instead of being read from a global.
"""

from __future__ import annotations

import pandas as pd
from fastapi import HTTPException, status

from data_manager.manager import get_view


class NoProjectDataError(Exception):
    pass


def resolve_view(project_id: str, view_name: str) -> pd.DataFrame:
    try:
        df = get_view(project_id, view_name)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not load data for this project: {exc}",
        ) from exc
    if df is None or df.empty:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This project has no saved data yet — finish the Data Workspace pipeline and save first.",
        )
    return df
