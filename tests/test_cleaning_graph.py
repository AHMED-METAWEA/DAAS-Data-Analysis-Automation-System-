"""The cleaning graph's routing, including the repair loop.

Previously only an execution *crash* triggered a retry. Code that ran fine but
corrupted the data ended the graph immediately: the table was marked failed and
never got a second attempt, because the validator had no structured reason to
hand back. Validation failures now re-enter the coder with the exact rule and
column that broke.
"""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd

from graphs.cleaning_graph import build_cleaning_graph

_ROUND_REVENUE = {"id": "1", "description": "Round the revenue column to whole pounds."}


def _state(plan: list[dict]) -> dict:
    return {
        "raw_df": pd.DataFrame({"revenue": [100.4, 200.6, 300.5], "qty": [1, 2, 3]}),
        "file_path": "t", "planner_model": "m", "coder_model": "m",
        "key_columns": [], "metadata": {}, "cleaning_plan": plan,
        "generated_code": "", "execution_result": {}, "validation_report": {},
        "transformation_log": [], "retry_count": 0, "last_error": "", "messages": [],
    }


_TOUCHES_UNRELATED_COLUMN = (
    "def clean_data(df):\n"
    "    df = df.copy()\n"
    "    df['revenue'] = df['revenue'].round(0)\n"
    "    df['qty'] = df['qty'] * 1000\n"
    "    return df\n"
)
_CORRECT = (
    "def clean_data(df):\n"
    "    df = df.copy()\n"
    "    df['revenue'] = df['revenue'].round(0)\n"
    "    return df\n"
)


class TestValidationRepairLoop:
    def test_invariant_violation_sends_the_code_back_for_repair(self) -> None:
        prompts: list[str] = []

        def _fake(purpose, messages, **kwargs):
            prompts.append(messages[-1]["content"])
            return _TOUCHES_UNRELATED_COLUMN if len(prompts) == 1 else _CORRECT

        with patch("agents.cleaning.coder.complete", side_effect=_fake):
            final = build_cleaning_graph().invoke(_state([_ROUND_REVENUE]))

        assert len(prompts) == 2
        # The repair prompt must name the rule and the column, not just say "it failed".
        assert "unauthorized_value_change" in prompts[1]
        assert "qty" in prompts[1]
        assert final["validation_report"]["passed"] is True
        assert final["execution_result"]["clean_df"]["qty"].tolist() == [1, 2, 3]

    def test_repeatedly_bad_code_eventually_fails_instead_of_looping(self) -> None:
        calls: list[str] = []

        def _always_bad(purpose, messages, **kwargs):
            calls.append(purpose)
            return _TOUCHES_UNRELATED_COLUMN

        with patch("agents.cleaning.coder.complete", side_effect=_always_bad):
            final = build_cleaning_graph().invoke(_state([_ROUND_REVENUE]))

        assert final["validation_report"]["passed"] is False
        assert len(calls) <= 3  # initial attempt + MAX_RETRIES

    def test_a_typed_plan_that_fails_does_not_burn_retries_on_the_llm(self) -> None:
        """A plan of deterministic operators produces the same bytes every run,
        so re-running it cannot help — that would be an operator bug, not a bad
        model sample."""
        def _fail_if_called(*a, **kw):
            raise AssertionError("the coder must not be called for a typed plan")

        plan = [{"id": "1", "op": "impute", "columns": ["revenue"],
                 "params": {"strategy": "median"}, "description": "fill revenue"}]
        with patch("agents.cleaning.coder.complete", side_effect=_fail_if_called):
            final = build_cleaning_graph().invoke(_state(plan))

        assert final["validation_report"]["passed"] is True
        assert final["retry_count"] == 0


class TestDeterministicPathNeedsNoLlm:
    def test_typed_plan_runs_with_no_llm_available(self) -> None:
        def _boom(*a, **kw):
            raise RuntimeError("no API key")

        plan = [
            {"id": "1", "op": "parse_numeric", "columns": ["revenue"], "description": "parse"},
            {"id": "2", "op": "flag_outliers", "columns": ["revenue"], "description": "flag"},
        ]
        with patch("agents.cleaning.coder.complete", side_effect=_boom):
            final = build_cleaning_graph().invoke(_state(plan))

        assert final["validation_report"]["passed"] is True
        assert "revenue__is_outlier" in final["execution_result"]["clean_df"].columns
