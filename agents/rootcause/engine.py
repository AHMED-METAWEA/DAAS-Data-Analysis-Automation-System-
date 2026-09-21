"""Root-cause orchestrator — the one public entry point.

Sequence, and the reason for the order:

    schema  →  measure  →  windows  →  dimensions  →  search  →  bridge
                                                        │
                                                        ▼
                                            figures  →  narration

Everything up to ``search`` is exact arithmetic on the rows.  The figure
registry is built from that result, and only then is a model allowed to write a
sentence — around citation tokens it cannot alter.  So the failure mode of the
narration layer is a *thinner* explanation, never a wrong number.

Each stage degrades independently and states why: a dataset with no date column
gets a clear "there is nothing to compare" rather than a drill-down on an
invented period, and a dataset with no usable dimension is told which columns
were rejected and on what grounds.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from agents.analytics.schema_intel import build_schema_summary
from agents.constants import DEFAULT_INSIGHTS_MODEL
from agents.rootcause.dimensions import detect_dimensions
from agents.rootcause.figures import build_registry
from agents.rootcause.measures import (
    Measure,
    MeasureUnavailableError,
    WindowPair,
    available_measures,
    build_measure,
    resolve_windows,
)
from agents.rootcause.narrate import narrate
from agents.rootcause.schema import Explanation, RootCauseResult, Window
from agents.rootcause.search import RootCauseSearch, SearchConfig

logger = logging.getLogger(__name__)

# Below this many rows in a period, a "slice" is a handful of transactions and
# every drill-down is noise dressed up as a finding.
MIN_ROWS_PER_PERIOD = 20


def _slice_bridge(
    df: pd.DataFrame,
    rows: np.ndarray,
    measure: Measure,
    order_col: str | None,
    period: np.ndarray,
) -> dict | None:
    """Split a slice's own change into order count vs order size.

    The same decomposition as the insights revenue bridge, applied *inside* the
    slice.  It answers the question that always follows the drill-down: the
    North/Email decline — are fewer orders coming in, or are the same customers
    buying less?  Those are two different fixes.
    """
    if order_col is None or measure.values is None or measure.unit != "currency":
        return None
    if rows.size == 0:
        return None
    values = measure.values.to_numpy(dtype=float)
    orders = df[order_col].astype(str).to_numpy()

    def stats(flag: int) -> tuple[float, int]:
        sel = rows[period[rows] == flag]
        if sel.size == 0:
            return 0.0, 0
        return float(np.nan_to_num(values[sel]).sum()), int(np.unique(orders[sel]).size)

    rev_c, ord_c = stats(1)
    rev_p, ord_p = stats(0)
    if not ord_p or not ord_c:
        return {"available": False, "reason": "one of the periods has no orders in this slice"}
    aov_c, aov_p = rev_c / ord_c, rev_p / ord_p
    volume = (ord_c - ord_p) * aov_p
    basket = (aov_c - aov_p) * ord_p
    interaction = (ord_c - ord_p) * (aov_c - aov_p)
    return {
        "available": True,
        "orders_prior": ord_p, "orders_current": ord_c, "orders_change": ord_c - ord_p,
        "aov_prior": round(aov_p, 2), "aov_current": round(aov_c, 2),
        "aov_change": round(aov_c - aov_p, 2),
        "volume_effect": round(volume, 2),
        "basket_effect": round(basket, 2),
        "interaction_effect": round(interaction, 2),
        "residual": round((rev_c - rev_p) - (volume + basket + interaction), 2),
        "primary_driver": "order count" if abs(volume) >= abs(basket) else "order size",
    }


def _unavailable(reason: str, **extra) -> RootCauseResult:
    return RootCauseResult(available=False, reason=reason, **extra)


# The window picker's option text. Localised alongside the measure labels for
# the same reason: the picker and the result it produces have to be in one
# language, and the frontend renders both of these strings verbatim.
_WINDOW_MODE_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "auto": "Automatic (best available)",
        "last_month": "Last complete month vs the month before",
        "rolling_30d": "Last 30 days vs the previous 30",
        "custom": "Custom dates",
    },
    "ar": {
        "auto": "تلقائي (أفضل مقارنة متاحة)",
        "last_month": "آخر شهر مكتمل مقابل الشهر السابق له",
        "rolling_30d": "آخر 30 يومًا مقابل الـ 30 يومًا السابقة",
        "custom": "تواريخ مخصّصة",
    },
}


def _window_mode_label(language: str, mode: str) -> str:
    table = _WINDOW_MODE_LABELS.get(language) or _WINDOW_MODE_LABELS["en"]
    return table.get(mode) or _WINDOW_MODE_LABELS["en"][mode]


def _weekday_imbalance(dt: pd.Series, cur_mask: pd.Series, pri_mask: pd.Series) -> str:
    """Weekdays whose *number of calendar days* differs between the two periods.

    This is the confound that makes weekday a trap in period comparison: five
    Tuesdays against four is a 25% head start, and no amount of statistical
    testing on the revenue will notice, because the revenue really did differ.
    """
    def counts(mask: pd.Series) -> pd.Series:
        days = dt[mask].dt.normalize().dropna().drop_duplicates()
        return days.dt.day_name().value_counts()

    cur, pri = counts(cur_mask), counts(pri_mask)
    differences = []
    for day in sorted(set(cur.index) | set(pri.index)):
        c, p = int(cur.get(day, 0)), int(pri.get(day, 0))
        if c != p:
            differences.append(f"{day}: {c} vs {p}")
    return "; ".join(differences)


def run_root_cause(
    df: pd.DataFrame | None,
    *,
    measure: str = "revenue",
    window_mode: str = "auto",
    current_start: str | None = None,
    current_end: str | None = None,
    prior_start: str | None = None,
    prior_end: str | None = None,
    dimensions: list[str] | None = None,
    include_weekday: bool = False,
    max_depth: int = 3,
    beam_width: int = 48,
    top_k: int = 6,
    min_rows: int = 12,
    min_explanatory_power: float = 0.08,
    min_signal_to_noise: float = 2.0,
    node_budget: int = 40_000,
    with_narrative: bool = True,
    model: str | None = None,
    language: str = "en",
    business_context: str = "",
) -> RootCauseResult:
    """Explain a metric's change by finding the slices responsible for it."""
    if df is None or df.empty:
        return _unavailable("There is no data to analyse.")

    schema = build_schema_summary(df)
    time_col = schema.get("time_column")
    if not time_col or time_col not in df.columns:
        return _unavailable(
            "No date column was detected, so there are no two periods to compare. "
            "Root-cause analysis explains a *change*; without time there is no change to explain."
        )

    dt = pd.to_datetime(df[time_col], errors="coerce")
    if dt.notna().sum() == 0:
        return _unavailable(f"No value in '{time_col}' could be read as a date.")

    try:
        measure_obj = build_measure(df, measure, schema, language)
    except MeasureUnavailableError as exc:
        return _unavailable(str(exc))

    try:
        windows: WindowPair = resolve_windows(
            dt, mode=window_mode,
            current_start=current_start, current_end=current_end,
            prior_start=prior_start, prior_end=prior_end,
            language=language,
        )
    except MeasureUnavailableError as exc:
        return _unavailable(str(exc))

    cur_mask = windows.current.mask(dt).fillna(False)
    pri_mask = windows.prior.mask(dt).fillna(False)
    cur_rows, pri_rows = int(cur_mask.sum()), int(pri_mask.sum())
    if cur_rows < MIN_ROWS_PER_PERIOD or pri_rows < MIN_ROWS_PER_PERIOD:
        return _unavailable(
            f"{windows.current.label} has {cur_rows:,} rows and {windows.prior.label} has "
            f"{pri_rows:,} — at least {MIN_ROWS_PER_PERIOD} in each is needed before a "
            "difference between them means anything."
        )

    detected, skipped = detect_dimensions(
        df, schema=schema, time_column=time_col, include_weekday=include_weekday,
    )
    if dimensions:
        wanted = {d.strip() for d in dimensions if d and d.strip()}
        unknown = wanted - {d.name for d in detected}
        detected = [d for d in detected if d.name in wanted]
        for name in sorted(unknown):
            skipped.append({"column": name, "reason": "requested, but not usable as a dimension"})
    if not detected:
        # Name the rejected columns and why: "no dimensions" reads as a bug,
        # whereas "region was rejected as an identifier at 4,102 distinct values"
        # is something the user can fix upstream.
        return _unavailable(
            "No column in this dataset can act as a business dimension — every one is a "
            "date, a measurement or a unique identifier, so there is nothing to slice by.",
            warnings=[f"{s['column']}: {s['reason']}" for s in skipped[:10]],
        )

    config = SearchConfig(
        max_depth=max(1, min(max_depth, 4)),
        beam_width=max(2, min(beam_width, 256)),
        node_budget=max(500, min(node_budget, 500_000)),
        min_rows=max(1, min_rows),
        min_explanatory_power=max(0.0, min(min_explanatory_power, 1.0)),
        min_signal_to_noise=max(0.0, min(min_signal_to_noise, 10.0)),
        top_k=max(1, min(top_k, 20)),
    )
    search = RootCauseSearch(
        df, measure_obj, detected, windows, dt, config=config, time_column=time_col,
    )
    search.stats.dimensions_skipped = skipped
    explanations: list[Explanation] = search.run()

    result = RootCauseResult(
        available=True,
        measure=measure_obj.key,
        measure_label=measure_obj.label,
        measure_unit=measure_obj.unit,
        measure_basis=measure_obj.basis,
        measure_additive=measure_obj.additive,
        current=Window(
            label=windows.current.label,
            start=windows.current.start.date().isoformat(),
            end=windows.current.end.date().isoformat(),
            value=search.total_current, rows=search.total_current_rows,
        ),
        prior=Window(
            label=windows.prior.label,
            start=windows.prior.start.date().isoformat(),
            end=windows.prior.end.date().isoformat(),
            value=search.total_prior, rows=search.total_prior_rows,
        ),
        total_delta=search.total_delta,
        total_change_pct=(
            search.total_delta / search.total_prior * 100 if search.total_prior else None
        ),
        explanations=explanations,
        per_dimension=search.per_dimension_summary(),
        stats=search.stats,
        dimensions=[d.as_dict() for d in detected],
        window_basis=windows.basis,
    )

    # Bridge decomposition, only for the explanations a reader will actually
    # look at — it costs a pass over the slice's rows each.
    order_col = schema.get("order_column")
    period = search._period  # noqa: SLF001 - same package, one owner
    for explanation in explanations[:3]:
        node = search._evaluated.get(explanation.slice.key())  # noqa: SLF001
        if node is not None:
            explanation.bridge = _slice_bridge(df, node.rows, measure_obj, order_col, period)

    if explanations:
        result.drill_path = search.drill_path(explanations[0])

    # ── Caveats that change how the result should be read ──────────────────
    if windows.caveat:
        result.warnings.append(windows.caveat)
    if include_weekday:
        imbalance = _weekday_imbalance(dt, cur_mask, pri_mask)
        if imbalance:
            result.warnings.append(
                "Weekday is enabled, and the two periods do NOT contain the same number of "
                f"each weekday ({imbalance}). A weekday can therefore appear to explain a "
                "change that is only the calendar — read any weekday finding here as a "
                "hypothesis, not a cause."
            )
    if not measure_obj.additive:
        result.warnings.append(
            f"{measure_obj.label} is a distinct count, so slice values do not add up to the "
            "total — one order can appear under several products. Each slice's share of the "
            "change is still exact; the shares simply do not sum to 100%."
        )
    if search.total_delta == 0:
        result.warnings.append(
            f"{measure_obj.label} did not change between the two periods, so there is no "
            "movement to attribute. Slices may still have moved in opposite directions."
        )
    if search.stats.budget_exhausted:
        result.warnings.append(
            f"The search hit its {config.node_budget:,}-slice budget and stopped early. "
            "The explanations shown are real; a smaller or narrower dimension set would let "
            "the search finish."
        )
    if search.stats.pruned_by_beam:
        result.warnings.append(
            f"{search.stats.pruned_by_beam:,} slices were dropped by the beam "
            f"(width {config.beam_width}). Pruning by support and by the magnitude bound is "
            "exact; the beam is the one approximate step, so a very small anomaly hiding "
            "under an unremarkable parent could be missed."
        )
    robust = [e for e in explanations if e.robust]
    if explanations and not robust and search.stats.corrected_threshold:
        result.warnings.append(
            f"None of these slices survives correction for the {search.stats.slices_tested:,} "
            f"slices tested (which raises the bar to {search.stats.corrected_threshold:.1f} "
            "standard errors). They are the strongest leads in the data, not proven causes — "
            "confirm one before acting on it."
        )
    elif len(robust) < len(explanations):
        result.warnings.append(
            f"{len(robust)} of {len(explanations)} explanations survive correction for the "
            f"{search.stats.slices_tested:,} slices tested; the rest are leads, not conclusions."
        )
    if search.rejected_as_noise:
        result.warnings.append(
            f"{search.rejected_as_noise:,} slice(s) moved enough to qualify but not by more "
            f"than their own sampling noise (under {config.min_signal_to_noise:g} standard "
            "errors), so they are not reported as causes."
        )
    if not explanations and search.total_delta != 0:
        result.warnings.append(
            f"The change is spread across the business rather than concentrated: no slice "
            f"accounts for as much as {config.min_explanatory_power * 100:.0f}% of it while "
            "clearly exceeding its own noise. That is itself the finding — this is a broad "
            "move, not a segment failure."
        )
    for warning in (schema.get("warnings") or [])[:2]:
        result.warnings.append(f"Schema: {warning}")

    # ── Figures, then words ────────────────────────────────────────────────
    registry = build_registry(result)
    result.figures = [row for row in registry.audit_rows() if row["unit"] != "text"]
    if with_narrative and explanations:
        try:
            result.narrative, result.verification = narrate(
                result, registry,
                model=model or DEFAULT_INSIGHTS_MODEL,
                language=language, business_context=business_context,
            )
        except Exception:  # pragma: no cover - narration must never break the result
            logger.exception("Root-cause narration failed; returning the arithmetic only")
            result.verification = {"status": "unverified", "narration_failed": True}
    return result


