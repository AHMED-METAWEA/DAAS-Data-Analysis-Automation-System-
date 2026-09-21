"""Unified report verification — the ONE entry point every payload-backed
DAAS report (insights, marketing, forecast narrative) goes through.

There is a single mechanism, no weaker path: from the deterministic analytics
payload(s) it builds

  * the numeric reference set (magnitude check on every material figure), and
  * name → value bindings for headline metrics (AOV, revenue, orders, …) AND
    for every named entity — each RFM segment, top product, and channel value —
    so a figure is verified against the metric/entity it is *attributed to*, not
    just its magnitude.

``verify_report`` then runs the same ``check_grounding`` primitive with those
bindings, and the returned :class:`GroundingResult` exposes a ``certificate()``
(numerical + semantic + traceability) that callers attach to their output.
"""

from __future__ import annotations

from math import isfinite

from agents.reporting.grounding import GroundingResult, check_grounding

# Entity names shorter than this (or without a letter) are too ambiguous to bind
# safely — a two-char product code would fire on unrelated prose.
_MIN_ENTITY_LEN = 3


def _is_real_number(val: object) -> bool:
    """A bindable numeric value: not a bool, and finite.

    NaN/inf routinely appear in analytics payloads (empty group means,
    divide-by-zero rates) but can never be a figure a report legitimately
    quotes, so they must not become a binding target.
    """
    return isinstance(val, (int, float)) and not isinstance(val, bool) and isfinite(val)


def _put_scalar(labels: dict[str, float | list[float]], names: list[str], val: object) -> None:
    if _is_real_number(val):
        for name in names:
            labels[name.lower()] = float(val)


def _add_entity(labels: dict[str, float | list[float]], name: object, val: object) -> None:
    """Bind an entity name (segment/product/channel) to ONE of its own numbers.

    Multiple attributes (a segment's count, revenue and %) accumulate into a
    list; a figure attributed to that entity must match one of them, which
    catches cross-entity misattribution (quoting Espresso's sales as Latte's)
    without penalising any of the entity's legitimate figures.
    """
    if not isinstance(name, str):
        return
    key = name.strip().lower()
    if len(key) < _MIN_ENTITY_LEN or not any(ch.isalpha() for ch in key):
        return
    if not _is_real_number(val):
        return
    existing = labels.get(key)
    if isinstance(existing, list):
        if float(val) not in existing:
            existing.append(float(val))
    elif isinstance(existing, (int, float)):
        # A headline scalar already owns this name — don't clobber it.
        return
    else:
        labels[key] = [float(val)]


def build_reference_labels(*payloads: object) -> dict[str, float | list[float]]:
    """Metric/entity name → authoritative value(s) for semantic grounding.

    Headline KPIs bind to a single exact value; segments/products/channels bind
    to the set of their own figures. Names are lowercased; entity names too
    short or non-alphabetic are skipped as unsafe to bind.
    """
    labels: dict[str, float | list[float]] = {}

    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        kpi = payload.get("kpi") or {}

        _put_scalar(labels, ["total revenue"], kpi.get("revenue"))
        _put_scalar(labels, ["average order value", "aov"], kpi.get("aov"))
        _put_scalar(labels, ["median order value"], kpi.get("aov_median"))
        _put_scalar(labels, ["total orders", "number of orders"], kpi.get("orders"))
        _put_scalar(labels, ["total customers", "number of customers"], kpi.get("total_customers"))
        _put_scalar(
            labels,
            ["average revenue per customer", "revenue per customer"],
            kpi.get("revenue_per_customer_avg"),
        )

        # Top products (entity -> [revenue]).
        for name, rev in (kpi.get("top_products") or {}).items():
            _add_entity(labels, name, rev)

        # Category revenue breakdowns (entity -> [revenue]).
        for _dim, breakdown in (kpi.get("category_revenue") or {}).items():
            if isinstance(breakdown, dict):
                for name, rev in breakdown.items():
                    _add_entity(labels, name, rev)

        # RFM segments (entity -> [count, revenue, revenue_pct, …]).
        segments = (payload.get("rfm") or {}).get("segments") or {}
        for name, stats in segments.items():
            if isinstance(stats, dict):
                for v in stats.values():
                    _add_entity(labels, name, v)

        # Marketing channel dimensions (entity -> [revenue, share, …]).
        for _dim, breakdown in (payload.get("channels") or {}).items():
            if isinstance(breakdown, dict):
                for name, stats in breakdown.items():
                    if isinstance(stats, dict):
                        for v in stats.values():
                            _add_entity(labels, name, v)

    return labels


def verify_report(
    report_text: str,
    *payloads: object,
    extra_labeled: dict[str, float | list[float]] | None = None,
) -> GroundingResult:
    """Verify a report's figures against the payload(s) — magnitude AND
    metric/entity attribution — in one consistent pass. This is the function
    every payload-backed agent should call (never ``check_grounding`` with an
    ad-hoc label map), so grounding is identical across agents.
    """
    labels = build_reference_labels(*payloads)
    if extra_labeled:
        labels.update(extra_labeled)
    return check_grounding(report_text, *payloads, labeled=labels)
