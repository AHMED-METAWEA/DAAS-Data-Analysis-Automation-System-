"""
Deterministic assembly of a forecast narrative.

The forecasting pipeline returns structured, per-metric output (selected model,
back-test error, horizon values, and an LLM business interpretation).  This
module stitches those pieces into a single consulting-style markdown report so
the forecast can be grounded, presented and exported like every other Smart
Analyst report — without an extra LLM call.
"""

from __future__ import annotations

from typing import Any


def _fmt(value: Any, spec: str = ",.0f") -> str:
    try:
        return format(float(value), spec)
    except (TypeError, ValueError):
        return "—"


def _metric_title(metric: str) -> str:
    return (metric or "Metric").replace("_", " ").title()


# Reliability is stated before any number is shown, not buried in a footnote.
# A reader who stops after the first line of a section should still know
# whether the figures underneath are safe to plan against.
_RELIABILITY_BADGE = {
    "reliable": "✅ **Reliable**",
    "indicative": "⚠️ **Indicative only**",
    "unreliable": "🛑 **Not reliable**",
}


def _horizon_label(out: dict) -> str:
    """"total" vs "average" — an additive metric and a rate mean different things."""
    return "total" if out.get("aggregation") == "sum" else "average"


def _horizon_text(out: dict) -> str:
    """"30_days" -> "30 days" for prose."""
    raw = str(out.get("forecast_horizon") or "the forecast horizon")
    return raw.replace("_", " ")


def _reliability_block(out: dict) -> list[str]:
    level = out.get("reliability", "unknown")
    badge = _RELIABILITY_BADGE.get(level)
    if not badge:
        return []
    lines = [f"{badge} — {out.get('reliability_headline', '')}".rstrip(" —")]
    reasons = out.get("reliability_reasons") or []
    if level != "reliable" and reasons:
        lines.extend(f"  - {r}" for r in reasons)
    rec = out.get("recommended_granularity")
    if rec:
        lines.append(f"  - **Suggestion:** forecast at **{rec}** granularity instead. "
                     f"{out.get('granularity_reason', '')}".rstrip())
    lines.append("")
    return lines


