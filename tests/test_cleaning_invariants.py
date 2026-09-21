"""Hard invariants: the gate that decides whether a cleaning run may stand.

Every case in ``TestCorruptionsThatUsedToPass`` is a transformation that the
previous validator accepted. That validator checked three things — at least one
row survived, the total null count did not rise, and no column's null count
rose — and none of these corruptions changes a null count, so all of them
passed and shipped to Postgres with a green tick and a confident log line.

The model here is authorization: the plan says which columns each step targets,
each operator declares statically what class of change it may cause, and every
observed difference must be attributable to one of them.
"""

from __future__ import annotations

import pandas as pd

from agents.cleaning.invariants import authorization_from_plan, check_invariants
from models.cleaning_ops import CleaningStep


def _raw() -> pd.DataFrame:
    df = pd.DataFrame({
        "order_id": [f"A{i}" for i in range(1, 21)],
        "revenue": [100.0 + i for i in range(20)],
        "city": ["Cairo", "Giza"] * 10,
        "qty": list(range(1, 21)),
    })
    df.loc[3, "revenue"] = None       # something the plan legitimately fixes
    return df


# A realistic plan: remove exact duplicates, fill the blank revenue.
_PLAN = [
    CleaningStep(id="1", op="drop_duplicate_rows", description="Remove exact duplicate rows."),
    CleaningStep(id="2", op="impute", columns=["revenue"], params={"strategy": "median"},
                 description="Fill the missing revenue with the median."),
]


def _check(clean: pd.DataFrame, plan=None, keys=None):
    return check_invariants(_raw(), clean, plan or _PLAN, key_columns=keys or {"order_id"})


class TestCorruptionsThatUsedToPass:
    def test_rescaling_a_column_is_rejected(self) -> None:
        out = _raw().assign(revenue=lambda d: d.revenue * 100)
        report = _check(out)
        assert not report.passed
        assert "unauthorized_value_change" in {v.rule for v in report.violations}

    def test_overwriting_every_value_with_the_mean_is_rejected(self) -> None:
        """The plan says 'fill the missing revenue'. Naming the column is not
        permission to replace the values that were already there."""
        out = _raw()
        out["revenue"] = out["revenue"].mean()
        report = _check(out)
        assert not report.passed
        assert "unauthorized_value_change" in {v.rule for v in report.violations}

    def test_deleting_a_column_is_rejected(self) -> None:
        report = _check(_raw().drop(columns=["revenue"]))
        assert not report.passed
        assert "unauthorized_column_drop" in {v.rule for v in report.violations}

    def test_deleting_rows_that_are_not_duplicates_is_rejected(self) -> None:
        """'Remove duplicate rows' is not a licence to delete 45% of the data —
        and deleting rows lowers both the row count and the null count, so the
        old check was satisfied twice over."""
        report = _check(_raw().head(11))
        assert not report.passed
        assert "rows_removed_that_were_not_duplicates" in {v.rule for v in report.violations}

    def test_rewriting_a_join_key_is_rejected(self) -> None:
        out = _raw()
        out["order_id"] = out["order_id"].str.lower().str.replace("a", "x")
        report = _check(out)
        assert not report.passed
        assert "join_key_modified" in {v.rule for v in report.violations}

    def test_duplicating_every_row_is_rejected(self) -> None:
        out = pd.concat([_raw(), _raw()], ignore_index=True)
        report = _check(out)
        assert not report.passed
        assert {"rows_added", "rows_invented"} & {v.rule for v in report.violations}

    def test_shuffling_a_column_against_the_wrong_rows_is_rejected(self) -> None:
        out = _raw()
        out["revenue"] = out["revenue"].sort_values(ascending=False).values
        report = _check(out)
        assert not report.passed

    def test_silently_filling_an_unrelated_column_is_rejected(self) -> None:
        raw = _raw()
        raw.loc[5, "qty"] = None
        out = raw.copy()
        out["qty"] = out["qty"].fillna(0)
        report = check_invariants(raw, out, _PLAN, key_columns={"order_id"})
        assert not report.passed
        assert "unauthorized_null_fill" in {v.rule for v in report.violations}

    def test_blanking_out_real_values_is_rejected(self) -> None:
        out = _raw()
        out.loc[7, "city"] = None
        report = _check(out)
        assert not report.passed
        assert "unauthorized_null_introduction" in {v.rule for v in report.violations}

    def test_silent_dtype_change_is_rejected(self) -> None:
        out = _raw()
        out["qty"] = out["qty"].astype(str)
        report = _check(out)
        assert not report.passed
        assert "unauthorized_dtype_change" in {v.rule for v in report.violations}


