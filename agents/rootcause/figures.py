"""Citable figures for a root-cause result.

The drill-down inherits the insights pipeline's central rule: the model that
writes the explanation is never allowed to type a digit.  Every number it may
state is registered here with its exact value, the formula behind it and a
stable key, and the model writes ``{{exp1_loss}}`` where a figure belongs.  The
substitution happens server-side after generation, so a wrong number in a
root-cause narrative is not *unlikely* — it is unreachable.

Slice labels are registered too (``{{exp1_label}}``).  A drill-down's whole
payload is entity names, and a model asked to retype "Office Bulk Subscription"
three times will eventually produce "Office Bulk Subscriptions".
"""

from __future__ import annotations

from agents.insights.figures import FigureRegistry
from agents.rootcause.schema import RootCauseResult

# How many explanations get a full figure block. Beyond this the reader is no
# longer drilling down, they are reading a table — and every extra near-identical
# figure is another chance for the model to cite the wrong one.
MAX_CITED_EXPLANATIONS = 3


def build_registry(result: RootCauseResult) -> FigureRegistry:
    """Register every figure a root-cause narrative may cite."""
    reg = FigureRegistry()
    if not result.available or result.current is None or result.prior is None:
        return reg

    unit = result.measure_unit
    cur, pri = result.current, result.prior
    span = f"{cur.label} vs {pri.label}"

    # ── 1. The change being explained ──────────────────────────────────────
    reg.add_text("rc_measure", "The metric being explained", result.measure_label,
                 result.measure_basis)
    reg.add_text("rc_current_window", "The period being explained", cur.label,
                 f"rows dated {cur.start} → {cur.end}")
    reg.add_text("rc_prior_window", "The baseline it is compared against", pri.label,
                 f"rows dated {pri.start} → {pri.end}")
    reg.add("rc_prior_value", f"{result.measure_label} in {pri.label}", pri.value, unit,
            result.measure_basis, period=pri.label)
    reg.add("rc_current_value", f"{result.measure_label} in {cur.label}", cur.value, unit,
            result.measure_basis, period=cur.label)
    reg.add("rc_total_change", f"Total change in {result.measure_label}, {span} "
            "(carries its own +/− sign)", result.total_delta, unit,
            f"{result.measure_label} in {cur.label} − in {pri.label}", period=span)
    # Sign-free companion, for "fell by {{rc_total_decline}}". Citing the signed
    # figure there renders "fell by −6,656.10", which states the opposite.
    direction = "decline" if result.total_delta < 0 else "increase"
    reg.add(f"rc_total_{direction}",
            f"Size of the total {direction}, {span} (no sign — use after 'fell by' / 'grew by')",
            abs(result.total_delta), unit,
            f"absolute difference in {result.measure_label} between the two periods", period=span)
    if result.total_change_pct is not None:
        reg.add("rc_total_change_pct", f"Total change in %, {span} (carries its own sign)",
                result.total_change_pct, "percent",
                f"total change ÷ {result.measure_label} in {pri.label} × 100", period=span)
        reg.add(f"rc_total_{direction}_pct", f"Size of the total {direction} in %, {span} (no sign)",
                abs(result.total_change_pct), "percent",
                f"absolute change ÷ {result.measure_label} in {pri.label} × 100", period=span)

    # ── 2. Each explanation ────────────────────────────────────────────────
    for i, exp in enumerate(result.explanations[:MAX_CITED_EXPLANATIONS], start=1):
        label = exp.slice.render_compact()
        expression = exp.slice.render()
        offered = i <= 2  # ranks 3+ stay auditable but are kept out of the prompt
        reg.add_text(f"exp{i}_label", f"#{i} explaining slice", label, expression)
        reg.add_text(f"exp{i}_where", f"#{i} slice as a filter expression", expression,
                     "the conditions that define the slice")
        reg.add(f"exp{i}_prior", f"{result.measure_label} of '{label}' in {pri.label}",
                exp.prior_value, unit, f"{result.measure_basis}, restricted to {expression}",
                period=pri.label, prompt=offered)
        reg.add(f"exp{i}_current", f"{result.measure_label} of '{label}' in {cur.label}",
                exp.current_value, unit, f"{result.measure_basis}, restricted to {expression}",
                period=cur.label, prompt=offered)
        reg.add(f"exp{i}_change", f"Change in '{label}', {span} (carries its own sign)",
                exp.delta, unit, f"its {cur.label} value − its {pri.label} value",
                period=span, prompt=False)
        exp_dir = "loss" if exp.delta < 0 else "gain"
        reg.add(f"exp{i}_{exp_dir}", f"Size of the {exp_dir} in '{label}', {span} (no sign)",
                abs(exp.delta), unit,
                f"absolute change in {result.measure_label} for {expression}",
                period=span, prompt=offered)
        if exp.change_pct is not None:
            reg.add(f"exp{i}_change_pct", f"'{label}' change in % (carries its own sign)",
                    exp.change_pct, "percent", "its change ÷ its prior value × 100",
                    period=span, prompt=offered)
        reg.add(f"exp{i}_share_of_change", f"Share of the TOTAL change explained by '{label}'",
                abs(exp.explanatory_power) * 100, "percent",
                "its change ÷ the total change × 100", period=span, prompt=offered)
        reg.add(f"exp{i}_rows_share", f"Share of all transaction lines that '{label}' represents",
                exp.rows_share * 100, "percent",
                "its rows across both periods ÷ all rows across both periods × 100",
                period=span, prompt=offered)
        reg.add(f"exp{i}_concentration",
                f"How concentrated the change is in '{label}' (× its size)",
                exp.concentration, "ratio",
                "share of the change it explains ÷ share of the rows it represents",
                period=span, prompt=offered)
        reg.add(f"exp{i}_expected", f"What '{label}' would have been had it moved at the "
                "business-wide rate", exp.expected_current, unit,
                f"its {pri.label} value × (total {cur.label} ÷ total {pri.label})",
                period=cur.label, prompt=False)
        reg.add(f"exp{i}_excess", f"How far '{label}' missed the business-wide rate by "
                "(carries its own sign)", exp.excess, unit,
                "its actual current value − what the business-wide rate implied",
                period=span, prompt=offered)
        reg.add(f"exp{i}_rest_change", f"Change in everything EXCEPT '{label}', {span}",
                exp.rest_delta, unit,
                f"{result.measure_label} outside {expression}: {cur.label} − {pri.label}",
                period=span, prompt=offered)
        if exp.rest_change_pct is not None:
            reg.add(f"exp{i}_rest_change_pct",
                    f"Change in % of everything EXCEPT '{label}' (carries its own sign)",
                    exp.rest_change_pct, "percent",
                    "change outside the slice ÷ its prior value × 100", period=span,
                    prompt=offered)
        if exp.bridge and exp.bridge.get("available"):
            reg.add(f"exp{i}_volume_effect",
                    f"Part of '{label}'s change caused by ORDER COUNT",
                    exp.bridge.get("volume_effect"), unit,
                    "(change in its orders) × (its prior average order value)",
                    period=span, prompt=offered)
            reg.add(f"exp{i}_basket_effect",
                    f"Part of '{label}'s change caused by ORDER SIZE",
                    exp.bridge.get("basket_effect"), unit,
                    "(change in its average order value) × (its prior order count)",
                    period=span, prompt=offered)
            reg.add(f"exp{i}_orders_change", f"Change in '{label}'s order count",
                    exp.bridge.get("orders_change"), "count",
                    f"its distinct orders in {cur.label} − in {pri.label}",
                    period=span, prompt=offered)

    # ── 3. What the search itself did ──────────────────────────────────────
    stats = result.stats
    reg.add("rc_dimensions_searched", "Dimensions the search looked at",
            len(stats.dimensions_searched), "count",
            "columns qualifying as business dimensions", prompt=False)
    reg.add("rc_nodes_evaluated", "Slices the search actually evaluated",
            stats.nodes_evaluated, "count", "nodes visited in the dimension lattice",
            prompt=False)
    reg.add("rc_combinations_possible", "Slices an exhaustive search would have visited",
            stats.exhaustive_combinations, "count",
            "product of dimension sizes over every combination up to the depth limit",
            prompt=False)
    return reg
