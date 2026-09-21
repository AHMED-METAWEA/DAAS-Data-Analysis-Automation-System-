"""The anti-fabrication contract: citations, rendering and strict verification.

These lock in the property the insights pipeline is built around — a number in
the report either came out of the calculation engine verbatim, or it was checked
against the closed registry and flagged if it did not match. There is no third
path, and no number reaches a reader unexamined.
"""

from __future__ import annotations

import pandas as pd
import pytest

from agents.analytics.engine import run_analytics
from agents.insights.decision_metrics import compute_decision_metrics
from agents.insights.figures import (
    LEFTOVER_MARKUP_RE,
    FigureRegistry,
    build_registry,
    normalise_tokens,
)
from agents.insights.strict_verify import (
    check_action_values,
    check_bare_referents,
    check_sign_agreement,
    mask_non_claims,
    render_and_verify,
    verify_typed_numbers,
)


def _sales_df(n_days: int = 200) -> pd.DataFrame:
    rows = []
    base = pd.Timestamp("2024-01-01")
    oid = 0
    for d in range(n_days):
        for _ in range(1 + d % 3):
            oid += 1
            rows.append({
                "order_id": f"O{oid}",
                "customer_id": f"C{oid % 25}",
                "order_date": (base + pd.Timedelta(days=d)).strftime("%Y-%m-%d"),
                "product": f"P{oid % 6}",
                "quantity": 1,
                "total_price": 20.0 + (oid % 10) * 5,
                "cost": 10.0 + (oid % 10) * 2,
            })
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def registry() -> FigureRegistry:
    df = _sales_df()
    return build_registry(run_analytics(df), compute_decision_metrics(df))


# ── Rendering ───────────────────────────────────────────────────────────────

def test_citation_renders_the_exact_registered_value(registry: FigureRegistry) -> None:
    fig = registry.get("revenue_total")
    rendered, unknown = registry.render("Revenue was {{revenue_total}}.")
    assert rendered == f"Revenue was {fig.display}."
    assert unknown == []


def test_invented_citation_is_visible_not_silently_dropped(registry: FigureRegistry) -> None:
    """An invented token must leave a mark in the output. Silently deleting it
    would turn a fabrication attempt into a clean-looking sentence."""
    rendered, unknown = registry.render("Revenue was {{revenue_from_mars}}.")
    assert unknown == ["revenue_from_mars"]
    assert "[figure unavailable: revenue_from_mars]" in rendered


def test_redundant_percent_sign_is_absorbed(registry: FigureRegistry) -> None:
    """Percent figures print their own sign; a model-added one must not produce
    '43.8%%' in a published document."""
    rendered, _ = registry.render("Repeat rate is {{repeat_rate_pct}}%.")
    assert "%%" not in rendered
    assert rendered.endswith("%.")


def test_malformed_token_is_repaired(registry: FigureRegistry) -> None:
    assert normalise_tokens("{{revenue_total}") == "{{revenue_total}}"
    assert normalise_tokens("{revenue_total}}") == "{{revenue_total}}"
    rendered, unknown = registry.render("Revenue was {{revenue_total}.")
    assert unknown == []
    assert "{" not in rendered and "}" not in rendered


def test_no_citation_markup_survives_into_a_report(registry: FigureRegistry) -> None:
    draft = "Revenue {{revenue_total}}, orders {{orders_total}, bogus {{nope}}."
    rendered, result = render_and_verify(draft, registry)
    assert not LEFTOVER_MARKUP_RE.search(rendered.replace("[figure unavailable: nope]", ""))
    assert "nope" in result.unknown_tokens
    assert not result.is_clean


# ── Strict verification ─────────────────────────────────────────────────────

def test_fabricated_number_is_caught(registry: FigureRegistry) -> None:
    result = verify_typed_numbers("Total revenue was 9,999,999.00 this period.", registry)
    assert result.typed_total == 1
    assert not result.typed[0].verified
    assert not result.is_clean
    assert result.typed[0].context, "the offending sentence must be quoted for the correction pass"


def test_typed_number_matching_a_computed_figure_passes(registry: FigureRegistry) -> None:
    display = registry.get("revenue_total").display
    result = verify_typed_numbers(f"Total revenue was {display} this period.", registry)
    assert result.typed[0].verified
    assert result.typed[0].figure_key == "revenue_total"
    assert result.typed[0].basis == "registry_exact"


def test_honest_rounding_is_accepted_but_distortion_is_not(registry: FigureRegistry) -> None:
    value = registry.get("revenue_total").value
    rounded = verify_typed_numbers(f"Revenue was about {round(value):,}.", registry)
    assert rounded.typed[0].verified

    distorted = verify_typed_numbers(f"Revenue was {value * 1.4:,.2f}.", registry)
    assert not distorted.typed[0].verified


