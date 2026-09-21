"""The drill-down search — does it find the cause, and does it stay quiet when
there isn't one?

The second half matters more than the first. Any group-by finds *something*;
what makes attribution trustworthy is that it does not manufacture a cause out
of a dataset that has none. So the suite is built around a matched pair: one
dataset with a planted three-dimensional cause, and an identical generator with
the plant removed. The engine must recover the first and refuse to confirm
anything in the second.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd
import pytest

from agents.rootcause.dimensions import detect_dimensions
from agents.rootcause.engine import describe_options, run_root_cause
from agents.rootcause.measures import MeasureUnavailableError, build_measure, resolve_windows
from agents.rootcause.schema import Slice, SlicePredicate

PRODUCTS = {
    "Office Bulk Subscription": 120.0, "Desk Lamp": 40.0, "Ergo Chair": 300.0,
    "Notebook": 8.0, "Monitor Arm": 65.0,
}
PRODUCT_NAMES = list(PRODUCTS)
REGIONS = ["North", "South", "East", "West"]
CHANNELS = ["Email", "Retail", "Web"]
PAYMENTS = ["Card", "Cash", "Transfer"]

# The planted cause: this exact combination all but disappears in the current
# period, while every other slice is generated identically in both.
PLANTED = {"product_name": "Office Bulk Subscription", "region": "North", "channel": "Email"}


def _dataset(*, plant: bool, seed: int = 7, per_day: int = 200) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows, oid = [], 0
    for day in pd.date_range("2025-03-01", "2025-04-30", freq="D"):
        current = day >= pd.Timestamp("2025-04-01")
        for _ in range(per_day):
            product = PRODUCT_NAMES[rng.integers(len(PRODUCT_NAMES))]
            region = REGIONS[rng.integers(len(REGIONS))]
            channel = CHANNELS[rng.integers(len(CHANNELS))]
            if (
                plant and current
                and product == PLANTED["product_name"]
                and region == PLANTED["region"]
                and channel == PLANTED["channel"]
                and rng.random() < 0.9
            ):
                continue
            oid += 1
            price = PRODUCTS[product]
            rows.append({
                "order_id": f"O{oid:06d}", "order_date": day,
                "customer_id": f"C{int(rng.integers(1, 900)):04d}",
                "product_name": product, "region": region, "channel": channel,
                "payment_method": PAYMENTS[rng.integers(len(PAYMENTS))],
                "quantity": 2, "unit_price": price, "unit_cost": price * 0.6,
            })
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def planted() -> pd.DataFrame:
    return _dataset(plant=True)


@pytest.fixture(scope="module")
def control() -> pd.DataFrame:
    return _dataset(plant=False)


# ── The core claim ──────────────────────────────────────────────────────────

def test_recovers_the_planted_three_dimensional_cause(planted):
    """The whole feature in one assertion: find the smallest slice responsible."""
    result = run_root_cause(
        planted, measure="revenue", window_mode="last_month", with_narrative=False,
    )
    assert result.available, result.reason

    found = {tuple(sorted(e.slice.as_dict().items())) for e in result.explanations}
    assert tuple(sorted(PLANTED.items())) in found, (
        "the planted product × region × channel slice was not among the explanations: "
        f"{[e.slice.render_compact() for e in result.explanations]}"
    )


def test_the_planted_slice_is_confirmed_not_merely_listed(planted):
    result = run_root_cause(
        planted, measure="revenue", window_mode="last_month", with_narrative=False,
    )
    planted_exp = next(
        e for e in result.explanations if e.slice.as_dict() == PLANTED
    )
    # Tiny slice, huge share of the change: the concentration ratio is the
    # headline claim and it must be dramatic here.
    assert planted_exp.concentration > 10
    assert abs(planted_exp.explanatory_power) > 0.3
    # And it must clear the bar *after* correcting for how many slices were
    # tested — otherwise it is a lead, not a finding.
    assert planted_exp.robust is True
    assert planted_exp.signal_to_noise > result.stats.corrected_threshold


def test_control_dataset_confirms_nothing(control):
    """No planted cause, so nothing may be reported as one.

    This is the test that makes the feature honest. Contribution analysis will
    always surface *some* slice; the guard is the noise model plus the
    correction for how many slices were tested.
    """
    result = run_root_cause(
        control, measure="revenue", window_mode="last_month", with_narrative=False,
    )
    assert result.available
    confirmed = [e for e in result.explanations if e.robust]
    assert confirmed == [], (
        "a dataset with no planted cause produced confirmed root causes: "
        f"{[e.slice.render_compact() for e in confirmed]}"
    )
    if result.explanations:
        # Anything still shown must be labelled as a lead, and the caveat must
        # be stated rather than left for the reader to infer.
        assert any("correction" in w for w in result.warnings)


# ── Arithmetic that has to close ────────────────────────────────────────────

def test_explanatory_power_is_the_slice_change_over_the_total(planted):
    result = run_root_cause(
        planted, measure="revenue", window_mode="last_month", with_narrative=False,
    )
    for e in result.explanations:
        assert e.delta == pytest.approx(e.current_value - e.prior_value, abs=0.01)
        assert e.explanatory_power == pytest.approx(e.delta / result.total_delta, rel=1e-6)


def test_slice_plus_rest_reconciles_with_the_total(planted):
    """For an additive measure the parts must add up, or the report is lying."""
    result = run_root_cause(
        planted, measure="revenue", window_mode="last_month", with_narrative=False,
    )
    top = result.explanations[0]
    assert top.prior_value + top.rest_prior == pytest.approx(result.prior.value, rel=1e-6)
    assert top.current_value + top.rest_current == pytest.approx(result.current.value, rel=1e-6)


def test_excess_measures_deviation_from_the_business_wide_rate(planted):
    result = run_root_cause(
        planted, measure="revenue", window_mode="last_month", with_narrative=False,
    )
    growth = result.current.value / result.prior.value
    for e in result.explanations:
        assert e.expected_current == pytest.approx(e.prior_value * growth, rel=1e-6)
        assert e.excess == pytest.approx(e.current_value - e.expected_current, rel=1e-6)


def test_slice_bridge_decomposition_closes(planted):
    """Volume + basket + interaction must equal the slice's own change."""
    result = run_root_cause(
        planted, measure="revenue", window_mode="last_month", with_narrative=False,
    )
    bridged = [e for e in result.explanations if e.bridge and e.bridge.get("available")]
    assert bridged, "no explanation carried a revenue bridge"
    for e in bridged:
        parts = (
            e.bridge["volume_effect"] + e.bridge["basket_effect"] + e.bridge["interaction_effect"]
        )
        assert parts == pytest.approx(e.delta, abs=0.05)
        assert e.bridge["residual"] == pytest.approx(0.0, abs=0.05)


