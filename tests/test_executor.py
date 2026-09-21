"""Executor node: deterministic operators first, generated code only for
free-text steps.

The executor's contract changed with the operator rebuild. It no longer takes
"here is some code, run it" — it takes a *plan*. Typed steps run as operators
from ``tools.cleaning_ops``; ``generated_code`` is used only when the plan
contains a step no operator covers.
"""

from __future__ import annotations

import time
from concurrent.futures import TimeoutError as FutureTimeoutError
from unittest.mock import patch

import pandas as pd
import pytest

from agents.cleaning.executor import executor_node, parse_plan_steps
from tools.sandbox import run_with_timeout

_FREE_TEXT = {"id": "1", "description": "Apply the custom rule the user typed.", "source": "manual"}


def _state(code: str, raw_df: pd.DataFrame, retry_count: int = 0, plan=None) -> dict:
    return {
        "generated_code": code,
        "raw_df": raw_df,
        "retry_count": retry_count,
        # A free-text step by default, so generated code is actually reached.
        "cleaning_plan": [_FREE_TEXT] if plan is None else plan,
    }


class TestRunWithTimeoutPrimitive:
    """The shared timeout helper (tools.sandbox.run_with_timeout) that both
    run_analysis and the cleaning executor use."""

    def test_returns_result_when_fast_enough(self) -> None:
        assert run_with_timeout(lambda: 1 + 1, timeout=1.0) == 2

    def test_raises_timeout_error_when_too_slow(self) -> None:
        def _slow():
            time.sleep(0.3)
            return "done"

        with pytest.raises(FutureTimeoutError):
            run_with_timeout(_slow, timeout=0.05)


class TestPlanParsing:
    def test_typed_and_free_text_steps_are_distinguished(self) -> None:
        steps = parse_plan_steps([
            {"id": "1", "op": "trim_whitespace", "columns": ["a"], "description": "trim"},
            {"id": "2", "description": "something bespoke"},
        ])
        assert [s.is_typed for s in steps] == [True, False]

    def test_malformed_step_degrades_to_free_text_instead_of_raising(self) -> None:
        steps = parse_plan_steps([{"id": "1", "description": "x", "params": "not-a-dict"}])
        assert len(steps) == 1
        assert steps[0].is_typed is False


class TestOperatorPath:
    def test_typed_plan_runs_without_any_generated_code(self) -> None:
        raw_df = pd.DataFrame({"a": [1.0, None, 3.0]})
        plan = [{"id": "1", "op": "impute", "columns": ["a"],
                 "params": {"strategy": "median"}, "description": "fill a"}]

        result = executor_node({
            "generated_code": "", "raw_df": raw_df, "retry_count": 0, "cleaning_plan": plan,
        })

        assert result["execution_result"]["success"] is True
        assert result["execution_result"]["clean_df"]["a"].tolist() == [1.0, 2.0, 3.0]

    def test_ledger_counts_are_measured_not_reported(self) -> None:
        raw_df = pd.DataFrame({"a": [1.0, None, 3.0]})
        plan = [{"id": "1", "op": "impute", "columns": ["a"],
                 "params": {"strategy": "median"}, "description": "fill a"}]

        result = executor_node({
            "generated_code": "", "raw_df": raw_df, "retry_count": 0, "cleaning_plan": plan,
        })
        ledger = result["execution_result"]["ledger"]

        assert ledger["steps"][0]["nulls_filled"] == 1
        assert ledger["steps"][0]["cells_changed"] == 1
        assert "filled 1 missing value(s)" in ledger["steps"][0]["summary"]

    def test_a_failing_operator_is_recorded_and_does_not_abort_the_run(self) -> None:
        raw_df = pd.DataFrame({"a": [1.0, None, 3.0]})
        plan = [
            {"id": "1", "op": "impute", "columns": ["nonexistent"],
             "params": {"strategy": "median"}, "description": "bad step"},
            {"id": "2", "op": "impute", "columns": ["a"],
             "params": {"strategy": "median"}, "description": "good step"},
        ]

        result = executor_node({
            "generated_code": "", "raw_df": raw_df, "retry_count": 0, "cleaning_plan": plan,
        })
        steps = result["execution_result"]["ledger"]["steps"]

        assert steps[0]["applied"] is False and steps[0]["error"]
        assert steps[1]["applied"] is True