def test_report_structure_numbers_are_not_scored(registry: FigureRegistry) -> None:
    """Headings and enumerations are scaffolding, not claims about the business."""
    text = "### Decision 2: act now\nThe top 3 priorities over the next 30 days."
    result = verify_typed_numbers(text, registry)
    assert result.typed_total == 0


def test_dates_are_not_mistaken_for_figures(registry: FigureRegistry) -> None:
    masked = mask_non_claims("Between 2024-01-01 and 2024-12-31, and in May 2025.")
    assert "2024" not in masked
    result = verify_typed_numbers("Sales ran from 2024-01-01 to 2024-12-31 (May 2025).", registry)
    assert result.typed_total == 0


def test_citation_rate_reflects_engine_authored_numbers(registry: FigureRegistry) -> None:
    result = verify_typed_numbers("Revenue {{revenue_total}} across {{orders_total}} orders.", registry)
    assert result.typed_total == 0
    assert result.cited_count == 2
    assert result.citation_rate == 1.0
    assert result.is_clean


# ── Attribution checks ──────────────────────────────────────────────────────

def test_currency_symbol_is_flagged_when_the_data_has_no_currency(registry: FigureRegistry) -> None:
    result = verify_typed_numbers("Revenue was $1,000.00.", registry, currency_known=False)
    assert result.currency_claims == ["$"]
    assert not result.is_clean

    allowed = verify_typed_numbers("Revenue was $1,000.00.", registry, currency_known=True)
    assert allowed.currency_claims == []


def test_action_value_citing_a_level_is_flagged(registry: FigureRegistry) -> None:
    """'What it's worth: {{revenue_total}}' is a true number making a false
    claim — nothing is *worth* a period total."""
    level = "- **What it's worth:** {{revenue_total}} in upside."
    assert check_action_values(level, registry)

    key = next(
        (f.key for f in registry.all() if f.key.startswith("scn_")),
        None,
    )
    if key:
        sized = f"- **What it's worth:** {{{{{key}}}}} under its stated assumption."
        assert check_action_values(sized, registry) == []


def test_action_value_with_no_figure_at_all_is_flagged(registry: FigureRegistry) -> None:
    assert check_action_values("- **What it's worth:** a lot, probably.", registry)


def test_bare_referent_citation_is_flagged(registry: FigureRegistry) -> None:
    assert check_bare_referents("Growth is on track, as measured by {{revenue_total}}.", registry)
    assert check_bare_referents("Monthly revenue passes {{revenue_total}}.", registry) == []


def test_change_verb_on_a_negative_figure_is_flagged() -> None:
    """'fell by -6,656.10' states a rise. Correct number, false sentence —
    exactly the failure a numbers-only check cannot see."""
    reg = FigureRegistry()
    reg.add("mon_revenue_change", "Revenue change", -6656.10, "currency", "curr - prior")
    reg.add("mon_revenue_decline", "Size of the drop", 6656.10, "currency", "|curr - prior|")

    problems = check_sign_agreement("Revenue fell by {{mon_revenue_change}}.", reg)
    assert len(problems) == 1
    assert "mon_revenue_decline" in problems[0], "must point at the sign-free companion"

    assert check_sign_agreement("Revenue fell by {{mon_revenue_decline}}.", reg) == []
    assert check_sign_agreement("Revenue changed by {{mon_revenue_change}}.", reg) == []


# ── Registry construction ───────────────────────────────────────────────────

def test_every_figure_carries_a_formula(registry: FigureRegistry) -> None:
    """The audit trail is the product, not a debug aid: a figure with no stated
    derivation cannot be checked by the reader."""
    for figure in registry.all():
        assert figure.formula, f"{figure.key} has no formula"
        assert figure.label, f"{figure.key} has no label"


def test_scenario_figures_declare_their_assumption(registry: FigureRegistry) -> None:
    scenarios = [f for f in registry.all() if f.quality == "scenario"]
    assert scenarios, "expected sized opportunities in this fixture"
    for figure in scenarios:
        assert figure.assumption, f"{figure.key} is a scenario with no stated assumption"


def test_only_one_comparison_window_is_offered_to_the_model(registry: FigureRegistry) -> None:
    """Two windows in the prompt means two different 'revenue declines' can
    appear in one report and the reader cannot tell which is real."""
    offered = {
        line.split("}}")[0].removeprefix("{{")
        for line in registry.prompt_table().splitlines()
        if line.startswith("{{")
    }
    assert not ({"mon_revenue_change"} & offered and {"p30_revenue_change"} & offered)


def test_hidden_figures_still_verify_a_typed_number(registry: FigureRegistry) -> None:
    """Trimming the prompt must not weaken verification: a figure kept out of
    the token list is still a computed value the report may legitimately state."""
    hidden = next((f for f in registry.numeric() if not f.prompt), None)
    assert hidden is not None
    result = verify_typed_numbers(f"A figure of {hidden.display}.", registry)
    assert result.typed[0].verified
