"""The deterministic cleaning operators and the measuring apply engine.

These are the tests that let the pipeline claim its cleaning is correct rather
than plausible: every operator is a pure function with a fixed contract, so it
can be pinned down here instead of being re-derived by a language model on
every run.
"""

from __future__ import annotations

import pandas as pd
import pytest

from models.cleaning_ops import CleaningStep
from tools.cleaning_ops import (
    REGISTRY,
    apply_plan,
    apply_step,
    category_key,
    infer_dayfirst,
    null_token_mask,
    order_steps,
    parse_boolean_series,
    parse_datetime_series,
    parse_numeric_series,
    validate_step,
)


def _step(op: str, columns=None, params=None, sid="1") -> CleaningStep:
    return CleaningStep(id=sid, op=op, columns=columns or [], params=params or {}, description=op)


class TestParseNumeric:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("$1,299.00", 1299.0),
            ("1,200 EGP", 1200.0),
            ("EGP 1,200", 1200.0),
            ("(1,234)", -1234.0),          # accounting-style negative
            ("45%", 0.45),
            ("−50", -50.0),           # Unicode minus
            (" 7 ", 7.0),
            ("-3.5", -3.5),
            ("1e3", 1000.0),
        ],
    )
    def test_parses_real_world_money_formats(self, raw, expected) -> None:
        assert parse_numeric_series(pd.Series([raw]))[0] == expected

    @pytest.mark.parametrize("raw", ["A1", "SKU-123", "2024-01-05", "abc", "12.5.6", ""])
    def test_refuses_to_salvage_digits_out_of_non_numbers(self, raw) -> None:
        """An identifier or a date must never become a number. Stripping
        'everything that is not a digit' turns "A1" into 1 and "2024-01-05"
        into 20240105 — silently inventing values that were never in the data.
        """
        assert pd.isna(parse_numeric_series(pd.Series([raw]))[0])

    def test_european_decimal_convention_is_inferred_per_column(self) -> None:
        assert parse_numeric_series(
            pd.Series(["1.299,00", "2.450,50", "99,99"])
        ).tolist() == [1299.0, 2450.5, 99.99]

    def test_anglo_decimal_convention_is_inferred_per_column(self) -> None:
        assert parse_numeric_series(
            pd.Series(["1,299.00", "2,450.50", "99.99"])
        ).tolist() == [1299.0, 2450.5, 99.99]

    def test_unparseable_values_become_null_not_zero(self) -> None:
        out = parse_numeric_series(pd.Series(["10", "oops", "30"]))
        assert out.tolist()[0] == 10.0
        assert pd.isna(out[1])
        assert out.tolist()[2] == 30.0


class TestParseDatetime:
    def test_iso_dates_are_never_reinterpreted_by_dayfirst(self) -> None:
        """dayfirst is inferred from slash-style values in the column. Applying
        it to an ISO string turns 2024-01-05 into 1 May, moving up to 11/12 of
        rows into the wrong month with nothing downstream able to notice."""
        out = parse_datetime_series(
            pd.Series(["2024-01-05", "2024/03/07", "15/03/2024"])
        )
        assert out[0] == pd.Timestamp("2024-01-05")
        assert out[1] == pd.Timestamp("2024-03-07")
        assert out[2] == pd.Timestamp("2024-03-15")

    def test_day_first_column_is_read_day_first(self) -> None:
        out = parse_datetime_series(pd.Series(["13/01/2024", "05/02/2024"]))
        assert out.tolist() == [pd.Timestamp("2024-01-13"), pd.Timestamp("2024-02-05")]

    def test_month_first_column_is_read_month_first(self) -> None:
        out = parse_datetime_series(pd.Series(["01/13/2024", "02/05/2024"]))
        assert out.tolist() == [pd.Timestamp("2024-01-13"), pd.Timestamp("2024-02-05")]

    def test_genuinely_ambiguous_column_is_reported_not_guessed(self) -> None:
        assert infer_dayfirst(pd.Series(["01/02/2024", "03/04/2024"])) is None

    def test_excel_serial_numbers_are_recognised(self) -> None:
        assert parse_datetime_series(pd.Series([45231, 45300]))[0] == pd.Timestamp("2023-11-01")

    def test_epoch_seconds_are_recognised(self) -> None:
        assert parse_datetime_series(pd.Series([1710000000, 1720000000]))[0].year == 2024

    def test_unparseable_dates_become_nat_and_are_never_filled(self) -> None:
        out = parse_datetime_series(pd.Series(["2024-01-05", "not a date", "2024-01-07"]))
        assert pd.isna(out[1])