class TestGeneratedCodePath:
    def test_code_runs_for_free_text_steps(self) -> None:
        code = (
            "def clean_data(df):\n"
            "    df = df.copy()\n"
            "    df['a'] = df['a'].fillna(0)\n"
            "    return df\n"
        )
        result = executor_node(_state(code, pd.DataFrame({"a": [1.0, None, 3.0]})))

        assert result["execution_result"]["success"] is True
        assert result["execution_result"]["clean_df"]["a"].tolist() == [1.0, 0.0, 3.0]

    def test_code_is_ignored_when_every_step_is_typed(self) -> None:
        """A typed plan must not be able to smuggle in generated code."""
        code = "def clean_data(df):\n    df = df.copy()\n    df['a'] = 999\n    return df\n"
        plan = [{"id": "1", "op": "trim_whitespace", "columns": ["b"], "description": "trim b"}]
        raw_df = pd.DataFrame({"a": [1.0, 2.0], "b": [" x ", "y"]})

        result = executor_node(_state(code, raw_df, plan=plan))

        assert result["execution_result"]["clean_df"]["a"].tolist() == [1.0, 2.0]

    def test_missing_clean_data_function_fails_gracefully(self) -> None:
        result = executor_node(_state("x = 1\n", pd.DataFrame({"a": [1]})))
        assert result["execution_result"]["success"] is False
        assert result["retry_count"] == 1

    def test_numpy_and_re_are_available_to_generated_code(self) -> None:
        """np.nan and re.sub are what an LLM reaches for constantly. Without
        them in scope every such attempt burned a retry on a NameError."""
        code = (
            "def clean_data(df):\n"
            "    df = df.copy()\n"
            "    df['a'] = df['a'].fillna(np.nan).fillna(0)\n"
            "    df['b'] = df['b'].map(lambda s: re.sub(r'\\s+', '', s))\n"
            "    return df\n"
        )
        result = executor_node(_state(code, pd.DataFrame({"a": [1.0, None], "b": ["x y", "z"]})))

        assert result["execution_result"]["success"] is True, result["execution_result"]["error"]
        assert result["execution_result"]["clean_df"]["b"].tolist() == ["xy", "z"]


class TestExecutorNodeTimeout:
    def test_timeout_is_caught_and_reported_without_a_raw_traceback(self) -> None:
        with patch(
            "agents.cleaning.executor.run_with_timeout", side_effect=FutureTimeoutError()
        ):
            result = executor_node(_state("def clean_data(df):\n    return df\n", pd.DataFrame({"a": [1]})))

        assert result["execution_result"]["success"] is False
        assert result["execution_result"]["clean_df"] is None
        assert "timed out" in result["execution_result"]["error"].lower()
        assert "Traceback" not in result["execution_result"]["error"]
        assert result["retry_count"] == 1

    def test_genuinely_slow_code_times_out_end_to_end(self) -> None:
        # Real thread-pool timeout, not mocked — proves the wiring, not just
        # the except-branch. shutdown(wait=False) means this returns in ~0.05s
        # even though the loop keeps running a bit longer in the background.
        code = (
            "def clean_data(df):\n"
            "    total = 0\n"
            "    for i in range(50_000_000):\n"
            "        total += i\n"
            "    return df\n"
        )
        with patch("agents.cleaning.executor.run_with_timeout") as mock_run:
            mock_run.side_effect = lambda fn, *a, **kw: run_with_timeout(fn, *a, timeout=0.05, **kw)
            result = executor_node(_state(code, pd.DataFrame({"a": [1]})))

        assert result["execution_result"]["success"] is False
        assert "timed out" in result["execution_result"]["error"].lower()
