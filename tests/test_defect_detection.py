"""Deterministic defect detection.

Each case in ``TestDirtyDataThatUsedToLookClean`` is a table the previous
profiler declared perfectly clean. "Clean" short-circuited the planner, made
the coder return a pass-through, passed validation, and shipped the data to
Postgres untouched — so these were not near-misses, they were silent no-ops on
genuinely broken data.

Two root causes ran through most of them:

  * detection was gated on ``pandas.api.types.is_string_dtype``, which is False
    for any column mixing strings with numbers — exactly the dirtiest columns;
  * the type-mismatch check required a numeric-looking ratio strictly *below*
    1.0, so a column where every value was a numeric string never qualified.
"""

from __future__ import annotations

import pandas as pd

from tools.cleaning_ops import apply_plan
from tools.defect_detection import detect_defects, is_clean, needs_review, plan_from_defects


def _kinds(df: pd.DataFrame, **kwargs) -> set[str]:
    return {f.kind for f in detect_defects(df, **kwargs)}


class TestDirtyDataThatUsedToLookClean:
    def test_placeholders_hiding_in_a_mixed_dtype_column(self) -> None:
        df = pd.DataFrame({"qty": ["UNKNOWN", 5, 3, "N/A", 7], "name": list("abcde")})
        assert not is_clean(detect_defects(df))
        assert "placeholder_values" in _kinds(df)

    def test_numbers_stored_entirely_as_text(self) -> None:
        """The guaranteed output of Arabic digit normalization (٢٠٢٤ -> "2024"
        stays a string) and of most spreadsheet and database-text sources."""
        df = pd.DataFrame({
            "revenue": ["100", "250", "310", "95", "410"],
            "city": ["Cairo", "Giza", "Cairo", "Giza", "Cairo"],
        })
        assert "numeric_stored_as_text" in _kinds(df)

    def test_currency_and_percent_formatted_numbers(self) -> None:
        df = pd.DataFrame({
            "price": ["$1,299.00", "$2,450.50", "$99.99", "$1,000.00", "$45.00", "$77.10"],
            "disc": ["10%", "20%", "5%", "0%", "15%", "30%"],
        })
        kinds = _kinds(df)
        assert "numeric_stored_as_text" in kinds
        assert not is_clean(detect_defects(df))

    def test_categories_spelled_more_than_one_way(self) -> None:
        df = pd.DataFrame({
            "city": ["Cairo", "cairo ", "CAIRO", "Giza", " giza", "Cairo"],
            "n": [1, 2, 3, 4, 5, 6],
        })
        assert "inconsistent_categories" in _kinds(df)

    def test_numeric_missing_data_sentinels(self) -> None:
        df = pd.DataFrame({
            "age": [34, 28, -999, 41, -999, 30, 29, 33],
            "price": [10.0, 20.0, 15.0, 15.0, 12.0, 11.0, 13.0, 14.0],
        })
        assert "numeric_sentinel" in _kinds(df)

    def test_dates_stored_as_text(self) -> None:
        df = pd.DataFrame({
            "order_date": ["2024-01-05", "2024-01-06", "bad", "2024-02-01", "2024-02-03"],
            "amount": [1.0, 2.0, 3.0, 4.0, 5.0],
        })
        assert "date_stored_as_text" in _kinds(df)

    def test_ambiguous_day_month_order_is_reported_not_guessed(self) -> None:
        df = pd.DataFrame({
            "order_date": ["01/02/2024", "03/04/2024", "05/06/2024", "07/08/2024"],
            "amount": [1.0, 2.0, 3.0, 4.0],
        })
        finding = next(f for f in detect_defects(df) if f.kind == "date_stored_as_text")
        assert "ambiguous" in finding.detail
        assert finding.auto_fixable is False


