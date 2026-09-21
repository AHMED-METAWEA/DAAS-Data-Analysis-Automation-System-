"""
Evidence ranking — decide *what matters* before the model decides how to say it.

A model handed 120 equally-presented figures writes a report where the weekday
pattern gets the same weight as a 23% revenue collapse.  Prioritisation is a
business judgement, and it is deterministic: it follows from the size of the
money involved and from whether a number is a threat, a leak, or a strength.

So the ranking happens here, in code, with an explicit money-at-stake attached
to each item.  The model receives an already-ordered brief and its job narrows
to writing it well — which is the only part of the job it is actually good at.

Every bullet is written with ``{{citation}}`` tokens, so the evidence the model
copies from is already fabrication-proof.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .figures import TOKEN_RE, FigureRegistry

# Some bullets open with an instruction aimed at the model rather than at a
# reader ("USE THIS COMPARISON WINDOW THROUGHOUT THE REPORT (May 2025 against
# April 2025)."). It belongs in the prompt — it is what stops the report mixing
# two comparison windows — but it must never reach a human, and this evidence is
# now read by three of them: the Insights audit panel, the alert inbox, and a
# WhatsApp briefing. Stripped at the seam rather than removed from the prompt.
_PROMPT_DIRECTIVE_RE = re.compile(r"^[A-Z][A-Z ]{9,}[^.]*\.\s*")

# Thresholds that turn a number into a *finding*. They are named constants
# rather than inline magic so the editorial judgement is visible and arguable.
_MATERIAL_DECLINE_PCT = 5.0        # a period move worth leading the report with
_CONCENTRATION_ALERT_PCT = 25.0    # one product/customer carrying this much is a risk
_DISCOUNT_ALERT_PCT = 3.0          # discount above this share of gross is a policy issue
_WEAK_REPEAT_PCT = 30.0            # below this, the business is acquisition-dependent
_STRONG_REPEAT_PCT = 60.0
_WEEKDAY_SPREAD_PCT = 15.0         # weekday effect big enough to schedule against


@dataclass
class EvidenceItem:
    """One ranked, pre-written finding the report should be built around."""

    tag: str              # THREAT | LEAK | RISK | OPPORTUNITY | STRENGTH | CAVEAT
    text: str             # already contains {{citation}} tokens
    money_at_stake: float  # absolute magnitude, used only for ordering
    section: str          # which report section it belongs to
    # Stable identifier for *which finding this is*, independent of its wording
    # or its numbers. Two consumers need it: the alert fingerprint (so the same
    # standing leak is recognised tomorrow at a different value), and the Arabic
    # briefing, which substitutes an Arabic template carrying the same citation
    # tokens rather than translating the English prose after the fact.
    key: str = ""
    # True when money_at_stake is money moving or leaking, False when it is the
    # *size of an exposure* (concentration risk). Summing the two would produce
    # a headline total that means nothing — see monitoring/rules.py.
    is_event: bool = True

    def render(self) -> str:
        """For the prompt — instructions to the model included."""
        return f"[{self.tag}] {self.text}"

    def reader_text(self) -> str:
        """For a person — the finding, without the prompt engineering."""
        return _PROMPT_DIRECTIVE_RE.sub("", self.text or "").strip()


def _has(reg: FigureRegistry, *keys: str) -> bool:
    return all(k in reg for k in keys)


def _val(reg: FigureRegistry, key: str) -> float | None:
    fig = reg.get(key)
    return None if fig is None else fig.value


def build_evidence(
    payload: dict,
    decision: dict,
    reg: FigureRegistry,
) -> list[EvidenceItem]:
    """Rank the findings by money at stake and return them in report order."""
    items: list[EvidenceItem] = []
    sections = (decision or {}).get("sections", {}) or {}
    kpi = payload.get("kpi", {}) or {}
    revenue = float(kpi.get("revenue") or 0.0)

    def add(
        tag: str, text: str, money: float, section: str,
        key: str = "", *, is_event: bool = True,
    ) -> None:
        items.append(EvidenceItem(
            tag=tag, text=text, money_at_stake=abs(money or 0.0), section=section,
            key=key, is_event=is_event,
        ))

    # ── 1. The direction of travel, decomposed ─────────────────────────────
    # Prefer the calendar-month comparison: it is the one an owner already has
    # a mental baseline for. Fall back to the rolling window.
    # Whether the headline period actually moved. Product-level declines mean
    # something different depending on it: inside a falling period they are the
    # cause, inside a flat one they are ordinary mix churn — and calling routine
    # churn a THREAT next to "revenue is stable" reads as a contradiction.
    headline_moved = False
    comparison = sections.get("comparison", {}) or {}
    for prefix, cmp_key in (("mon", "last_month"), ("p30", "rolling_30d")):
        if not _has(reg, f"{prefix}_revenue_change", f"{prefix}_revenue_change_pct"):
            continue
        windows = comparison.get(cmp_key) or {}
        cur_label = (windows.get("current") or {}).get("label", "the current period")
        prior_label = (windows.get("prior") or {}).get("label", "the previous period")
        change = _val(reg, f"{prefix}_revenue_change") or 0.0
        pct = _val(reg, f"{prefix}_revenue_change_pct") or 0.0
        volume = _val(reg, f"{prefix}_volume_effect")
        basket = _val(reg, f"{prefix}_basket_effect")
        if abs(pct) < _MATERIAL_DECLINE_PCT:
            add(
                "STRENGTH",
                f"USE THIS COMPARISON WINDOW THROUGHOUT THE REPORT ({cur_label} against "
                f"{prior_label}). Revenue is stable period-on-period: it changed by "
                f"{{{{{prefix}_revenue_change}}}} ({{{{{prefix}_revenue_change_pct}}}}), which "
                "is not a material swing. Lead with structural issues, not with this "
                "comparison.",
                abs(change), "what_changed", "period_stable",
            )
            break

        headline_moved = True
        direction = "fell" if change < 0 else "grew"
        # Point at the sign-free magnitude token, so the brief the model copies
        # from already models the sentence it should write.
        magnitude = f"{prefix}_revenue_{'decline' if change < 0 else 'increase'}"
        magnitude_pct = f"{magnitude}_pct"
        driver = ""
        if volume is not None and basket is not None:
            if abs(volume) >= abs(basket):
                driver = (
                    f"Order COUNT is the dominant cause: {{{{{prefix}_volume_effect}}}} of the "
                    f"move came from the change in orders ({{{{{prefix}_orders_change}}}} orders) "
                    f"versus {{{{{prefix}_basket_effect}}}} from order size. This is a demand / "
                    "traffic problem, not a pricing problem — the fix is getting orders back, "
                    "not raising prices."
                )
            else:
                driver = (
                    f"Order SIZE is the dominant cause: {{{{{prefix}_basket_effect}}}} of the move "
                    f"came from average order value ({{{{{prefix}_aov_change}}}} per order) versus "
                    f"{{{{{prefix}_volume_effect}}}} from order count. Customers are still coming; "
                    "they are spending less per visit — look at basket composition, discounting "
                    "and mix, not at acquisition."
                )
        customers = (
            f" Distinct buyers changed by {{{{{prefix}_customers_change}}}}."
            if _has(reg, f"{prefix}_customers_change") else ""
        )
        # Name the figure that sizes recovery, so a recommendation has the right
        # token to quote instead of reaching for the period total.
        worth = (
            " Reversing this is worth {{scn_decline_recovery}}."
            if change < 0 and "scn_decline_recovery" in reg else ""
        )
        window = f"{cur_label} against {prior_label}"
        add(
            "THREAT" if change < 0 else "OPPORTUNITY",
            f"USE THIS COMPARISON WINDOW THROUGHOUT THE REPORT ({window}). Revenue "
            f"{direction} by {{{{{magnitude}}}}} ({{{{{magnitude_pct}}}}}) in {cur_label}, "
            f"from {{{{{prefix}_prior_revenue}}}} to {{{{{prefix}_revenue}}}}. "
            f"{driver}{customers}{worth}",
            abs(change), "what_changed", "period_move",
        )
        break

    # ── 2. Which products caused it ────────────────────────────────────────
    mom = sections.get("momentum", {}) or {}
    if mom.get("available"):
        fallers = mom.get("fallers", [])[:3]
        if fallers and _has(reg, "faller1_loss"):
            names = ", ".join(
                f"{{{{faller{i}_name}}}} (down {{{{faller{i}_loss}}}}, "
                f"{{{{faller{i}_change_pct}}}})"
                for i in range(1, min(len(fallers), 3) + 1)
                if _has(reg, f"faller{i}_loss")
            )
            total_lost = sum(abs(f.get("change") or 0) for f in fallers)
            if headline_moved:
                add(
                    "THREAT",
                    f"The decline is concentrated in specific products, not spread across the "
                    f"range: {names}. Naming these is what turns 'sales are down' into "
                    "something a person can act on this week.",
                    total_lost, "what_changed", "fallers_concentrated",
                )
            else:
                add(
                    "RISK",
                    f"Total revenue held steady, but that steadiness hides movement underneath: "
                    f"{names} lost ground and were offset elsewhere. Worth watching rather than "
                    "reacting to — but if the offset stops, the total starts falling.",
                    total_lost, "what_changed", "fallers_offset",
                )
        risers = mom.get("risers", [])[:2]
        if risers and _has(reg, "riser1_gain"):
            names = ", ".join(
                f"{{{{riser{i}_name}}}} (up {{{{riser{i}_gain}}}}, {{{{riser{i}_change_pct}}}})"
                for i in range(1, min(len(risers), 2) + 1)
                if _has(reg, f"riser{i}_gain")
            )
            add(
                "STRENGTH",
                f"Against the trend, these grew: {names}. Worth understanding what is working "
                "there before it gets buried under the bad news.",
                sum(abs(r.get("change") or 0) for r in risers), "working", "risers",
            )

    # ── 3. Concentration exposure ──────────────────────────────────────────
    top_share = _val(reg, "top_product_share")
    if top_share is not None and top_share >= _CONCENTRATION_ALERT_PCT:
        add(
            "RISK",
            f"{{{{top_product_name}}}} alone is {{{{top_product_share}}}} of all revenue "
            f"({{{{top_product_revenue}}}}), and {{{{products_for_80pct}}}} of "
            f"{{{{products_total}}}} products make {{{{pareto_threshold}}}} of it. A supply "
            "problem, a price rise or a competitor on that one line hits the whole business "
            "at once — this is the largest single-point risk in the data.",
            _val(reg, "top_product_revenue") or 0.0, "concentration",
            "product_concentration", is_event=False,
        )
    elif _has(reg, "products_for_80pct", "products_total"):
        add(
            "RISK",
            f"{{{{products_for_80pct}}}} of {{{{products_total}}}} products generate "
            f"{{{{pareto_threshold}}}} of revenue ({{{{products_for_80pct_share}}}} of the "
            "range). Everything below that line is consuming attention and working capital "
            "for {{bottom_half_products_share}} of revenue.",
            revenue * 0.2, "concentration", "product_pareto", is_event=False,
        )

    decile_share = _val(reg, "top_decile_customer_share")
    if decile_share is not None and decile_share >= 40:
        add(
            "RISK",
            f"The top {{{{top_decile_pct}}}} of customers ({{{{top_decile_customers}}}} "
            f"accounts) bring {{{{top_decile_customer_share}}}} of revenue, while the "
            f"lower half brings "
            f"{{{{bottom_half_customer_share}}}}. Losing a handful of accounts is a "
            "material revenue event — they deserve named ownership, not a mailing list.",
            revenue * (decile_share / 100), "concentration",
            "customer_concentration", is_event=False,
        )

    # ── 4. Margin leakage ──────────────────────────────────────────────────
    margin = sections.get("margin", {}) or {}
    if margin.get("available"):
        if _has(reg, "gross_margin_pct", "gross_profit"):
            add(
                "STRENGTH" if (_val(reg, "gross_margin_pct") or 0) >= 30 else "THREAT",
                f"Gross margin is {{{{gross_margin_pct}}}} — {{{{gross_profit}}}} of profit on "
                f"{{{{revenue_total}}}} of revenue. Every revenue figure in this report should "
                "be read against that: revenue is not money kept.",
                _val(reg, "gross_profit") or 0.0, "standing", "gross_margin", is_event=False,
            )
        if _has(reg, "margin_drag_revenue", "scn_margin_normalisation_upside"):
            add(
                "LEAK",
                f"{{{{margin_drag_revenue}}}} of revenue ({{{{margin_drag_share}}}}) sits in "
                f"products earning below the portfolio margin — led by {{{{lowmargin1_name}}}} "
                f"at {{{{lowmargin1_margin_pct}}}} against an overall {{{{gross_margin_pct}}}}. "
                f"Bringing just those to the portfolio rate is worth "
                f"{{{{scn_margin_normalisation_upside}}}} in gross profit, with no extra sales.",
                _val(reg, "scn_margin_normalisation_upside") or 0.0, "decisions",
                "margin_drag",
            )
        if margin.get("loss_making_products"):
            add(
                "LEAK",
                f"{{{{lossmaker1_name}}}} is sold at a gross loss ({{{{lossmaker1_loss}}}}). "
                "Every unit sold makes the business worse off — this needs a price, cost or "
                "delist decision, not a marketing push.",
                abs(_val(reg, "lossmaker1_loss") or 0.0), "decisions", "loss_making_product",
            )
    else:
        add(
            "CAVEAT",
            "No verified cost data, so every figure here is revenue, not profit. "
            "The highest-revenue product may not be the most profitable one, and this report "
            "cannot tell you which is which.",
            0.0, "blind_spots", "no_cost_data", is_event=False,
        )

    # ── 5. Discount leakage ────────────────────────────────────────────────
    disc_share = _val(reg, "discount_share_of_gross")
    if disc_share is not None and disc_share >= _DISCOUNT_ALERT_PCT:
        add(
            "LEAK",
            f"{{{{discount_given}}}} was given away in discounts — {{{{discount_share_of_gross}}}} "
            f"of pre-discount value, applied to {{{{discount_lines_pct}}}} of all lines at an "
            f"average of {{{{discount_avg_pct}}}}. Discounting on half the book is not a "
            "promotion, it is the real price list.",
            _val(reg, "discount_given") or 0.0, "decisions", "discount_leak",
        )

    # ── 6. Retention economics ─────────────────────────────────────────────
    repeat_pct = _val(reg, "repeat_rate_pct")
    if repeat_pct is not None:
        if repeat_pct < _WEAK_REPEAT_PCT:
            # The sized upside needs a median order value, which needs an order
            # id column — cite it only when it exists.
            upside = (
                " Getting each of them to one more order of median size is worth "
                "{{scn_second_purchase_upside}} — cheaper than buying the same revenue in "
                "new customers."
                if "scn_second_purchase_upside" in reg else
                " Winning a second order from even part of that group is the cheapest "
                "revenue available to this business."
            )
            add(
                "OPPORTUNITY",
                f"Only {{{{repeat_rate_pct}}}} of customers ever bought twice; "
                f"{{{{one_time_customers}}}} bought once and never returned.{upside}",
                _val(reg, "scn_second_purchase_upside") or 0.0, "decisions", "weak_repeat",
            )
        elif repeat_pct >= _STRONG_REPEAT_PCT:
            # With no one-time buyers at all there is nothing to contrast against.
            contrast = (
                ", at {{revenue_per_repeat_customer}} each versus "
                "{{revenue_per_one_time_customer}} for one-timers"
                if _has(reg, "revenue_per_repeat_customer", "revenue_per_one_time_customer")
                else ""
            )
            add(
                "STRENGTH",
                f"{{{{repeat_rate_pct}}}} of customers come back and repeat buyers generate "
                f"{{{{repeat_revenue_share}}}} of revenue{contrast}. The business runs on its "
                "existing base — protecting it outranks acquisition.",
                _val(reg, "repeat_revenue") or revenue * 0.5, "working", "strong_repeat",
                is_event=False,
            )

    # ── 7. Weekly rhythm ───────────────────────────────────────────────────
    uplift = _val(reg, "best_weekday_uplift_pct")
    if uplift is not None and abs(uplift) >= _WEEKDAY_SPREAD_PCT:
        worth = (
            " Lifting the weakest day to an ordinary day is worth "
            "{{scn_weak_weekday_uplift}} across the period."
            if "scn_weak_weekday_uplift" in reg else ""
        )
        add(
            "OPPORTUNITY",
            f"{{{{best_weekday}}}} averages {{{{best_weekday_avg}}}} against "
            f"{{{{avg_daily_revenue}}}} on a typical day ({{{{best_weekday_uplift_pct}}}} "
            f"better), while {{{{worst_weekday}}}} runs {{{{worst_weekday_gap_pct}}}} below."
            f"{worth} That is a staffing, stock and promotion-timing decision, not a "
            "curiosity.",
            _val(reg, "scn_weak_weekday_uplift") or 0.0, "decisions", "weekday_rhythm",
        )

    # ── 8. Data-quality caveats that change how figures should be read ─────
    for check in payload.get("quality", []) or []:
        if (
            check.get("check") == "missing_values"
            and check.get("total_missing")
            and check.get("severity") != "low"
            and "missing_values_pct" in reg
        ):
            add("CAVEAT",
                "{{missing_values_pct}} of all cells are empty — metrics built on the affected "
                "columns are computed on fewer rows than the headline row count suggests.",
                0.0, "blind_spots", "missing_values", is_event=False)
        if (
            check.get("check") == "duplicate_rows"
            and check.get("count")
            and check.get("severity") != "low"
            and "duplicate_rows" in reg
        ):
            add("CAVEAT",
                "{{duplicate_rows}} fully duplicated rows ({{duplicate_rows_pct}}) are included "
                "in every total — revenue and order counts are inflated by that amount unless "
                "they are genuine repeat transactions.",
                0.0, "blind_spots", "duplicate_rows", is_event=False)

    for warning in (kpi.get("schema_warnings") or [])[:3]:
        add("CAVEAT", f"Column detection caution: {warning}", 0.0, "blind_spots",
            "schema_warning", is_event=False)

    # Safety net: a bullet citing a figure that was never registered would hand
    # the model a token rendering as "[figure unavailable: …]" in the published
    # report. Each branch above guards its optional citations, so this should
    # never fire — it exists so that a future edit which forgets to cannot ship
    # a broken citation.
    items = [it for it in items if all(m.group(1) in reg for m in TOKEN_RE.finditer(it.text))]

    # Threats and leaks first, then the rest by money at stake — so the report's
    # order of attention matches the P&L's.
    priority = {"THREAT": 0, "LEAK": 1, "RISK": 2, "OPPORTUNITY": 3, "STRENGTH": 4, "CAVEAT": 5}
    items.sort(key=lambda it: (priority.get(it.tag, 9), -it.money_at_stake))
    return items


def render_evidence(items: list[EvidenceItem]) -> str:
    """Format the ranked brief for the prompt, numbered so order is explicit."""
    return "\n".join(f"{i}. {item.render()}" for i, item in enumerate(items, start=1))


def evidence_payload(items: list[EvidenceItem]) -> list[dict[str, Any]]:
    """Structured form for the API, so the UI can show the ranking that drove
    the report rather than only the prose that came out of it."""
    return [
        {"rank": i, "tag": it.tag, "section": it.section,
         "money_at_stake": round(it.money_at_stake, 2), "text": it.reader_text()}
        for i, it in enumerate(items, start=1)
    ]