class TestNullTokens:
    def test_placeholders_are_found_in_mixed_dtype_columns(self) -> None:
        """The previous implementation gated on is_string_dtype, which is False
        for a column mixing strings and numbers — precisely where placeholders
        hide in a real file."""
        mask = null_token_mask(pd.Series(["UNKNOWN", 5, 3, "N/A", 7, None]))
        assert mask.tolist() == [True, False, False, True, False, False]

    def test_arabic_placeholders_are_recognised(self) -> None:
        assert null_token_mask(pd.Series(["غير معروف", "القاهرة"])).tolist() == [True, False]

    def test_real_values_are_not_treated_as_placeholders(self) -> None:
        assert not null_token_mask(pd.Series(["Cairo", "0", "false"])).any()


class TestCategoryHarmonisation:
    def test_case_and_spacing_variants_share_a_key(self) -> None:
        assert category_key("Cairo") == category_key("cairo ") == category_key("CAIRO")

    def test_genuinely_different_values_do_not(self) -> None:
        assert category_key("Cairo") != category_key("Giza")

    def test_variants_fold_onto_the_most_common_spelling(self) -> None:
        df = pd.DataFrame({"city": ["Cairo", "Cairo", "cairo ", "CAIRO", "Giza"]})
        out, ledger = apply_step(df, _step("harmonize_categories", ["city"]))
        assert out["city"].tolist() == ["Cairo", "Cairo", "Cairo", "Cairo", "Giza"]
        assert ledger.cells_changed == 2


class TestBooleanParsing:
    def test_common_flag_spellings(self) -> None:
        out = parse_boolean_series(pd.Series(["Y", "no", 1, "TRUE", "maybe", None]))
        assert out.tolist()[:4] == [True, False, True, True]
        assert pd.isna(out[4]) and pd.isna(out[5])


class TestImputeGuardrails:
    def test_fills_a_few_missing_values(self) -> None:
        df = pd.DataFrame({"a": [1.0, None, 3.0]})
        out, ledger = apply_step(df, _step("impute", ["a"], {"strategy": "median"}))
        assert out["a"].tolist() == [1.0, 2.0, 3.0]
        assert ledger.nulls_filled == 1

    def test_refuses_a_mostly_empty_column(self) -> None:
        """Filling 80% of a column manufactures the column rather than cleaning
        it, and every downstream average would be a statement about the fill
        value, not about the business."""
        df = pd.DataFrame({"a": [1.0, None, None, None, None]})
        out, ledger = apply_step(df, _step("impute", ["a"], {"strategy": "median"}))
        assert out["a"].isna().sum() == 4
        assert ledger.cells_changed == 0