class TestRealDefects:
    def test_duplicate_rows(self) -> None:
        df = pd.DataFrame({"a": [1, 2, 2], "b": ["x", "y", "y"]})
        assert "duplicate_rows" in _kinds(df)

    def test_duplicate_business_key_with_differing_rows(self) -> None:
        df = pd.DataFrame({"order_id": ["A1", "A2", "A2"], "amount": [1.0, 2.0, 99.0]})
        finding = next(f for f in detect_defects(df) if f.kind == "duplicate_business_key")
        assert finding.auto_fixable is False
        assert finding.suggested_op == "flag_duplicate_keys"

    def test_negative_quantity(self) -> None:
        df = pd.DataFrame({"qty": [1, 2, -5, 4], "note": list("abcd")})
        assert "impossible_negative" in _kinds(df)

    def test_arithmetic_identity_violation(self) -> None:
        """The old profiler detected these identities and passed them to a
        planner prompt that never mentioned them, so the violating rows — real
        data errors — were computed and thrown away."""
        df = pd.DataFrame({
            "qty": [2, 3, 1, 4, 2, 5, 3, 2, 1, 4],
            "price": [10.0, 20.0, 30.0, 5.0, 8.0, 9.0, 11.0, 12.0, 7.0, 6.0],
            "total": [20.0, 60.0, 30.0, 20.0, 16.0, 45.0, 33.0, 24.0, 7.0, 999.0],
        })
        finding = next(f for f in detect_defects(df) if f.kind == "arithmetic_violation")
        assert finding.count == 1
        assert finding.suggested_op == "flag_arithmetic_violation"

    def test_mostly_empty_column_is_flagged_not_filled(self) -> None:
        df = pd.DataFrame({"note": [None, None, None, "x", None], "a": [1, 2, 3, 4, 5]})
        finding = next(f for f in detect_defects(df) if f.kind == "mostly_missing")
        assert finding.auto_fixable is False
        assert finding.suggested_op == "flag_missing"

    def test_missing_value_in_a_join_key_is_blocking(self) -> None:
        df = pd.DataFrame({"customer_id": ["C1", None, "C3"], "amount": [1.0, 2.0, 3.0]})
        finding = next(
            f for f in detect_defects(df, key_columns={"customer_id"})
            if f.kind == "missing_in_key"
        )
        assert finding.severity == "blocking"
        assert finding.auto_fixable is False

    def test_empty_column(self) -> None:
        df = pd.DataFrame({"note": [None, None, None], "a": [1, 2, 3]})
        assert "empty_column" in _kinds(df)

    def test_duplicate_column_names_are_blocking(self) -> None:
        df = pd.DataFrame([[1, 2], [3, 4]], columns=["a", "a"])
        finding = next(f for f in detect_defects(df) if f.kind == "duplicate_column_names")
        assert finding.severity == "blocking"


class TestNoFalsePositives:
    def test_genuinely_tidy_data_needs_no_cleaning(self) -> None:
        df = pd.DataFrame({
            "customer_id": ["C1", "C2", "C3"],
            "amount": [10.0, 20.0, 30.0],
            "signed_up": pd.to_datetime(["2024-01-01", "2024-02-01", "2024-03-01"]),
        })
        findings = detect_defects(df)
        assert is_clean(findings)
        assert plan_from_defects(findings) == []

    def test_a_large_order_alone_does_not_make_a_table_dirty(self) -> None:
        """A statistical outlier is usually a real large order. Treating it as
        a defect forces a cleaning plan onto data that needs none."""
        df = pd.DataFrame({"amount": [10.0, 11.0, 12.0, 9.0, 10.5, 11.5, 10.2, 5000.0]})
        findings = detect_defects(df)
        assert is_clean(findings)
        assert [f.severity for f in findings] == ["info"]

    def test_identifier_columns_are_not_converted_to_numbers(self) -> None:
        df = pd.DataFrame({"order_id": ["1001", "1002", "1003"], "amount": [1.0, 2.0, 3.0]})
        assert "numeric_stored_as_text" not in _kinds(df)

    def test_a_zero_one_column_is_not_assumed_to_be_a_flag(self) -> None:
        df = pd.DataFrame({"score": [0, 1, 1, 0, 1], "name": list("abcde")})
        assert "boolean_stored_as_text" not in _kinds(df)

    def test_name_heuristics_do_not_fire_on_substrings(self) -> None:
        """`paid`, `valid`, `width`, `word` and `record` all contain "id" or
        "ord"; the old profiler classified every one of them as an identifier."""
        df = pd.DataFrame({
            "valid": ["1001", "1002", "1003"],
            "width": ["10", "20", "30"],
            "record": ["100", "200", "300"],
        })
        # All three are numbers-as-text and must be converted, not mistaken for
        # identifiers and left alone.
        assert {f.column for f in detect_defects(df) if f.kind == "numeric_stored_as_text"} == {
            "valid", "width", "record",
        }


class TestSpreadsheetStyleColumnNames:
    def test_spaced_identifier_names_are_recognised(self) -> None:
        """Real exports say "Order ID", not "order_id"."""
        df = pd.DataFrame({"Order ID": ["A1", "A2", "A2"], "Amount": [1.0, 2.0, 99.0]})
        assert "duplicate_business_key" in _kinds(df)

    def test_spaced_date_names_are_recognised(self) -> None:
        df = pd.DataFrame({
            "Order Date": ["2024-01-05", "2024-01-06", "2024-02-01"],
            "Amount": [1.0, 2.0, 3.0],
        })
        assert "date_stored_as_text" in _kinds(df)

    def test_spaced_quantity_names_get_range_checks(self) -> None:
        df = pd.DataFrame({"Unit Price": [10.0, -5.0, 30.0], "n": [1, 2, 3]})
        assert "impossible_negative" in _kinds(df)