# ── The search itself ───────────────────────────────────────────────────────

def test_pruning_is_reported_not_hidden(planted):
    result = run_root_cause(
        planted, measure="revenue", window_mode="last_month", with_narrative=False,
    )
    stats = result.stats
    assert stats.nodes_evaluated > 0
    assert stats.exhaustive_combinations >= stats.nodes_evaluated
    assert stats.slices_tested > 0
    assert stats.corrected_threshold > 2.0  # correction always raises the bar
    if stats.pruned_by_beam:
        assert any("beam" in w for w in result.warnings)


def test_duplicate_lattice_paths_are_recognised_once(planted):
    """A∧B is reachable from A and from B; it must be computed once."""
    result = run_root_cause(
        planted, measure="revenue", window_mode="last_month",
        max_depth=3, with_narrative=False,
    )
    assert result.stats.duplicate_paths > 0
    assert result.stats.nodes_examined > result.stats.nodes_evaluated


def test_explanations_are_not_nested_restatements(planted):
    """Two explanations must not be the same finding at different depths."""
    result = run_root_cause(
        planted, measure="revenue", window_mode="last_month", with_narrative=False,
    )
    keys = [set(e.slice.key()) for e in result.explanations]
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            if a < b or b < a:
                # A nested pair is allowed only when the deeper slice is
                # materially more concentrated — that is the refinement rule.
                deeper = result.explanations[keys.index(b if a < b else a)]
                shallower = result.explanations[keys.index(a if a < b else b)]
                assert deeper.concentration > shallower.concentration


