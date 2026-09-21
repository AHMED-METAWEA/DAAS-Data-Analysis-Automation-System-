"""The briefing as a report a reader is asked to trust.

The claim this makes to a business owner is narrow and specific: *figures are
measured, causes are inferred*. These tests exist because that claim is only
worth making if it survives contact with the awkward cases — a percentage metric
that must not wear a currency symbol, a standing exposure that must not look
like it belongs in the headline total, a model draft that failed verification
and must be disclosed rather than quietly swapped.

The last test is the one that matters most: the phone summary and the full
report are two renderings of one set of figures, and if they can ever disagree
about a number then neither can be trusted.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest

from monitoring.briefing import BriefingContext, compose, format_metric_value
from monitoring.rules import Finding, metric_unit


def _finding(**kw) -> Finding:
    base = dict(
        rule="snapshot_delta", severity="high", metric="revenue",
        title="Revenue fell 5.6%", body="Revenue came in below the previous period.",
        money_at_stake=6656.0, current_value=111744.0, prior_value=118400.0,
        change_pct=-5.6, is_event=True,
    )
    base.update(kw)
    return Finding(**base)


def _ctx(**kw) -> BriefingContext:
    base = dict(
        project_name="Nile Retail", language="en", currency="EGP", row_count=12480,
        data_last_date="2026-08-21", app_link="https://daas.app/p/nile",
        generated_at=datetime(2026, 8, 22, 7, 0, tzinfo=UTC),
        previous_captured_at=datetime(2026, 8, 15, 7, 0, tzinfo=UTC),
    )
    base.update(kw)
    return BriefingContext(**base)


_ROOT_CAUSE = {
    "available": True,
    "current": {"label": "15-21 Aug", "start": "2026-08-15", "end": "2026-08-21",
                "value": 111744.0, "rows": 1840},
    "prior": {"label": "8-14 Aug", "start": "2026-08-08", "end": "2026-08-14",
              "value": 118400.0, "rows": 1902},
    "explanations": [
        {"slice_label": "Alexandria - wholesale", "explanatory_power_pct": 71.0,
         "rows_share_pct": 4.2, "rest_change_pct": -0.8, "robust": True},
        {"slice_label": "returns", "explanatory_power_pct": 12.0,
         "rows_share_pct": 1.1, "robust": False},
    ],
    "stats": {"slices_tested": 1284},
    "narrative": "The drop sits almost entirely in Alexandria wholesale.",
    "verification": {"fell_back_to_deterministic": False},
}


class TestUnits:
    """A figure printed in the wrong unit fails at the one job the figures
    block has — letting the reader check the arithmetic."""

    def test_percentage_metric_never_wears_a_currency_symbol(self) -> None:
        markdown, _ = compose(
            [_finding(metric="gross_margin_pct", current_value=31.4,
                      prior_value=33.1, change_pct=-5.1)],
            _ctx(),
        )
        assert "31.4%" in markdown
        assert "EGP 31.40" not in markdown
        assert "EGP 33.10" not in markdown

    def test_money_metric_keeps_its_currency(self) -> None:
        markdown, _ = compose([_finding()], _ctx())
        assert "EGP 118,400" in markdown and "EGP 111,744" in markdown

    def test_counts_get_no_decimals_and_no_currency(self) -> None:
        assert format_metric_value(1902.0, "orders", "EGP") == "1,902"

    def test_unknown_metric_is_never_assumed_to_be_money(self) -> None:
        # Guessing a currency onto a figure that has none is the expensive
        # direction to be wrong in.
        assert metric_unit("some_new_kpi") == "number"
        assert "EGP" not in format_metric_value(12.5, "some_new_kpi", "EGP")

    def test_unknown_pct_metric_is_inferred_from_its_name(self) -> None:
        assert metric_unit("refund_rate_pct") == "percent"
        assert format_metric_value(4.25, "refund_rate_pct", "EGP") == "4.2%"

    def test_metric_uses_the_same_label_the_rules_use(self) -> None:
        # A metric called two different things in one message reads as two
        # different metrics.
        markdown, _ = compose([_finding(metric="gross_margin_pct")], _ctx())
        assert "Gross margin %" in markdown
        assert "- gross_margin_pct:" not in markdown


class TestBasisAndFigures:
    def test_report_names_the_baseline_it_measured_against(self) -> None:
        markdown, _ = compose([_finding()], _ctx())
        assert "Basis of this report" in markdown
        assert "15 Aug 2026" in markdown
        assert "12,480 records analysed." in markdown

    def test_basis_section_is_omitted_when_there_is_nothing_to_put_in_it(self) -> None:
        markdown, _ = compose(
            [_finding()],
            _ctx(previous_captured_at=None, data_last_date=None, row_count=0, currency=None),
        )
        assert "Basis of this report" not in markdown

    def test_figures_show_prior_current_and_change(self) -> None:
        markdown, _ = compose([_finding()], _ctx())
        assert "Revenue: EGP 118,400 → EGP 111,744 (-5.6%)" in markdown

    def test_findings_without_measurements_are_skipped_not_faked(self) -> None:
        markdown, _ = compose(
            [_finding(current_value=None, prior_value=None, change_pct=None)], _ctx()
        )
        assert "The figures" not in markdown

    def test_comparison_windows_are_named_with_their_row_counts(self) -> None:
        markdown, _ = compose([_finding()], _ctx(root_cause=_ROOT_CAUSE))
        assert "15-21 Aug (1,840 rows) vs 8-14 Aug (1,902 rows)." in markdown


class TestExposuresVersusEvents:
    def test_exposure_is_labelled_so_it_cannot_be_read_as_part_of_the_total(self) -> None:
        # The headline total deliberately excludes exposures. An unlabelled
        # "EGP 322,000" under "Money at stake: EGP 6,656" reads as an
        # arithmetic error in the report rather than as two different things.
        markdown, _ = compose(
            [
                _finding(),
                _finding(rule="concentration", severity="medium",
                         title="Half of revenue comes from 10 customers",
                         money_at_stake=322000.0, is_event=False,
                         current_value=None, prior_value=None, change_pct=None),
            ],
            _ctx(),
        )
        assert "Money at stake: EGP 6,656." in markdown
        assert "not part of the total above" in markdown

    def test_event_amounts_stay_plain(self) -> None:
        markdown, _ = compose(
            [_finding(), _finding(title="Margin missed target", money_at_stake=2100.0)],
            _ctx(),
        )
        assert "— EGP 2,100" in markdown
        assert "EGP 2,100 exposed" not in markdown


class TestAssurance:
    def test_states_that_no_figure_was_written_by_a_model(self) -> None:
        markdown, _ = compose([_finding()], _ctx(root_cause=_ROOT_CAUSE))
        assert "No figure in this report is written by a language model." in markdown

    def test_reports_the_robustness_of_its_own_explanations(self) -> None:
        markdown, _ = compose([_finding()], _ctx(root_cause=_ROOT_CAUSE))
        assert "1 of 2 candidate explanations remain significant" in markdown
        assert "1,284 slices tested" in markdown

    def test_separates_measured_figures_from_inferred_causes(self) -> None:
        # The honest version of "100% accurate": say which half is provable.
        markdown, _ = compose([_finding()], _ctx(root_cause=_ROOT_CAUSE))
        assert "Figures are measured. Causes are inferred" in markdown

    def test_discloses_when_the_models_draft_was_rejected(self) -> None:
        rejected = {**_ROOT_CAUSE, "verification": {"fell_back_to_deterministic": True}}
        markdown, _ = compose([_finding()], _ctx(root_cause=rejected))
        assert "did not pass that check" in markdown

    def test_does_not_claim_verification_when_there_was_no_narrative(self) -> None:
        # Claiming to have checked prose that never existed is exactly the kind
        # of unearned assurance this section is meant to avoid.
        no_narrative = {**_ROOT_CAUSE, "narrative": ""}
        markdown, _ = compose([_finding()], _ctx(root_cause=no_narrative))
        assert "was checked against those same figures" not in markdown

    def test_quiet_briefing_carries_no_assurance_block(self) -> None:
        markdown, _ = compose([], _ctx())
        assert "How this report was produced" not in markdown


class TestShortChannelSummary:
    def test_summary_is_a_summary_not_a_truncated_report(self) -> None:
        markdown, message = compose([_finding()], _ctx(root_cause=_ROOT_CAUSE))
        assert len(message.text) < len(markdown)
        # The evidence sections sit lowest in the report and would be the first
        # thing a 4096-char truncation removed.
        assert "How this report was produced" not in message.text
        assert "Basis of this report" not in message.text

    def test_summary_still_carries_the_lead_figure_and_a_link(self) -> None:
        _, message = compose([_finding()], _ctx(root_cause=_ROOT_CAUSE))
        assert "EGP 118,400" in message.text and "EGP 111,744" in message.text
        assert "https://daas.app/p/nile" in message.text

    def test_summary_fits_the_strictest_channel_limit_with_room_to_spare(self) -> None:
        findings = [_finding()] + [
            _finding(title=f"Secondary finding number {i}", money_at_stake=100.0 * i)
            for i in range(1, 6)
        ]
        _, message = compose(findings, _ctx(root_cause=_ROOT_CAUSE))
        # 4096 is the WhatsApp/Telegram cap; for_length would cut mid-evidence.
        assert len(message.text) < 4096
        assert message.for_length(4096) == message.text

    def test_summary_and_report_never_state_different_numbers(self) -> None:
        """Two renderings, one set of figures.

        If the phone summary and the emailed report could ever disagree, a
        reader who saw both would be right to trust neither — and that is the
        whole proposition here.
        """
        markdown, message = compose(
            [_finding(), _finding(metric="gross_margin_pct", current_value=31.4,
                                  prior_value=33.1, change_pct=-5.1,
                                  title="Margin slipped", money_at_stake=2100.0)],
            _ctx(root_cause=_ROOT_CAUSE),
        )
        money = re.compile(r"EGP [\d,]+(?:\.\d+)?")
        in_summary = set(money.findall(message.text))
        in_report = set(money.findall(markdown))
        assert in_summary, "summary should carry at least one figure"
        assert in_summary <= in_report, (
            f"summary states figures the report does not: {in_summary - in_report}"
        )


class TestArabic:
    @pytest.mark.parametrize("key", [
        "أساس هذا التقرير",   # basis heading
        "الأرقام",            # figures heading
        "كيف أُعدّ هذا التقرير",  # method heading
    ])
    def test_new_sections_are_composed_in_arabic(self, key: str) -> None:
        markdown, _ = compose(
            [_finding()], _ctx(language="ar", root_cause=_ROOT_CAUSE),
        )
        assert key in markdown

    def test_arabic_report_has_no_english_section_headings(self) -> None:
        markdown, _ = compose([_finding()], _ctx(language="ar", root_cause=_ROOT_CAUSE))
        for english in ("Basis of this report", "The figures", "How this report was produced"):
            assert english not in markdown