def describe_options(df: pd.DataFrame | None, language: str = "en") -> dict:
    """What this dataset supports — for the UI's controls, computed once.

    Offering a measure or a dimension the data cannot support produces an error
    the user cannot act on; the picker is built from the data instead.
    """
    if df is None or df.empty:
        return {"available": False, "reason": "No data.", "measures": [], "dimensions": []}
    schema = build_schema_summary(df)
    time_col = schema.get("time_column")
    detected, skipped = detect_dimensions(df, schema=schema, time_column=time_col)
    windows_available: list[dict[str, str]] = []
    if time_col and time_col in df.columns:
        dt = pd.to_datetime(df[time_col], errors="coerce")
        for mode in ("auto", "last_month", "rolling_30d"):
            label = _window_mode_label(language, mode)
            try:
                pair = resolve_windows(dt, mode=mode, language=language)
            except MeasureUnavailableError:
                continue
            windows_available.append({
                "mode": mode, "label": label,
                "current": pair.current.label, "prior": pair.prior.label,
                "current_start": pair.current.start.date().isoformat(),
                "current_end": pair.current.end.date().isoformat(),
                "prior_start": pair.prior.start.date().isoformat(),
                "prior_end": pair.prior.end.date().isoformat(),
            })
        windows_available.append({
            "mode": "custom", "label": _window_mode_label(language, "custom"),
            "current": "", "prior": "",
        })
    return {
        "available": bool(time_col) and bool(detected),
        "reason": (
            "" if time_col and detected else
            "This project needs a date column and at least one categorical column before a "
            "drill-down can run."
        ),
        "time_column": time_col,
        "measures": available_measures(df, schema, language),
        "dimensions": [d.as_dict() for d in detected],
        "dimensions_skipped": skipped,
        "windows": windows_available,
    }