def test_deeper_search_finds_at_least_what_a_shallow_one_does(planted):
    shallow = run_root_cause(
        planted, measure="revenue", window_mode="last_month",
        max_depth=1, with_narrative=False,
    )
    deep = run_root_cause(
        planted, measure="revenue", window_mode="last_month",
        max_depth=3, with_narrative=False,
    )
    assert deep.total_delta == pytest.approx(shallow.total_delta)
    # The deep search reaches the planted triple; the shallow one cannot.
    assert max((e.concentration for e in deep.explanations), default=0) > max(
        (e.concentration for e in shallow.explanations), default=0
    )


# ── Guards against confidently wrong answers ────────────────────────────────

def test_weekday_is_off_by_default_because_the_calendar_confounds_it(planted):
    """April and May do not contain the same number of Tuesdays.

    With weekday enabled, a flat business can show "Tuesday explains 92% of the
    decline" — a calendar artefact wearing the costume of a root cause.
    """
    dimensions, skipped = detect_dimensions(
        planted, time_column="order_date", include_weekday=False,
    )
    assert "weekday" not in {d.name for d in dimensions}
    assert any(s["column"] == "weekday" for s in skipped)

    result = run_root_cause(
        planted, measure="revenue", window_mode="last_month",
        include_weekday=True, with_narrative=False,
    )
    assert any("weekday" in w.lower() for w in result.warnings), (
        "enabling weekday must state the calendar confound"
    )


def test_identifier_columns_are_rejected_as_dimensions(planted):
    _, skipped = detect_dimensions(planted, time_column="order_date")
    rejected = {s["column"] for s in skipped}
    assert "customer_id" in rejected   # near-unique
    assert "order_id" in rejected      # the order key
    assert "unit_price" in rejected    # a measurement, not a category


def test_thin_slices_do_not_become_causes():
    """A handful of transactions swinging is not a segment failure."""
    df = _dataset(plant=True, per_day=12, seed=3)
    result = run_root_cause(
        df, measure="revenue", window_mode="last_month",
        min_signal_to_noise=2.0, with_narrative=False,
    )
    if result.available:
        for e in result.explanations:
            if e.robust:
                assert e.prior_rows + e.current_rows >= 12


# ── Degrading honestly ──────────────────────────────────────────────────────

def test_no_date_column_is_refused_with_a_reason():
    df = pd.DataFrame({"product": ["a", "b"] * 30, "revenue": [10.0, 20.0] * 30})
    result = run_root_cause(df, with_narrative=False)
    assert not result.available
    assert "date" in result.reason.lower()


def test_empty_dataset_is_refused():
    result = run_root_cause(pd.DataFrame(), with_narrative=False)
    assert not result.available


def test_unknown_measure_is_refused_with_a_reason(planted):
    result = run_root_cause(planted, measure="nonsense", with_narrative=False)
    assert not result.available
    assert "nonsense" in result.reason


def test_distinct_count_measures_are_flagged_as_non_additive(planted):
    result = run_root_cause(
        planted, measure="orders", window_mode="last_month", with_narrative=False,
    )
    assert result.available
    assert result.measure_additive is False
    assert any("distinct count" in w for w in result.warnings)
    # No variance model applies to a distinct count, so no z is asserted.
    for e in result.explanations:
        assert e.signal_to_noise is None


def test_options_describe_only_what_the_data_supports(planted):
    options = describe_options(planted)
    assert options["available"]
    keys = {m["key"] for m in options["measures"]}
    assert {"revenue", "units", "gross_profit", "orders"} <= keys
    names = {d["name"] for d in options["dimensions"]}
    assert {"product_name", "region", "channel", "payment_method"} <= names
    assert "customer_id" not in names