class TestLegitimateWorkPasses:
    def test_filling_the_column_the_plan_named(self) -> None:
        out = _raw()
        out["revenue"] = out["revenue"].fillna(out["revenue"].median())
        assert _check(out).passed

    def test_removing_a_genuine_duplicate_row(self) -> None:
        raw = _raw()
        raw.loc[19] = raw.loc[0]
        out = raw.drop_duplicates()
        assert check_invariants(raw, out, _PLAN, key_columns={"order_id"}).passed

    def test_a_no_op_clean_passes(self) -> None:
        assert _check(_raw().copy()).passed

    def test_parsing_may_introduce_nulls_when_the_plan_says_so(self) -> None:
        raw = pd.DataFrame({"price": ["10", "oops", "30"]})
        plan = [CleaningStep(id="1", op="parse_numeric", columns=["price"],
                             description="Convert price to numbers.")]
        out = pd.DataFrame({"price": [10.0, None, 30.0]})
        report = check_invariants(raw, out, plan)
        assert report.passed
        assert "high_parse_failure" in {w.rule for w in report.warnings}

    def test_flag_columns_may_be_added(self) -> None:
        plan = [CleaningStep(id="1", op="flag_outliers", columns=["revenue"],
                             description="Flag unusual revenue.")]
        out = _raw()
        out["revenue__is_outlier"] = False
        assert check_invariants(_raw(), out, plan).passed


class TestWarnings:
    def test_large_row_loss_warns_without_failing(self) -> None:
        raw = pd.DataFrame({"a": [1, 1, 1, 1, 2]})
        plan = [CleaningStep(id="1", op="drop_duplicate_rows", description="dedupe")]
        out = raw.drop_duplicates()
        report = check_invariants(raw, out, plan)
        assert report.passed
        assert "large_row_loss" in {w.rule for w in report.warnings}


class TestFreeTextAuthorization:
    """Generated code has no per-step ledger, so its authorization comes from
    the columns the plan's free-text steps actually name."""

    def test_code_may_touch_a_column_the_step_names(self) -> None:
        plan = [CleaningStep(id="1", description="Round the revenue column to whole pounds.")]
        out = _raw()
        out["revenue"] = out["revenue"].round(0)
        assert check_invariants(_raw(), out, plan, key_columns={"order_id"}).passed

    def test_code_may_not_touch_a_column_no_step_names(self) -> None:
        plan = [CleaningStep(id="1", description="Round the revenue column to whole pounds.")]
        out = _raw()
        out["qty"] = out["qty"] * 2
        report = check_invariants(_raw(), out, plan, key_columns={"order_id"})
        assert not report.passed
        assert report.violations[0].column == "qty"

    def test_repair_feedback_names_the_rule_and_the_column(self) -> None:
        plan = [CleaningStep(id="1", description="Round the revenue column.")]
        out = _raw()
        out["qty"] = out["qty"] * 2
        feedback = check_invariants(_raw(), out, plan, key_columns={"order_id"}).repair_feedback()
        assert "unauthorized_value_change" in feedback
        assert "qty" in feedback


class TestAuthorizationDerivation:
    def test_impute_authorizes_filling_but_not_rewriting(self) -> None:
        auth = authorization_from_plan(
            [CleaningStep(id="1", op="impute", columns=["a"], params={"strategy": "median"})],
            ["a"],
        )
        assert auth.fill_columns == {"a"}
        assert auth.rewrite_columns == set()

    def test_flag_operators_authorize_neither(self) -> None:
        auth = authorization_from_plan(
            [CleaningStep(id="1", op="flag_outliers", columns=["a"])], ["a"]
        )
        assert auth.rewrite_columns == set()
        assert auth.fill_columns == set()
        assert auth.may_add_columns is True

    def test_cross_column_operands_are_authorized(self) -> None:
        auth = authorization_from_plan(
            [CleaningStep(id="1", op="recompute_arithmetic", columns=["total"],
                          params={"left": "qty", "right": "price", "result": "total"})],
            ["qty", "price", "total"],
        )
        assert {"qty", "price", "total"} <= auth.columns
