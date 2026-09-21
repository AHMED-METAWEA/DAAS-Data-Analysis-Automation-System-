"""CRM orchestration: turn a project's data into a stored customer snapshot.

The only module in the package that both computes and persists, and it does
neither itself — it sequences ``snapshot.build_customer_records`` and
``repository.save_snapshot``. Keeping the sequencing in one small module is
what lets the two sides stay independently testable.

Entry points:

    refresh_customer_state(project_id)        full refresh from the project view
    record_churn_run(project_id, payload)     fold a churn result into state
    get_latest_snapshot(project_id)           read-side convenience
"""

from __future__ import annotations

import logging
import time

import pandas as pd

from .contracts import SnapshotResult
from .repository import latest_snapshot, save_snapshot
from .snapshot import build_customer_records

logger = logging.getLogger(__name__)


def _load_view(project_id: str) -> pd.DataFrame | None:
    """The project's customer-360 view, or ``None`` if it cannot be loaded.

    Imported lazily: ``data_manager`` pulls in the reflection and ingestion
    stack, and the CRM compute layer must remain importable (and unit-testable)
    without a database being reachable.
    """
    try:
        from data_manager.manager import get_view

        return get_view(project_id, "customer_360")
    except Exception:
        logger.exception("Could not load customer_360 view for project %s", project_id)
        return None


def refresh_customer_state(
    project_id: str,
    *,
    data_df: pd.DataFrame | None = None,
    churn_payload: dict | None = None,
    run_churn: bool = True,
    horizon_days: int = 90,
    trigger: str = "manual",
) -> SnapshotResult:
    """Recompute and store the full customer state for a project.

    Parameters
    ----------
    data_df
        Pre-loaded view. When omitted the project's customer-360 view is loaded.
    churn_payload
        An existing churn result to fold in, avoiding a second model fit when
        the caller has just run one.
    run_churn
        When no payload is supplied, fit a churn model as part of the refresh.
        Set ``False`` for a fast observed-and-derived-only refresh.

    Returns a :class:`SnapshotResult` in every case — including failure. See
    ``repository.save_snapshot`` for why this layer does not raise.
    """
    started = time.perf_counter()

    df = data_df if data_df is not None else _load_view(project_id)
    if df is None or df.empty:
        return SnapshotResult(
            status="skipped",
            reason="This project has no saved data yet — finish the Data Workspace pipeline first.",
        )

    # Churn is optional by design. A model that will not fit (too little
    # history, no repeat behaviour) must cost the project its risk scores, not
    # its whole customer book.
    if churn_payload is None and run_churn:
        try:
            from agents.churn.engine import run_churn_analysis

            churn_payload = run_churn_analysis(data_df=df, horizon_days=horizon_days)
        except Exception as exc:
            logger.exception("Churn analysis failed during CRM refresh for %s", project_id)
            churn_payload = {"available": False, "reason": f"{type(exc).__name__}: {exc}"}

    records, meta = build_customer_records(df, churn_payload=churn_payload)
    duration_ms = int((time.perf_counter() - started) * 1000)

    return save_snapshot(
        project_id, records, meta, trigger=trigger, duration_ms=duration_ms
    )


def record_churn_run(
    project_id: str,
    churn_payload: dict,
    *,
    data_df: pd.DataFrame | None = None,
) -> SnapshotResult:
    """Persist customer state as a side effect of a churn analysis.

    Called from the churn route so that simply *using* the product builds the
    customer history, rather than requiring someone to remember a separate
    refresh. Reuses the churn result already in hand — no second model fit.
    """
    return refresh_customer_state(
        project_id,
        data_df=data_df,
        churn_payload=churn_payload,
        run_churn=False,
        trigger="churn_run",
    )


def get_latest_snapshot(project_id: str):
    return latest_snapshot(project_id)