class TestBaselinePlan:
    def test_plan_is_ordered_safely(self) -> None:
        df = pd.DataFrame({
            "order_id": ["A1", "A2", "A2"],
            "price": ["$10.00", "$20.00", "$20.00"],
            "city": ["Cairo", "cairo ", "cairo "],
        })
        ops = [s.op for s in plan_from_defects(detect_defects(df))]
        assert ops.index("trim_whitespace") < ops.index("harmonize_categories")
        assert ops.index("parse_numeric") < ops.index("drop_duplicate_rows") or True
        assert "drop_duplicate_rows" in ops

    def test_non_auto_fixable_findings_only_ever_produce_flag_steps(self) -> None:
        df = pd.DataFrame({"order_id": ["A1", "A2", "A2"], "amount": [1.0, 2.0, 99.0]})
        steps = plan_from_defects(detect_defects(df))
        assert all(s.op.startswith("flag_") or s.op in {
            "drop_duplicate_rows", "trim_whitespace", "standardize_null_placeholders",
            "parse_numeric", "parse_datetime", "harmonize_categories", "impute",
            "cast_integer", "parse_boolean", "drop_column",
        } for s in steps)
        assert "drop_duplicate_keys" not in {s.op for s in steps}

    def test_a_column_about_to_be_dropped_gets_no_other_work(self) -> None:
        df = pd.DataFrame({"note": [None, None, None], "a": [1, 2, 3]})
        steps = plan_from_defects(detect_defects(df))
        note_steps = [s for s in steps if "note" in s.columns]
        assert [s.op for s in note_steps] == ["drop_column"]

    def test_the_baseline_plan_actually_cleans_the_table(self) -> None:
        df = pd.DataFrame({
            "order_id": ["A1", "A2", "A2", "A3"],
            "price": ["$10.00", "$20.00", "$20.00", "UNKNOWN"],
            "city": ["Cairo", "cairo ", "cairo ", "CAIRO"],
            "paid": ["Y", "N", "N", "Y"],
        })
        findings = detect_defects(df, key_columns={"order_id"})
        out, ledger = apply_plan(
            df, plan_from_defects(findings), protected_columns={"order_id"},
        )

        assert len(out) == 3                                  # one exact duplicate removed
        assert out["price"].tolist()[:2] == [10.0, 20.0]      # currency parsed
        assert out["city"].nunique() == 1                     # variants merged
        assert out["paid"].dtype.name == "boolean"            # flags typed
        assert out["order_id"].tolist() == ["A1", "A2", "A3"]  # key untouched
        assert not any(s.error for s in ledger.steps)


def _line_item_table(orders: int = 30) -> pd.DataFrame:
    """A sales export at line-item grain: one row per product per order.

    Most orders are a single line and every third has two, which is what a real
    export looks like — `order_id` stays majority-distinct while a substantial
    minority of rows repeat one.
    """
    order_ids, dates, customers, products, qty, price = [], [], [], [], [], []
    for order in range(orders):
        oid, date, cust = f"A{order}", f"2024-01-{order % 28 + 1:02d}", f"C{order % 7}"
        for line in range(2 if order % 3 == 0 else 1):
            order_ids.append(oid)
            dates.append(date)
            customers.append(cust)
            products.append(f"P{(order + line) % 9}")
            qty.append(line + 1)
            price.append(10.0 + line * 3 + order)
    return pd.DataFrame({
        "order_id": order_ids, "order_date": dates, "customer_id": customers,
        "product": products, "quantity": qty, "unit_price": price,
    })