class TestFlaggingNeverRewrites:
    def test_flag_outliers_leaves_values_untouched(self) -> None:
        df = pd.DataFrame({"amount": [10.0, 11.0, 12.0, 9.0, 10.5, 11.5, 10.2, 5000.0]})
        out, ledger = apply_step(df, _step("flag_outliers", ["amount"], {"factor": 1.5}))
        assert out["amount"].tolist() == df["amount"].tolist()
        assert out["amount__is_outlier"].tolist()[-1] is True or out["amount__is_outlier"].iloc[-1]
        assert ledger.cells_changed == 0

    def test_flag_range_violation_marks_negatives_without_changing_them(self) -> None:
        df = pd.DataFrame({"qty": [1, -5, 3]})
        out, _ = apply_step(df, _step("flag_range_violation", ["qty"], {"min": 0.0}))
        assert out["qty"].tolist() == [1, -5, 3]
        assert out["qty__out_of_range"].tolist() == [False, True, False]

    def test_flag_arithmetic_violation_finds_the_bad_row(self) -> None:
        df = pd.DataFrame({
            "qty": [2, 3, 1, 4],
            "price": [10.0, 20.0, 30.0, 5.0],
            "total": [20.0, 60.0, 30.0, 999.0],
        })
        out, _ = apply_step(df, _step(
            "flag_arithmetic_violation", ["total"],
            {"left": "qty", "right": "price", "result": "total", "operator": "*"},
        ))
        assert out["total__mismatch"].tolist() == [False, False, False, True]


class TestCastInteger:
    def test_whole_number_floats_become_integers(self) -> None:
        df = pd.DataFrame({"order_id": [1001.0, 1002.0]})
        out, _ = apply_step(df, _step("cast_integer", ["order_id"]))
        assert str(out["order_id"].dtype) == "Int64"
        assert out["order_id"].tolist() == [1001, 1002]

    def test_genuine_decimals_are_left_alone(self) -> None:
        df = pd.DataFrame({"price": [10.5, 20.25]})
        out, _ = apply_step(df, _step("cast_integer", ["price"]))
        assert out["price"].tolist() == [10.5, 20.25]


class TestKeyColumnProtection:
    def test_representation_changing_ops_are_skipped_on_a_join_key(self) -> None:
        df = pd.DataFrame({"customer_id": ["C1", "c2 ", "C3"]})
        out, ledger = apply_step(
            df, _step("harmonize_case", ["customer_id"], {"style": "lower"}),
            protected_columns={"customer_id"},
        )
        assert out["customer_id"].tolist() == ["C1", "c2 ", "C3"]
        assert ledger.applied is False
        assert "join key" in ledger.skipped_reason

    def test_whitespace_trimming_is_still_allowed_on_a_join_key(self) -> None:
        df = pd.DataFrame({"customer_id": ["C1", " C2 "]})
        out, ledger = apply_step(
            df, _step("trim_whitespace", ["customer_id"]), protected_columns={"customer_id"},
        )
        assert out["customer_id"].tolist() == ["C1", "C2"]
        assert ledger.applied is True


class TestApplyStepMeasurement:
    def test_ledger_counts_come_from_diffing_not_from_the_operator(self) -> None:
        df = pd.DataFrame({"a": [1.0, None, 3.0], "b": ["x", "y", "z"]})
        out, ledger = apply_step(df, _step("impute", ["a"], {"strategy": "mean"}))
        assert ledger.nulls_filled == 1
        assert ledger.cells_changed == 1
        assert ledger.rows_removed == 0
        assert ledger.samples and ledger.samples[0].column == "a"

    def test_a_raising_operator_is_recorded_and_returns_the_input_untouched(self) -> None:
        def _boom(df, columns, params):
            raise ValueError("operator bug")

        original = REGISTRY["trim_whitespace"]
        REGISTRY["trim_whitespace"] = (original[0], _boom)
        try:
            df = pd.DataFrame({"a": [" x "]})
            out, ledger = apply_step(df, _step("trim_whitespace", ["a"]))
            assert out["a"].tolist() == [" x "]
            assert ledger.applied is False
            assert "operator bug" in ledger.error
        finally:
            REGISTRY["trim_whitespace"] = original

    def test_an_operator_that_reorders_rows_is_rejected(self) -> None:
        """Row order is part of the contract: every cell-level comparison in the
        ledger aligns on the index, so a reordering operator would have its
        changes measured against the wrong rows."""
        def _reorder(df, columns, params):
            return df.sort_values("a")

        original = REGISTRY["trim_whitespace"]
        REGISTRY["trim_whitespace"] = (original[0], _reorder)
        try:
            df = pd.DataFrame({"a": [3, 1, 2]})
            out, ledger = apply_step(df, _step("trim_whitespace", ["a"]))
            assert ledger.applied is False
            assert "row-identity" in ledger.error
            assert out["a"].tolist() == [3, 1, 2]
        finally:
            REGISTRY["trim_whitespace"] = original

    def test_an_operator_that_invents_rows_is_rejected(self) -> None:
        def _duplicate(df, columns, params):
            return pd.concat([df, df], ignore_index=True)

        original = REGISTRY["trim_whitespace"]
        REGISTRY["trim_whitespace"] = (original[0], _duplicate)
        try:
            df = pd.DataFrame({"a": [1, 2]})
            out, ledger = apply_step(df, _step("trim_whitespace", ["a"]))
            assert ledger.applied is False
            assert len(out) == 2
        finally:
            REGISTRY["trim_whitespace"] = original