def build_forecast_report_md(results: dict) -> str:
    """Build a markdown forecast report from a pipeline result dict."""
    outputs = results.get("forecast_outputs", []) or []
    if not outputs:
        return ""

    lines: list[str] = ["# Sales & Demand Forecast", ""]

    # ── Executive summary ──────────────────────────────────────────────
    lines.append("## Executive Summary")
    for out in outputs:
        name = _metric_title(out.get("metric", ""))
        chg = out.get("change_percent")
        horizon = _horizon_text(out)
        model = out.get("selected_model", "n/a")
        level = out.get("reliability", "unknown")
        flag = "" if level == "reliable" else f" {_RELIABILITY_BADGE.get(level, '')}"

        total = out.get("horizon_total")
        if total is not None:
            lo, hi = out.get("horizon_total_lower"), out.get("horizon_total_upper")
            band = f" (range {_fmt(lo)}–{_fmt(hi)})" if lo is not None and hi is not None else ""
            chg_txt = f", {chg:+.1f}% vs the previous {horizon}" if chg is not None else ""
            lines.append(
                f"- **{name}** — projected {_horizon_label(out)} over {horizon}: "
                f"**{_fmt(total)}**{band}{chg_txt}. Model: {model}.{flag}"
            )
        elif chg is not None:
            lines.append(
                f"- **{name}** is projected to move **{chg:+.1f}%** over "
                f"{horizon} (model: {model}).{flag}"
            )
        else:
            lines.append(f"- **{name}** — forecast produced with {model}.{flag}")
    lines.append("")

    # Any metric that failed its checks is called out before the detail, so the
    # caveat cannot be missed by someone who only reads the summary.
    unreliable = [
        _metric_title(o.get("metric", "")) for o in outputs
        if o.get("reliability") == "unreliable"
    ]
    if unreliable:
        lines.append(
            f"> 🛑 **{', '.join(unreliable)}** did not pass out-of-sample reliability "
            f"checks. The figures above are shown for completeness; see each section "
            f"for why they should not be planned against."
        )
        lines.append("")

    # ── Per-metric detail ──────────────────────────────────────────────
    for out in outputs:
        name = _metric_title(out.get("metric", ""))
        lines.append(f"## {name}")

        current = out.get("current_value")
        forecast = out.get("forecasted_value")
        chg = out.get("change_percent")
        conf = out.get("confidence_score")
        ev = out.get("evaluation", {}) or {}
        skill = out.get("skill_score")

        lines.extend(_reliability_block(out))

        label = _horizon_label(out)
        horizon = _horizon_text(out)
        detail = []

        total = out.get("horizon_total")
        if total is not None:
            lo, hi = out.get("horizon_total_lower"), out.get("horizon_total_upper")
            level = out.get("interval_level", 0.8)
            band = (
                f" — {level:.0%} range {_fmt(lo)} to {_fmt(hi)}"
                if lo is not None and hi is not None else ""
            )
            detail.append(f"- **Projected {label} over {horizon}:** {_fmt(total)}{band}")
        # Stated explicitly so the comparison behind change_percent is auditable:
        # it is the same aggregate over the equivalent trailing window, not the
        # last single period.
        baseline = out.get("baseline_window_value", current)
        if baseline is not None:
            chg_txt = f" ({chg:+.1f}%)" if chg is not None else ""
            detail.append(
                f"- **Same {label} over the previous {horizon}:** {_fmt(baseline)}{chg_txt}"
            )

        detail.append(f"- **Selected model:** {out.get('selected_model', 'n/a')}")
        if out.get("granularity"):
            gran_txt = out["granularity"]
            if out.get("training_periods"):
                gran_txt += f" ({out['training_periods']} periods of history)"
            detail.append(f"- **Granularity:** {gran_txt}")
        if out.get("transform") and out["transform"] != "none":
            detail.append(f"- **Fitted on scale:** {out['transform']} (variance-stabilising)")
        reason = out.get("model_selection_reason")
        if reason:
            detail.append(f"- **Why this model:** {reason}")

        bt = (
            f"- **Back-test (out-of-sample):** MASE {ev.get('mase', '—')} · "
            f"MAPE {ev.get('mape', '—')}% · MAE {ev.get('mae', '—')} · RMSE {ev.get('rmse', '—')}"
        )
        if skill is not None:
            bt += f" · skill vs best naive baseline {skill * 100:+.0f}%"
        detail.append(bt)

        # The gap between a band's label and its measured coverage is the single
        # most useful number for deciding how much to trust the range.
        cov = out.get("measured_coverage")
        if cov is not None:
            level = out.get("interval_level", 0.8)
            verdict = "well calibrated" if abs(cov - level) <= 0.1 else "approximate"
            detail.append(
                f"- **Interval calibration:** the {level:.0%} band covered {cov:.0%} of "
                f"back-test outcomes ({verdict})"
            )
        pred = out.get("predictability")
        if pred is not None:
            detail.append(
                f"- **Signal vs noise:** {pred:.0%} of the variation is structure "
                f"({out.get('signal_verdict', 'unknown')}); the rest is period-to-period noise"
            )
        if conf is not None:
            detail.append(f"- **Confidence:** {conf:.0%}")
        impact = out.get("business_impact")
        if impact:
            detail.append(f"- **Business impact:** {impact}")
        lines.extend(detail)
        lines.append("")

        horizons = out.get("horizons")
        if isinstance(horizons, dict) and horizons:
            hz = " · ".join(f"{k.replace('_', ' ')}: {_fmt(v)}" for k, v in horizons.items())
            lines.append(f"**Horizon forecasts ({label}):** {hz}")
            lines.append("")

        # ── Data quality: what we had to do to the data to model it ─────
        quality = []
        dq = out.get("data_quality") or {}
        if dq.get("filled_periods"):
            share = dq.get("fill_share", 0)
            quality.append(
                f"{dq['filled_periods']} period(s) ({share:.0%}) had no records and were "
                f"{'treated as zero' if dq.get('aggregation') == 'sum' else 'interpolated'}"
            )
        if dq.get("dropped_partial"):
            quality.append(
                f"incomplete {'/'.join(dq['dropped_partial'])} bucket(s) dropped "
                f"(a partial period is not a low period)"
            )
        anomalies = out.get("anomalies_detected") or 0
        if anomalies:
            periods = out.get("anomaly_periods") or []
            dates = ", ".join(str(p.get("date")) for p in periods[:4] if p.get("date"))
            quality.append(
                f"{anomalies} anomalous period(s) capped for model fitting"
                + (f" ({dates}{'…' if len(periods) > 4 else ''})" if dates else "")
            )
        shift = out.get("level_shift")
        if shift:
            quality.append(
                f"a sustained level shift was detected in the history "
                f"({shift.get('score')} pooled SD) — earlier data describes a different regime"
            )
        if quality:
            lines.append("**Data quality**")
            lines.extend(f"- {q}" for q in quality)
            lines.append("")

        summary = out.get("business_summary")
        if summary:
            lines.append(f"**Summary.** {summary}")
            lines.append("")

        for label, key in (("Risks", "risks"),
                           ("Opportunities", "opportunities"),
                           ("Recommended actions", "recommended_actions")):
            items = out.get(key) or []
            if items:
                lines.append(f"**{label}**")
                lines.extend(f"- {item}" for item in items)
                lines.append("")

        lines.append("---")
        lines.append("")

    # Drop the trailing separator.
    while lines and lines[-1] in ("", "---"):
        lines.pop()

    return "\n".join(lines)