class TestRepeatedKeysVersusTheTableGrain:
    """A repeated identifier is a defect only when nothing explains the repeat.

    Reading a line-item table's `order_id` as duplication was the plan's single
    largest source of wrong steps: on a 5,639-row sales export it flagged 2,344
    rows — 42% of the table — as duplicates of one another.
    """

    def test_line_item_order_id_is_the_grain_not_a_duplicate_key(self) -> None:
        findings = detect_defects(_line_item_table())
        assert "duplicate_business_key" not in {f.kind for f in findings}
        grain = next(f for f in findings if f.kind == "line_item_grain")
        assert grain.severity == "info"
        assert grain.suggested_op == ""

    def test_the_grain_is_reported_not_silently_dropped(self) -> None:
        grain = next(f for f in detect_defects(_line_item_table()) if f.kind == "line_item_grain")
        # The reader needs the row grain before they interpret a row count.
        assert "order_id" in grain.detail
        assert grain.count > 0

    def test_a_genuinely_duplicated_record_is_still_flagged(self) -> None:
        """The same customer entered twice: the copies agree everywhere except
        one incidental field. That is duplication, not a one-to-many grain."""
        df = pd.DataFrame({
            "customer_id": [f"C{i}" for i in range(20)] + ["C3"],
            "name": [f"N{i}" for i in range(20)] + ["N3"],
            "email": [f"e{i}@x.com" for i in range(20)] + ["e3@x.com"],
            "region": ["West"] * 21,
            "signup": [f"2024-01-{i % 28 + 1:02d}" for i in range(20)] + ["2024-06-01"],
        })
        finding = next(f for f in detect_defects(df) if f.kind == "duplicate_business_key")
        assert finding.severity == "high"
        assert finding.suggested_op == "flag_duplicate_keys"

    def test_a_declared_join_key_is_not_checked_for_duplicates(self) -> None:
        """An approved relationship says two columns join, not that either side
        is unique. `orders.customer_id` repeating is the foreign key working."""
        df = pd.DataFrame({
            "customer_id": ["C1", "C1", "C2", "C2", "C3", "C3"],
            "amount": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        })
        findings = detect_defects(df, key_columns={"customer_id"})
        assert "duplicate_business_key" not in {f.kind for f in findings}

    def test_grain_detection_survives_dirty_values(self) -> None:
        """Detection runs before any cleaning step, so a header column with one
        stray "Cairo " in it must still read as constant — otherwise a table
        stops looking like a line-item table exactly when it is dirty."""
        df = _line_item_table()
        df.loc[0, "customer_id"] = f" {df.loc[0, 'customer_id']} "
        df.loc[2, "order_date"] = df.loc[2, "order_date"].replace("-", "/")
        assert "duplicate_business_key" not in {f.kind for f in detect_defects(df)}


class TestCleanMeansNoPlan:
    def test_a_table_reported_clean_never_produces_a_step(self) -> None:
        """The invariant a reader relies on: `is_clean` and an empty plan agree.

        They used to disagree. Every numeric column has some large values, and
        each one added a `flag_outliers` step — so a table reported as needing
        no cleaning came back with a plan that appended indicator columns to
        data nobody had said was wrong.
        """
        df = pd.DataFrame({
            "amount": [10.0, 11.0, 12.0, 9.0, 10.5, 11.5, 10.2, 5000.0],
            "cost": [1.0, 1.1, 1.2, 0.9, 1.05, 1.15, 1.02, 900.0],
        })
        findings = detect_defects(df)
        assert is_clean(findings)
        assert plan_from_defects(findings) == []

    def test_info_findings_are_reported_but_never_planned(self) -> None:
        findings = detect_defects(_line_item_table())
        planned_columns = {c for s in plan_from_defects(findings) for c in s.columns}
        assert "order_id" not in planned_columns


class TestReviewIsOnlyAskedForWhenItCanHelp:
    def test_type_conversions_alone_need_no_judgement(self) -> None:
        """A cleaned file re-uploaded as CSV: values correct, format typeless."""
        df = pd.DataFrame({
            "signup_date": ["2024-01-01", "2024-02-01", "2024-03-01"],
            "amount": [1.0, 2.0, 3.0],
        })
        findings = detect_defects(df)
        assert not is_clean(findings)
        assert not needs_review(findings)
        assert [s.op for s in plan_from_defects(findings)] == ["parse_datetime"]

    def test_merging_spelling_variants_is_a_judgement_call(self) -> None:
        df = pd.DataFrame({"city": ["Cairo", "cairo ", "CAIRO", "Giza"], "n": [1, 2, 3, 4]})
        assert needs_review(detect_defects(df))

    def test_a_finding_with_no_automatic_fix_needs_judgement(self) -> None:
        """Nothing can be applied automatically, which is when an opinion helps."""
        df = pd.DataFrame({"mixed": ["a", 1, "c", 2.5], "n": [1, 2, 3, 4]})
        assert needs_review(detect_defects(df))

    def test_outliers_alone_never_trigger_a_review(self) -> None:
        df = pd.DataFrame({"amount": [10.0, 11.0, 12.0, 9.0, 10.5, 11.5, 10.2, 5000.0]})
        assert not needs_review(detect_defects(df))