class TestStepValidation:
    def test_unknown_operator_is_rejected(self) -> None:
        assert "unknown operator" in validate_step(_step("teleport", ["a"]))

    def test_unknown_parameter_is_rejected(self) -> None:
        error = validate_step(_step("impute", ["a"], {"strategy": "median", "wat": 1}))
        assert "unknown parameter" in error

    def test_bad_choice_is_rejected(self) -> None:
        error = validate_step(_step("impute", ["a"], {"strategy": "vibes"}))
        assert "must be one of" in error

    def test_missing_required_parameter_is_rejected(self) -> None:
        assert "requires parameter" in validate_step(_step("impute", ["a"], {}))

    def test_operator_needing_a_column_is_rejected_without_one(self) -> None:
        assert "requires at least one target column" in validate_step(_step("parse_numeric", []))


class TestOrdering:
    def test_dedupe_is_ordered_before_imputation(self) -> None:
        """Imputing first manufactures duplicates that were never in the source
        and the dedupe step then deletes genuinely distinct rows."""
        ordered = order_steps([
            _step("impute", ["a"], {"strategy": "median"}, sid="1"),
            _step("drop_duplicate_rows", sid="2"),
        ])
        assert [s.op for s in ordered] == ["drop_duplicate_rows", "impute"]

    def test_parsing_is_ordered_before_anything_that_reads_the_values(self) -> None:
        ordered = order_steps([
            _step("flag_outliers", ["price"], sid="1"),
            _step("parse_numeric", ["price"], sid="2"),
            _step("standardize_null_placeholders", ["price"], sid="3"),
        ])
        assert [s.op for s in ordered] == [
            "standardize_null_placeholders", "parse_numeric", "flag_outliers",
        ]

    def test_free_text_steps_run_last(self) -> None:
        ordered = order_steps([
            CleaningStep(id="1", description="bespoke user rule"),
            _step("parse_numeric", ["price"], sid="2"),
        ])
        assert ordered[-1].is_typed is False


class TestApplyPlan:
    def test_end_to_end_on_a_filthy_table(self) -> None:
        df = pd.DataFrame({
            "order_id": ["A1", "A2", "A2"],
            "city": ["Cairo", "cairo ", "cairo "],
            "price": ["$10.00", "$20.00", "$20.00"],
        })
        plan = [
            _step("drop_duplicate_rows", sid="1"),
            _step("trim_whitespace", ["city"], sid="2"),
            _step("parse_numeric", ["price"], sid="3"),
            _step("harmonize_categories", ["city"], sid="4"),
        ]
        out, ledger = apply_plan(df, order_steps(plan))

        assert len(out) == 2
        assert out["price"].tolist() == [10.0, 20.0]
        assert out["city"].nunique() == 1
        assert ledger.rows_before == 3 and ledger.rows_after == 2
        assert all(line for line in ledger.as_log())

    def test_log_lines_are_derived_from_measured_counts(self) -> None:
        df = pd.DataFrame({"a": [1.0, None, 3.0]})
        _, ledger = apply_plan(df, [_step("impute", ["a"], {"strategy": "median"})])
        assert ledger.as_log() == ["impute on a: filled 1 missing value(s)"]
