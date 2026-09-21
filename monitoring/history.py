"""Recording what the metrics were, so tomorrow has something to compare against.

The distinction this module exists to preserve: "revenue is down 12% on last
week" needs *last week's recorded number*, not last week's number recomputed
from today's dataset.  Those two differ whenever the data is re-cleaned,
backfilled, re-joined or corrected — which, in a system whose whole first half
is a cleaning pipeline, is often.  Recomputing produces phantom alerts that
nobody can reproduce because the evidence for them no longer exists.
"""

from __future__ import annotations

from typing import Any

from db.monitoring import latest_snapshot, save_snapshot

# The metrics a snapshot carries. Deliberately a short, stable list: a snapshot
# is a time series, and a schema that grows every release produces a history
# where the interesting comparison is always against a field that did not exist.
SNAPSHOT_METRICS = (
    "revenue",
    "orders",
    "total_customers",
    "aov",
    "aov_median",
    "revenue_per_customer_avg",
    "avg_revenue_per_row",
)


def extract_metrics(payload: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    """The headline figures worth keeping, pulled out of one analytics run."""
    kpi = payload.get("kpi") or {}
    metrics: dict[str, Any] = {}
    for key in SNAPSHOT_METRICS:
        value = kpi.get(key)
        if value is None:
            continue
        try:
            metrics[key] = float(value)
        except (TypeError, ValueError):
            continue

    sections = (decision or {}).get("sections") or {}
    margin = sections.get("margin") or {}
    if margin.get("available"):
        for key in ("gross_profit", "gross_margin_pct"):
            if margin.get(key) is not None:
                metrics[key] = float(margin[key])
    repeat = sections.get("repeat") or {}
    if repeat.get("available") and repeat.get("repeat_rate_pct") is not None:
        metrics["repeat_rate_pct"] = float(repeat["repeat_rate_pct"])
    discount = sections.get("discount") or {}
    if discount.get("available") and discount.get("discount_given") is not None:
        metrics["discount_given"] = float(discount["discount_given"])
    return metrics


def data_last_date(decision: dict[str, Any]) -> str | None:
    coverage = ((decision or {}).get("sections") or {}).get("coverage") or {}
    return coverage.get("last_date") if coverage.get("available") else None


def latest_snapshot_metrics(project_id: str) -> dict[str, Any]:
    """The most recently recorded metrics, without recording anything.

    What a preview needs: it must compare against the same baseline the next
    real run will, while leaving that baseline untouched.
    """
    previous = latest_snapshot(project_id, "kpi")
    return {
        "previous_metrics": dict(previous.metrics) if previous else {},
        "previous_captured_at": previous.captured_at if previous else None,
    }


def capture(
    project_id: str,
    payload: dict[str, Any],
    decision: dict[str, Any],
) -> dict[str, Any]:
    """Record this run's metrics and return the ones recorded before it.

    Reading the previous snapshot *before* writing the new one is the whole
    contract — the other order compares the run against itself and reports that
    nothing ever changes.
    """
    previous = latest_snapshot(project_id, "kpi")
    previous_metrics = dict(previous.metrics) if previous else {}
    previous_at = previous.captured_at if previous else None

    metrics = extract_metrics(payload, decision)
    save_snapshot(
        project_id, "kpi", metrics,
        row_count=int((payload.get("metadata") or {}).get("row_count") or 0),
        data_last_date=data_last_date(decision),
    )
    return {"previous_metrics": previous_metrics, "previous_captured_at": previous_at}