# ── Windows and measures ────────────────────────────────────────────────────

def test_custom_window_requires_all_four_dates(planted):
    dt = pd.to_datetime(planted["order_date"])
    with pytest.raises(MeasureUnavailableError, match="prior_end"):
        resolve_windows(dt, mode="custom", current_start="2025-04-01",
                        current_end="2025-04-30", prior_start="2025-03-01")


def test_auto_window_prefers_the_calendar_month(planted):
    dt = pd.to_datetime(planted["order_date"])
    pair = resolve_windows(dt, mode="auto")
    assert pair.basis == "last_month"
    assert pair.current.label == "April 2025"
    assert pair.prior.label == "March 2025"


def test_revenue_measure_uses_the_shared_line_revenue_definition(planted):
    from agents.analytics.schema_intel import build_schema_summary

    schema = build_schema_summary(planted)
    measure = build_measure(planted, "revenue", schema)
    expected = (planted["unit_price"] * planted["quantity"]).sum()
    assert measure.aggregate() == pytest.approx(expected, rel=1e-9)


# ── Slice identity ──────────────────────────────────────────────────────────

def test_slice_key_is_order_independent():
    a = Slice((SlicePredicate("region", "North"), SlicePredicate("channel", "Email")))
    b = Slice((SlicePredicate("channel", "Email"), SlicePredicate("region", "North")))
    assert a.key() == b.key()


# ── Narration: the second gate ──────────────────────────────────────────────
#
# The citation architecture proves the numbers. It proves nothing about the
# prose, and a draft can be arithmetically perfect and still unusable — a live
# Arabic run came back with a Chinese conjunction inside an otherwise correct
# sentence. These cover the gate that catches that.

def test_alien_script_in_the_draft_is_caught():
    from agents.rootcause.narrate import alien_scripts

    found = alien_scripts("البيانات تشير إلى أن المنطقة تمثل 18.5%،尽管 هذا مرتفع", "")
    assert "CJK" in found
    assert "尽" in found["CJK"]


def test_a_script_that_came_from_the_customers_data_is_not_a_leak():
    """A Chinese supplier name in the source data may legitimately be quoted."""
    from agents.rootcause.narrate import alien_scripts

    source = "EXPLANATION 1: supplier = 中国供应商 accounts for 40% of the change."
    assert alien_scripts("The cause is 中国供应商, which is 40% of the change.", source) == {}


def test_clean_arabic_and_english_drafts_pass_the_gate():
    from agents.rootcause.narrate import alien_scripts

    assert alien_scripts("Revenue fell by 4,000 in May 2025 against April 2025.", "") == {}
    assert alien_scripts("انخفض الإيراد بمقدار 4,000 في مايو 2025 مقارنةً بأبريل 2025.", "") == {}


def test_deterministic_summary_exists_in_arabic_and_types_no_digit(planted):
    """The fallback must be in the language that was asked for.

    Falling back to an English paragraph inside an Arabic briefing trades a
    wrong sentence for a foreign one, which is not an improvement.
    """
    from agents.rootcause.figures import build_registry
    from agents.rootcause.narrate import deterministic_summary

    result = run_root_cause(planted, measure="revenue", with_narrative=False)
    registry = build_registry(result)

    arabic = deterministic_summary(result, registry, "ar")
    english = deterministic_summary(result, registry, "en")

    assert arabic and english and arabic != english
    assert any("\u0600" <= ch <= "\u06ff" for ch in arabic), "Arabic summary is not Arabic"
    # Every figure arrives by substitution, in both languages — the template
    # itself must never contain a literal number.
    for template in (arabic, english):
        outside_tokens = re.sub(r"\{\{[^}]+\}\}", "", template)
        assert not any(ch.isdigit() for ch in outside_tokens), template

    # Both renderings cite the same figures, because they are one arithmetic.
    assert set(re.findall(r"\{\{([^}]+)\}\}", arabic)) == \
           set(re.findall(r"\{\{([^}]+)\}\}", english))
