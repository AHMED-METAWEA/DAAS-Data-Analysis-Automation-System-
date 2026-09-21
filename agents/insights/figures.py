"""
Figure Registry — the mechanism that makes a fabricated number structurally
impossible rather than merely detectable.

Grounding checkers are guards: the model writes a number, and afterwards we try
to prove it came from the data.  That can only ever *catch* fabrication, and it
catches it probabilistically — a wrong figure that happens to land near some
value in the payload slips through.

This module inverts the flow.  Every number a report is allowed to contain is
computed here first, registered with

  * an exact ``value`` and the ``display`` string it must be printed as,
  * the ``formula`` and inputs it came from (shown to the reader as an audit
    trail), and
  * a stable ``key``,

and the model is then told to write **citation tokens** (``{{revenue_total}}``)
instead of digits.  :func:`render` substitutes the registered display strings
server-side after generation.  A cited number is therefore not "checked" — it
is *never produced by the model at all*, so it cannot be wrong.

Anything the model types as raw digits anyway is caught by
``strict_verify.verify_rendered_report`` and sent back for correction, so the
two mechanisms close both directions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from tools.token_budget import estimate_tokens

# ``{{key}}`` — deliberately unlike anything that occurs in business prose, so a
# stray brace in a product name can never be mistaken for a citation.
TOKEN_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")
# Same token, capturing a redundant trailing "%". Percent figures already render
# their own sign, and models reliably add a second one ("-16.2%%"). Consuming it
# at substitution time fixes it everywhere instead of relying on the prompt.
_TOKEN_PCT_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}(\s*%)?")
# Models drop a brace ("{{mon_basket_effect}"). The intent is unambiguous, and
# the alternative is publishing literal braces in a business report, so these are
# normalised to well-formed tokens before substitution.
_MALFORMED_TOKEN_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}(?!\})|\{(?<!\{\{)\s*([a-zA-Z0-9_]+)\s*\}\}")
# Nothing brace-wrapped may survive into a published report. Anything left after
# substitution is a defect, never cosmetic.
LEFTOVER_MARKUP_RE = re.compile(r"\{\{?[a-zA-Z0-9_ ]*\}?\}")


def normalise_tokens(text: str) -> str:
    """Repair single-brace citation typos into well-formed ``{{key}}`` tokens."""
    return _MALFORMED_TOKEN_RE.sub(
        lambda m: "{{" + (m.group(1) or m.group(2)) + "}}", text or "",
    )

# Quality of a figure, surfaced to the reader:
#   exact    — measured directly from the rows
#   scenario — exact arithmetic under an explicitly stated assumption
QUALITY_EXACT = "exact"
QUALITY_SCENARIO = "scenario"


@dataclass(frozen=True)
class Figure:
    """One number a report is permitted to state, with its provenance."""

    key: str
    label: str
    value: float
    unit: str  # currency | percent | count | days | date | ratio
    display: str
    formula: str
    period: str = ""
    quality: str = QUALITY_EXACT
    assumption: str = ""
    # Offered to the model as a citable token. Figures with ``prompt=False`` are
    # still registered — they stay in the audit trail and still satisfy the
    # verifier if the model types their value — but they are kept out of the
    # prompt. Two reasons: the token list is the single biggest consumer of the
    # context budget, and every extra near-duplicate figure is another chance to
    # cite the wrong one.
    prompt: bool = True

    def as_row(self) -> dict[str, Any]:
        """Audit-trail row for the API / UI / export."""
        return {
            "key": self.key,
            "label": self.label,
            "value": self.value,
            "unit": self.unit,
            "display": self.display,
            "formula": self.formula,
            "period": self.period,
            "quality": self.quality,
            "assumption": self.assumption,
        }


def _fmt(value: float, unit: str) -> str:
    """Render a value the single way it is allowed to appear in the report.

    One value, one string: the same figure cannot show up as "43.8%" in the
    summary and "44%" three sections later, which is exactly how a reader loses
    confidence in a report that is in fact correct.
    """
    if unit == "percent":
        return f"{value:,.1f}%"
    if unit == "count":
        return f"{int(round(value)):,}"
    if unit == "days":
        return f"{int(round(value)):,}"
    if unit == "ratio":
        return f"{value:,.2f}"
    # currency and anything else: 2 decimals, thousands separated, sign kept
    return f"{value:,.2f}"


class FigureRegistry:
    """Ordered collection of every figure a given report may cite."""

    def __init__(self) -> None:
        self._figures: dict[str, Figure] = {}

    def add(
        self,
        key: str,
        label: str,
        value: object,
        unit: str,
        formula: str,
        *,
        period: str = "",
        quality: str = QUALITY_EXACT,
        assumption: str = "",
        display: str | None = None,
        prompt: bool = True,
    ) -> str | None:
        """Register a figure and return its citation token (``{{key}}``).

        ``None`` is returned — and nothing registered — when the value is not a
        real number, so a caller can write ``tok = reg.add(...)`` and simply
        skip the sentence when the underlying metric was unavailable.
        """
        if value is None or isinstance(value, bool):
            return None
        try:
            num = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        if num != num or num in (float("inf"), float("-inf")):  # NaN / inf
            return None
        self._figures[key] = Figure(
            key=key,
            label=label,
            value=num,
            unit=unit,
            display=display if display is not None else _fmt(num, unit),
            formula=formula,
            period=period,
            quality=quality,
            assumption=assumption,
            prompt=prompt,
        )
        return f"{{{{{key}}}}}"

    def add_text(self, key: str, label: str, text: str, formula: str, *, period: str = "") -> str:
        """Register a non-numeric citation (a date, a product name).

        Registering these too means the model never has to retype an entity
        name or a date either — the two other things it silently gets wrong.
        """
        self._figures[key] = Figure(
            key=key, label=label, value=float("nan"), unit="text",
            display=str(text), formula=formula, period=period,
        )
        return f"{{{{{key}}}}}"

    # ── Access ─────────────────────────────────────────────────────────────
    def __contains__(self, key: str) -> bool:
        return key in self._figures

    def __len__(self) -> int:
        return len(self._figures)

    def get(self, key: str) -> Figure | None:
        return self._figures.get(key)

    def all(self) -> list[Figure]:
        return list(self._figures.values())

    def numeric(self) -> list[Figure]:
        return [f for f in self._figures.values() if f.unit != "text"]

    def audit_rows(self) -> list[dict[str, Any]]:
        return [f.as_row() for f in self._figures.values()]

    # ── Prompt surface ─────────────────────────────────────────────────────
    def prompt_table(self, keys: set[str] | None = None) -> str:
        """The allow-list handed to the model: token → what it means → value.

        This *replaces* dumping the analytics JSON into the prompt.  The model
        can no longer pick an arbitrary number out of a nested payload, because
        the only numbers it can see are the ones a human curated as meaningful.

        Pass *keys* to emit a subset — the correction pass only needs the
        figures that could fix what was flagged, and a shorter list there both
        fits the token budget and removes distractors.
        """
        figures = [
            f for f in self._figures.values()
            if f.prompt and (keys is None or f.key in keys)
        ]
        # The full-period suffix repeats on most rows; hoisting it into one
        # header line reclaims a meaningful slice of the context budget without
        # losing the information.
        counts: dict[str, int] = {}
        for f in figures:
            if f.period:
                counts[f.period] = counts.get(f.period, 0) + 1
        common = max(counts, key=lambda p: counts[p]) if counts else ""

        lines: list[str] = []
        if common:
            lines.append(f"(Unless a line says otherwise, figures cover {common}.)")
        for f in figures:
            suffix = f" [{f.quality}: {f.assumption}]" if f.quality == QUALITY_SCENARIO else ""
            # Skip the period when it is the hoisted default, or when the label
            # already names it ("May 2025 vs April 2025" twice per line is waste).
            show_period = f.period and f.period != common and f.period not in f.label
            period = f" ({f.period})" if show_period else ""
            lines.append(f"{{{{{f.key}}}}} = {f.display} — {f.label}{period}{suffix}")
        return "\n".join(lines)

    def prompt_table_within(
        self, budget_tokens: int, *, priority: list[str] | None = None,
        keys: set[str] | None = None,
    ) -> tuple[str, int]:
        """The allow-list trimmed to fit ``budget_tokens``, best figures first.

        Returns ``(table, offered_count)``. A budget of 0 means "no ceiling" and
        returns the full table — the normal case on a provider whose per-minute
        budget a single report never approaches.

        Trimming is by *priority order*, not by size: this table is the single
        largest consumer of the context budget, and the registry is already
        arranged the way a decision should be reasoned about, so dropping from
        the tail removes the least decision-relevant figures. ``priority`` moves
        specific keys to the front — the caller passes the figures its ranked
        brief already cites, because a number the model can read in the brief
        but cannot cite is exactly the pressure that makes it type digits.
        """
        offered = [f.key for f in self._figures.values()
                   if f.prompt and (keys is None or f.key in keys)]
        full = self.prompt_table(keys=set(offered))
        if budget_tokens <= 0 or estimate_tokens(full) <= budget_tokens:
            return full, len(offered)

        front = [k for k in (priority or []) if k in offered]
        ordered = front + [k for k in offered if k not in set(front)]

        chosen: set[str] = set()
        table = ""
        for key in ordered:
            trial = chosen | {key}
            rendered = self.prompt_table(keys=trial)
            if estimate_tokens(rendered) > budget_tokens:
                break
            chosen, table = trial, rendered
        return table, len(chosen)

    # ── Rendering ──────────────────────────────────────────────────────────
    def render(self, text: str) -> tuple[str, list[str]]:
        """Substitute every ``{{key}}`` with its exact display string.

        Returns ``(rendered_text, unknown_keys)``.  An unknown token is replaced
        with an explicit ``[figure unavailable: key]`` marker rather than being
        left as raw braces or silently deleted — an invented citation must be
        visible in the output, never cosmetically hidden.
        """
        unknown: list[str] = []
        text = normalise_tokens(text)

        def _sub(m: re.Match) -> str:
            key, trailing_pct = m.group(1), m.group(2)
            fig = self._figures.get(key)
            if fig is None:
                unknown.append(key)
                return f"[figure unavailable: {key}]" + (trailing_pct or "")
            # Swallow the duplicate "%" only for figures that print their own.
            if fig.unit == "percent":
                return fig.display
            return fig.display + (trailing_pct or "")

        return _TOKEN_PCT_RE.sub(_sub, text), unknown

    def used_keys(self, text: str) -> list[str]:
        """Keys actually cited in *text* (pre-render), in order of appearance."""
        seen: list[str] = []
        for m in TOKEN_RE.finditer(normalise_tokens(text)):
            if m.group(1) in self._figures and m.group(1) not in seen:
                seen.append(m.group(1))
        return seen


# ── Registry construction ───────────────────────────────────────────────────

def _period_of(window: dict) -> str:
    return f"{window.get('start')} → {window.get('end')}"


def _add_window(reg: FigureRegistry, prefix: str, win: dict, tag: str, *, prompt: bool) -> None:
    """Register revenue / orders / customers / AOV for one comparison window."""
    period = _period_of(win)
    label = win.get("label", tag)
    reg.add(f"{prefix}_revenue", f"Revenue in {label}", win.get("revenue"), "currency",
            f"sum of line revenue for rows dated {period}", period=period, prompt=prompt)
    reg.add(f"{prefix}_orders", f"Orders in {label}", win.get("orders"), "count",
            f"count of {win.get('order_basis', 'orders')} dated {period}", period=period,
            prompt=prompt)
    reg.add(f"{prefix}_customers", f"Distinct customers in {label}", win.get("customers"), "count",
            f"distinct customer ids with a row dated {period}", period=period, prompt=prompt)
    reg.add(f"{prefix}_aov", f"Average order value in {label}", win.get("aov"), "currency",
            f"revenue ÷ orders for {period}", period=period, prompt=prompt)
    # Units are registered for the audit trail but never offered: no decision in
    # the brief turns on them, and they read confusingly next to order counts.
    reg.add(f"{prefix}_units", f"Units sold in {label}", win.get("units"), "count",
            f"sum of quantity for rows dated {period}", period=period, prompt=False)


def build_registry(payload: dict, decision: dict) -> FigureRegistry:
    """Assemble every citable figure for one insights report.

    Order matters: the model reads this table top-down, so it is arranged the
    way a decision should be reasoned about — what the data covers, how big the
    business is, which way it is moving and why, where the money is
    concentrated, where it leaks, and what an action is worth.
    """
    reg = FigureRegistry()
    kpi = payload.get("kpi", {}) or {}
    sections = (decision or {}).get("sections", {}) or {}
    meta = payload.get("metadata", {}) or {}

    # ── 0. Definitional constants ──────────────────────────────────────────
    # The thresholds the concentration metrics are *defined* by. They are
    # registered rather than typed as literals so that a sentence like "80% of
    # revenue" is a citation like every other number — otherwise the only bare
    # digits in the report would be exactly the ones nothing checks.
    reg.add("pareto_threshold", "Concentration threshold used (a definition, not a measurement)",
            80, "percent", "fixed definition: the share of revenue used to size the 'vital few'",
            display="80%")
    reg.add("top_decile_pct", "Top-customer bracket used (a definition, not a measurement)",
            10, "percent", "fixed definition: the top 10% of customers by revenue",
            display="10%")

    # ── 1. What the data covers ────────────────────────────────────────────
    cov = sections.get("coverage", {}) or {}
    full_period = ""
    if cov.get("available"):
        full_period = f"{cov['first_date']} → {cov['last_date']}"
        reg.add_text("period_start", "First date in the data", cov["first_date"],
                     f"minimum of '{cov['date_column']}'")
        reg.add_text("period_end", "Last date in the data", cov["last_date"],
                     f"maximum of '{cov['date_column']}'")
        reg.add("period_days", "Days covered by the data", cov["days_span"], "days",
                "last date − first date, inclusive", period=full_period)
        reg.add("period_months", "Months covered by the data", cov["months_span"], "ratio",
                "days covered ÷ 30.44", period=full_period, prompt=False)
        reg.add("days_with_sales", "Days with at least one transaction", cov["days_with_data"], "days",
                "distinct dates present in the data", period=full_period)
        reg.add("days_without_sales", "Days inside the period with no transaction at all",
                cov["days_without_data"], "days",
                "days covered − days with at least one transaction", period=full_period)
        reg.add("rows_undated", "Rows with an unreadable date", cov["rows_with_unparseable_date"], "count",
                f"rows where '{cov['date_column']}' could not be parsed as a date",
                prompt=bool(cov["rows_with_unparseable_date"]))
    reg.add("rows_total", "Rows (transaction lines) in the data", meta.get("row_count"), "count",
            "number of rows in the cleaned table")

    # ── 2. Size of the business ────────────────────────────────────────────
    rev_formula = f"sum of '{kpi.get('revenue_column_used')}' — {kpi.get('revenue_basis')}"
    reg.add("revenue_total", "Total revenue over the whole period", kpi.get("revenue"), "currency",
            rev_formula, period=full_period)
    reg.add("orders_total", "Total orders", kpi.get("orders"), "count",
            f"distinct values of '{kpi.get('order_column_used')}'" if kpi.get("order_column_used")
            else "row count (no order id column detected)", period=full_period)
    reg.add("customers_total", "Total distinct customers", kpi.get("total_customers"), "count",
            "distinct customer ids", period=full_period)
    reg.add("aov", "Average order value", kpi.get("aov"), "currency",
            kpi.get("aov_basis") or "revenue ÷ orders", period=full_period)
    reg.add("aov_median", "Median order value", kpi.get("aov_median"), "currency",
            "median of per-order totals", period=full_period)
    reg.add("order_value_max", "Largest single order", kpi.get("max_order_value"), "currency",
            "maximum per-order total", period=full_period, prompt=False)
    reg.add("revenue_per_customer_avg", "Average revenue per customer", kpi.get("revenue_per_customer_avg"),
            "currency", "revenue ÷ distinct customers", period=full_period)
    reg.add("revenue_per_customer_median", "Median revenue per customer",
            kpi.get("revenue_per_customer_median"), "currency",
            "median of per-customer revenue totals", period=full_period)

    # ── 3. Direction of travel, and what drove it ──────────────────────────
    # Both comparison windows are registered, but only ONE is offered to the
    # model. Two windows in the prompt means two different "revenue declines"
    # can appear in one report, and a reader cannot tell which is the real one.
    # Calendar months win when available: an owner already has a mental baseline
    # for "last month", and it matches how the rest of their reporting works.
    comparison = sections.get("comparison", {}) or {}
    primary_window = next(
        (k for k in ("last_month", "rolling_30d") if (comparison.get(k) or {}).get("comparable")),
        None,
    )
    for prefix, key in (("p30", "rolling_30d"), ("mon", "last_month")):
        cmp_ = comparison.get(key) or {}
        if not cmp_.get("comparable"):
            continue
        offer = key == primary_window
        cur, pri, bridge = cmp_["current"], cmp_["prior"], cmp_.get("bridge", {})
        _add_window(reg, prefix, cur, key, prompt=offer)
        _add_window(reg, f"{prefix}_prior", pri, key, prompt=offer)
        if not bridge.get("available"):
            continue
        span = f"{cur['label']} vs {pri['label']}"
        reg.add(f"{prefix}_revenue_change", f"Revenue change, {span} (carries its own +/− sign)",
                bridge["revenue_change"], "currency",
                f"revenue in {cur['label']} − revenue in {pri['label']}", period=span, prompt=offer)
        # Sign-free companion. "Revenue fell by {{..._change}}" renders as "fell
        # by -6,656.10", which literally states a rise; a magnitude figure is the
        # only way that sentence can be written correctly.
        change = bridge["revenue_change"] or 0.0
        reg.add(
            f"{prefix}_revenue_{'decline' if change < 0 else 'increase'}",
            f"Size of the revenue {'drop' if change < 0 else 'gain'}, {span} (no sign — use "
            f"after 'fell by' / 'grew by')",
            abs(change), "currency",
            f"absolute difference between revenue in {cur['label']} and in {pri['label']}",
            period=span, prompt=offer,
        )
        reg.add(f"{prefix}_revenue_change_pct", f"Revenue change %, {span} (carries its own sign)",
                bridge["revenue_change_pct"], "percent",
                f"revenue change ÷ revenue in {pri['label']} × 100", period=span, prompt=offer)
        pct = bridge["revenue_change_pct"]
        if pct is not None:
            reg.add(
                f"{prefix}_revenue_{'decline' if pct < 0 else 'increase'}_pct",
                f"Size of the revenue {'drop' if pct < 0 else 'gain'} in %, {span} (no sign)",
                abs(pct), "percent",
                f"absolute revenue change ÷ revenue in {pri['label']} × 100", period=span, prompt=offer,
            )
        reg.add(f"{prefix}_orders_change", f"Change in order count, {span}", bridge["orders_change"],
                "count", f"orders in {cur['label']} − orders in {pri['label']}", period=span, prompt=offer)
        reg.add(f"{prefix}_aov_change", f"Change in average order value, {span}", bridge["aov_change"],
                "currency", f"AOV in {cur['label']} − AOV in {pri['label']}", period=span, prompt=offer)
        reg.add(f"{prefix}_customers_change", f"Change in distinct customers, {span}",
                bridge["customers_change"], "count",
                f"customers in {cur['label']} − customers in {pri['label']}", period=span, prompt=offer)
        reg.add(f"{prefix}_volume_effect",
                f"Part of the revenue change caused by ORDER COUNT, {span}",
                bridge["volume_effect"], "currency",
                "(change in orders) × (prior-period average order value)", period=span, prompt=offer)
        reg.add(f"{prefix}_basket_effect",
                f"Part of the revenue change caused by ORDER SIZE, {span}",
                bridge["basket_effect"], "currency",
                "(change in average order value) × (prior-period order count)", period=span, prompt=offer)
        reg.add(f"{prefix}_interaction_effect",
                f"Combined order-count × order-size effect, {span}",
                bridge["interaction_effect"], "currency",
                "(change in orders) × (change in average order value)", period=span, prompt=False)

    growth = kpi.get("monthly_growth_avg")
    if growth is not None:
        reg.add("monthly_growth_avg_pct", "Average month-over-month revenue growth",
                round(growth * 100, 1), "percent",
                "mean of month-over-month % changes in revenue across every month in the data",
                period=full_period)

    # ── 4. Where the money is concentrated ─────────────────────────────────
    conc = sections.get("concentration", {}) or {}
    prod = conc.get("products", {}) or {}
    if prod.get("available"):
        reg.add("products_total", "Products with any revenue", prod["total_count"], "count",
                "distinct product values with revenue > 0", period=full_period)
        reg.add("products_for_80pct", "Products that together make 80% of revenue",
                prod["count_for_80pct"], "count",
                "products ranked by revenue, counted until the cumulative share reaches 80%",
                period=full_period)
        reg.add("products_for_80pct_share", "Share of the product range those products represent",
                prod["count_for_80pct_share_of_base"], "percent",
                "products making 80% of revenue ÷ total products × 100", period=full_period)
        reg.add_text("top_product_name", "Best-selling product by revenue", prod["top1_name"],
                     "product with the highest total revenue")
        reg.add("top_product_revenue", f"Revenue of '{prod['top1_name']}'", prod["top1_revenue"],
                "currency", f"sum of line revenue where product = '{prod['top1_name']}'",
                period=full_period)
        reg.add("top_product_share", f"Share of revenue from '{prod['top1_name']}'",
                prod["top1_share"], "percent", "its revenue ÷ total revenue × 100", period=full_period)
        reg.add("top5_products_share", "Share of revenue from the top 5 products",
                prod.get("top5_share"), "percent",
                "revenue of the 5 highest-revenue products ÷ total revenue × 100", period=full_period)
        reg.add("bottom_half_products_share", "Share of revenue from the weaker half of the range",
                prod["bottom_half_share"], "percent",
                "revenue of the bottom 50% of products by revenue ÷ total revenue × 100",
                period=full_period)
        for i, ent in enumerate(prod.get("top_entities", [])[:5], start=1):
            # Ranks 4-5 stay in the audit trail but are not offered: no decision
            # in the brief turns on them, and each extra near-identical figure is
            # another chance to cite the wrong one.
            offer_rank = i <= 3
            reg.add_text(f"product{i}_name", f"#{i} product by revenue", ent["name"],
                         "ranked by total revenue")
            reg.add(f"product{i}_revenue", f"Revenue of '{ent['name']}'", ent["revenue"], "currency",
                    f"sum of line revenue where product = '{ent['name']}'", period=full_period,
                    prompt=offer_rank)
            reg.add(f"product{i}_share", f"Revenue share of '{ent['name']}'", ent["share"], "percent",
                    "its revenue ÷ total revenue × 100", period=full_period, prompt=offer_rank)

    cust = conc.get("customers", {}) or {}
    if cust.get("available"):
        reg.add("customers_for_80pct", "Customers who together make 80% of revenue",
                cust["count_for_80pct"], "count",
                "customers ranked by revenue, counted until the cumulative share reaches 80%",
                period=full_period)
        reg.add("top_decile_customers", "Size of the top 10% of customers",
                cust["top_decile_count"], "count", "10% of the distinct customer count, rounded",
                period=full_period)
        reg.add("top_decile_customer_share", "Share of revenue from the top 10% of customers",
                cust["top_decile_share"], "percent",
                "revenue of the top 10% of customers ÷ total revenue × 100", period=full_period)
        reg.add("bottom_half_customer_share", "Share of revenue from the lower half of customers",
                cust["bottom_half_share"], "percent",
                "revenue of the bottom 50% of customers ÷ total revenue × 100", period=full_period)
        reg.add("top_customer_share", "Share of revenue from the single largest customer",
                cust["top1_share"], "percent",
                "largest customer's revenue ÷ total revenue × 100", period=full_period)

    # ── 5. What is moving ──────────────────────────────────────────────────
    mom = sections.get("momentum", {}) or {}
    if mom.get("available"):
        span = f"{mom['current_window']} vs {mom['prior_window']}"
        for i, row in enumerate(mom.get("fallers", [])[:3], start=1):
            reg.add_text(f"faller{i}_name", f"#{i} declining product", row["product"],
                         "largest absolute revenue decline between the two windows")
            reg.add(f"faller{i}_change", f"Revenue change for '{row['product']}', {span} "
                    f"(carries its own − sign)",
                    row["change"], "currency",
                    f"'{row['product']}' revenue in {mom['current_window']} − in {mom['prior_window']}",
                    period=span, prompt=False)
            # Sign-free companion — "a decrease of {{faller1_change}}" would
            # render as "a decrease of −1,852.90", stating the opposite.
            reg.add(f"faller{i}_loss", f"Revenue lost by '{row['product']}', {span} (no sign)",
                    abs(row["change"] or 0.0), "currency",
                    f"size of the fall in '{row['product']}' revenue between the two windows",
                    period=span)
            reg.add(f"faller{i}_change_pct", f"Revenue decline of '{row['product']}' in % , {span} "
                    f"(no sign — the word 'decline' carries it)",
                    abs(row["change_pct"] or 0.0), "percent",
                    "size of its revenue change ÷ its prior revenue × 100", period=span)
            reg.add(f"faller{i}_current", f"Current revenue of '{row['product']}', {mom['current_window']}",
                    row["current_revenue"], "currency", "sum of its line revenue in the current window",
                    period=span, prompt=False)
        for i, row in enumerate(mom.get("risers", [])[:3], start=1):
            reg.add_text(f"riser{i}_name", f"#{i} growing product", row["product"],
                         "largest absolute revenue gain between the two windows")
            reg.add(f"riser{i}_gain", f"Revenue gained by '{row['product']}', {span}",
                    abs(row["change"] or 0.0), "currency",
                    f"'{row['product']}' revenue in {mom['current_window']} − in {mom['prior_window']}",
                    period=span)
            reg.add(f"riser{i}_change_pct", f"Revenue growth of '{row['product']}' in %, {span}",
                    row["change_pct"], "percent", "its revenue change ÷ its prior revenue × 100",
                    period=span)

    # ── 6. Profitability ───────────────────────────────────────────────────
    margin = sections.get("margin", {}) or {}
    if margin.get("available"):
        reg.add("gross_profit", "Gross profit over the whole period", margin["gross_profit"],
                "currency", margin["basis"], period=full_period)
        reg.add("gross_margin_pct", "Gross margin", margin["gross_margin_pct"], "percent",
                "gross profit ÷ revenue × 100", period=full_period)
        if margin.get("margin_drag_revenue"):
            reg.add("margin_drag_revenue", "Revenue sitting in below-average-margin products",
                    margin["margin_drag_revenue"], "currency",
                    "revenue of products with ≥1% revenue share whose margin is more than "
                    "2 points below the portfolio margin", period=full_period)
            reg.add("margin_drag_share", "Share of revenue in those products",
                    margin["margin_drag_share"], "percent",
                    "their revenue ÷ total revenue × 100", period=full_period)
        for i, row in enumerate(margin.get("margin_drag_products", [])[:3], start=1):
            reg.add_text(f"lowmargin{i}_name", f"#{i} below-average-margin product", row["product"],
                         "ranked by revenue among products below the portfolio margin")
            reg.add(f"lowmargin{i}_margin_pct", f"Gross margin of '{row['product']}'",
                    row["margin_pct"], "percent", "its gross profit ÷ its revenue × 100",
                    period=full_period)
            reg.add(f"lowmargin{i}_revenue", f"Revenue of '{row['product']}'", row["revenue"],
                    "currency", "sum of its line revenue", period=full_period)
        for i, row in enumerate(margin.get("best_margin_products", [])[:2], start=1):
            reg.add_text(f"highmargin{i}_name", f"#{i} highest-margin product", row["product"],
                         "highest gross margin among products with ≥1% revenue share")
            reg.add(f"highmargin{i}_margin_pct", f"Gross margin of '{row['product']}'",
                    row["margin_pct"], "percent", "its gross profit ÷ its revenue × 100",
                    period=full_period)
        for i, row in enumerate(margin.get("loss_making_products", [])[:3], start=1):
            reg.add_text(f"lossmaker{i}_name", f"#{i} loss-making product", row["product"],
                         "negative total gross profit")
            reg.add(f"lossmaker{i}_loss", f"Gross loss on '{row['product']}'", row["gross_profit"],
                    "currency", "its revenue − its cost", period=full_period)

    # ── 7. Discounting ─────────────────────────────────────────────────────
    disc = sections.get("discount", {}) or {}
    if disc.get("available"):
        reg.add("discount_given", "Revenue given away as discount", disc["discount_given"],
                "currency", disc["basis"], period=full_period)
        reg.add("discount_share_of_gross", "Discount as a share of pre-discount value",
                disc["discount_share_of_gross"], "percent",
                "discount given ÷ pre-discount value × 100", period=full_period)
        reg.add("discount_lines_pct", "Share of transaction lines that were discounted",
                disc["lines_discounted_pct"], "percent",
                "lines with a discount > 0 ÷ all lines × 100", period=full_period)
        reg.add("discount_avg_pct", "Average discount on the lines that were discounted",
                disc["avg_discount_on_discounted_lines_pct"], "percent",
                "mean discount rate across discounted lines only", period=full_period)
        reg.add("discount_max_pct", "Largest discount given on any line", disc["max_discount_pct"],
                "percent", "maximum discount rate in the data", period=full_period)

    # ── 8. Repeat-purchase economics ───────────────────────────────────────
    rep = sections.get("repeat", {}) or {}
    if rep.get("available"):
        reg.add("repeat_rate_pct", "Share of customers who bought more than once",
                rep["repeat_rate_pct"], "percent",
                f"customers with 2+ orders ÷ all customers × 100 ({rep['basis']})", period=full_period)
        reg.add("repeat_customers", "Customers who bought more than once", rep["repeat_customers"],
                "count", "customers with 2 or more orders", period=full_period)
        reg.add("one_time_customers", "Customers who bought exactly once",
                rep["one_time_customers"], "count", "customers with exactly 1 order",
                period=full_period)
        reg.add("repeat_revenue_share", "Share of revenue from repeat customers",
                rep["repeat_revenue_share"], "percent",
                "revenue from customers with 2+ orders ÷ total revenue × 100", period=full_period)
        reg.add("avg_orders_per_customer", "Average orders per customer",
                rep["avg_orders_per_customer"], "ratio", "total orders ÷ distinct customers",
                period=full_period)
        reg.add("revenue_per_repeat_customer", "Average revenue per repeat customer",
                rep["avg_revenue_per_repeat_customer"], "currency",
                "revenue from repeat customers ÷ number of repeat customers", period=full_period)
        reg.add("revenue_per_one_time_customer", "Average revenue per one-time customer",
                rep["avg_revenue_per_one_time_customer"], "currency",
                "revenue from one-time customers ÷ number of one-time customers", period=full_period)
        reg.add("median_order_value", "Median order value", rep["median_order_value"], "currency",
                "median of per-order revenue totals", period=full_period)

    # ── 9. Weekly rhythm ───────────────────────────────────────────────────
    wd = sections.get("weekday", {}) or {}
    if wd.get("available"):
        reg.add_text("best_weekday", "Strongest day of the week", wd["best_day"],
                     "highest average revenue per occurrence of that weekday")
        reg.add_text("worst_weekday", "Weakest day of the week", wd["worst_day"],
                     "lowest average revenue per occurrence of that weekday")
        reg.add("avg_daily_revenue", "Average revenue per day", wd["avg_daily_revenue"], "currency",
                "mean of daily revenue totals across the period", period=full_period)
        reg.add("best_weekday_avg", f"Average revenue on a {wd['best_day']}", wd["best_day_avg"],
                "currency", f"mean revenue across every {wd['best_day']} in the data",
                period=full_period)
        reg.add("best_weekday_uplift_pct", f"How much better a {wd['best_day']} is than an average day",
                wd["best_day_vs_avg_pct"], "percent",
                f"({wd['best_day']} average − overall daily average) ÷ overall daily average × 100",
                period=full_period)
        reg.add("worst_weekday_avg", f"Average revenue on a {wd['worst_day']}", wd["worst_day_avg"],
                "currency", f"mean revenue across every {wd['worst_day']} in the data",
                period=full_period)
        reg.add("worst_weekday_gap_pct", f"How much worse a {wd['worst_day']} is than an average day",
                wd["worst_day_vs_avg_pct"], "percent",
                f"({wd['worst_day']} average − overall daily average) ÷ overall daily average × 100",
                period=full_period)

    # ── 10. Data quality ───────────────────────────────────────────────────
    for check in payload.get("quality", []) or []:
        if check.get("check") == "missing_values" and check.get("total_missing"):
            reg.add("missing_values_pct", "Share of all cells that are empty",
                    check.get("overall_pct"), "percent",
                    "empty cells ÷ (rows × columns) × 100")
        if check.get("check") == "duplicate_rows" and check.get("count"):
            reg.add("duplicate_rows", "Fully duplicated rows", check.get("count"), "count",
                    "rows identical to another row across every column")
            reg.add("duplicate_rows_pct", "Share of rows that are duplicates", check.get("pct"),
                    "percent", "duplicate rows ÷ total rows × 100")

    # ── 11. Sized opportunities (arithmetic, assumption stated) ────────────
    for scn in (decision or {}).get("scenarios", []) or []:
        reg.add(f"scn_{scn['key']}", scn["label"], scn["value"], "currency", scn["formula"],
                period=full_period, quality=QUALITY_SCENARIO, assumption=scn["assumption"])

    return reg


def registry_summary(reg: FigureRegistry) -> dict[str, Any]:
    """Counts for the UI badge: how many figures exist and how many are exact."""
    figs = reg.numeric()
    return {
        "total": len(figs),
        "exact": sum(1 for f in figs if f.quality == QUALITY_EXACT),
        "scenario": sum(1 for f in figs if f.quality == QUALITY_SCENARIO),
        "text": len(reg.all()) - len(figs),
    }
