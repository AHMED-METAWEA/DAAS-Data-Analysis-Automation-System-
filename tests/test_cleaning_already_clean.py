"""Planner behaviour: deterministic baseline, LLM refinement, safe degradation.

The planner is no longer the component that decides whether a defect exists.
``tools.defect_detection`` measures the defects and derives a complete typed
plan; the LLM reviews it. That inversion is what these tests pin down — in
particular that every way the model can fail (unreachable, unparseable output,
an invented operator, a malformed parameter) degrades to the deterministic
plan rather than to a bad clean or to no clean at all.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pandas as pd

from agents.cleaning.coder import coder_node
from agents.cleaning.planner import parse_llm_plan, planner_node, render_plan_text
from agents.cleaning.profiler import profiler_node
from models.profiler_models import DatasetProfile
from tools.defect_detection import detect_defects, is_clean, plan_from_defects


def _profile_for(df: pd.DataFrame, key_columns=None) -> dict:
    state = {"raw_df": df, "key_columns": list(key_columns or [])}
    return profiler_node(state)["metadata"]


def _state(df: pd.DataFrame, key_columns=None) -> dict:
    return {"metadata": _profile_for(df, key_columns), "planner_model": "m"}


_CLEAN_DF = pd.DataFrame({"customer_id": ["C1", "C2", "C3"], "amount": [10.0, 20.0, 30.0]})
_DIRTY_DF = pd.DataFrame({
    "customer_id": ["C1", "C2", "C3", "C4"],
    "amount": [10.0, None, 30.0, 40.0],
    "city": ["Cairo", "cairo ", "CAIRO", "Giza"],
})


class TestAlreadyCleanShortCircuit:
    def test_clean_data_skips_the_llm_entirely(self) -> None:
        def _fail_if_called(*a, **kw):
            raise AssertionError("planner must not call the LLM for already-clean data")

        with patch("agents.cleaning.planner.complete", side_effect=_fail_if_called):
            result = planner_node(_state(_CLEAN_DF))

        assert result["cleaning_plan"] == []

    def test_profiler_does_not_modify_clean_data(self) -> None:
        """The profiler used to replace placeholders and widen every integer
        column to float64 before anything had authorized either change, so
        order_id 1001 became 1001.0 in every dataset."""
        df = pd.DataFrame({"order_id": [1001, 1002], "qty": [1, 2]})
        out = profiler_node({"raw_df": df, "key_columns": []})["raw_df"]
        assert out["order_id"].tolist() == [1001, 1002]
        assert str(out["order_id"].dtype) == "int64"

    def test_outliers_alone_do_not_make_a_table_dirty(self) -> None:
        df = pd.DataFrame({"amount": [10.0, 11.0, 12.0, 9.0, 10.5, 11.5, 10.2, 5000.0]})
        assert is_clean(detect_defects(df))

    def test_outliers_alone_do_not_produce_a_plan_either(self) -> None:
        """`is_clean` and the plan used to disagree: the table was reported as
        needing no cleaning and still came back with a `flag_outliers` step per
        numeric column, appending indicator columns to data nobody called
        wrong."""
        df = pd.DataFrame({"amount": [10.0, 11.0, 12.0, 9.0, 10.5, 11.5, 10.2, 5000.0]})

        def _fail_if_called(*a, **kw):
            raise AssertionError("planner must not call the LLM over extreme values")

        with patch("agents.cleaning.planner.complete", side_effect=_fail_if_called):
            assert planner_node(_state(df))["cleaning_plan"] == []


class TestTypeConversionsSkipTheLLM:
    """A CSV has no types, so a file that was cleaned and re-uploaded still
    arrives with its dates as text. Those findings have exactly one correct fix
    and measured parameters — asking a planner to approve `parse_datetime`
    spends a call and a slice of a per-minute token budget, and its only
    possible contributions are agreement or a dropped step."""

    _TYPED_ONLY_DF = pd.DataFrame({
        "signup_date": ["2024-01-01", "2024-02-01", "2024-03-01"],
        "amount": [1.0, 2.0, 3.0],
    })

    def test_lossless_conversions_are_planned_without_the_model(self) -> None:
        def _fail_if_called(*a, **kw):
            raise AssertionError("planner must not call the LLM for lossless conversions")

        with patch("agents.cleaning.planner.complete", side_effect=_fail_if_called):
            result = planner_node(_state(self._TYPED_ONLY_DF))

        assert [s["op"] for s in result["cleaning_plan"]] == ["parse_datetime"]

    def test_the_conversion_is_still_planned_not_silently_dropped(self) -> None:
        """Skipping the review must not become skipping the work: dates left as
        text reach Postgres as text."""
        with patch("agents.cleaning.planner.complete", side_effect=AssertionError):
            plan = planner_node(_state(self._TYPED_ONLY_DF))["cleaning_plan"]

        assert plan[0]["columns"] == ["signup_date"]
        assert plan[0]["source"] == "detector"

    def test_a_judgement_call_still_reaches_the_model(self) -> None:
        """Merging spelling variants is a choice a business could disagree
        with, so the model is still consulted for it."""
        called = {}

        def _fake(purpose, messages, **kwargs):
            called["yes"] = True
            return json.dumps([
                {"op": "harmonize_categories", "columns": ["city"], "params": {},
                 "description": "Merge the spellings of the same city."},
            ])

        df = pd.DataFrame({"city": ["Cairo", "cairo ", "CAIRO", "Giza"], "n": [1, 2, 3, 4]})
        with patch("agents.cleaning.planner.complete", side_effect=_fake):
            planner_node(_state(df))

        assert called.get("yes")


class TestPlannerRefinesTheBaseline:
    def test_dirty_data_sends_findings_and_baseline_to_the_model(self) -> None:
        captured = {}

        def _fake(purpose, messages, **kwargs):
            captured["system"] = messages[0]["content"]
            captured["user"] = messages[1]["content"]
            captured["json_mode"] = kwargs.get("json_mode")
            return json.dumps([
                {"op": "impute", "columns": ["amount"], "params": {"strategy": "median"},
                 "description": "Fill the one missing amount with the median."},
            ])

        with patch("agents.cleaning.planner.complete", side_effect=_fake):
            result = planner_node(_state(_DIRTY_DF))

        assert captured["json_mode"] is True
        # The operator catalog is injected so the model can only pick real ops.
        assert "harmonize_categories" in captured["system"]
        # Measured findings, not raw column stats, are what it reasons over.
        assert "inconsistent_categories" in captured["user"]
        assert result["cleaning_plan"][0]["op"] == "impute"

    def test_model_output_is_ordered_by_the_system_not_by_the_model(self) -> None:
        def _fake(purpose, messages, **kwargs):
            return json.dumps([
                {"op": "impute", "columns": ["amount"], "params": {"strategy": "median"},
                 "description": "fill"},
                {"op": "drop_duplicate_rows", "columns": [], "params": {}, "description": "dedupe"},
            ])

        with patch("agents.cleaning.planner.complete", side_effect=_fake):
            result = planner_node(_state(_DIRTY_DF))

        assert [s["op"] for s in result["cleaning_plan"]] == ["drop_duplicate_rows", "impute"]


class TestPlannerDegradesSafely:
    def test_llm_exception_falls_back_to_the_deterministic_plan(self) -> None:
        def _boom(*a, **kw):
            raise RuntimeError("rate limited")

        with patch("agents.cleaning.planner.complete", side_effect=_boom):
            result = planner_node(_state(_DIRTY_DF))

        assert result["cleaning_plan"]
        assert all(s["op"] for s in result["cleaning_plan"])

    def test_unparseable_output_falls_back_to_the_deterministic_plan(self) -> None:
        with patch("agents.cleaning.planner.complete", side_effect=lambda *a, **k: "sorry, no"):
            result = planner_node(_state(_DIRTY_DF))

        expected = {s.op for s in plan_from_defects(detect_defects(_DIRTY_DF))}
        assert {s["op"] for s in result["cleaning_plan"]} == expected

    def test_invented_operator_is_rejected_not_executed(self) -> None:
        steps, rejections = parse_llm_plan(
            json.dumps([{"op": "delete_bad_rows", "columns": ["a"], "description": "x"}]),
            ["a"],
        )
        assert steps == []
        assert "unknown operator" in rejections[0]

    def test_malformed_parameter_is_rejected(self) -> None:
        steps, rejections = parse_llm_plan(
            json.dumps([{"op": "impute", "columns": ["a"], "params": {"strategy": "vibes"},
                         "description": "x"}]),
            ["a"],
        )
        assert steps == []
        assert "must be one of" in rejections[0]

    def test_a_valid_step_survives_alongside_a_rejected_one(self) -> None:
        steps, rejections = parse_llm_plan(
            json.dumps([
                {"op": "teleport", "columns": ["a"], "description": "nope"},
                {"op": "trim_whitespace", "columns": ["a"], "description": "trim a"},
            ]),
            ["a"],
        )
        assert [s.op for s in steps] == ["trim_whitespace"]
        assert len(rejections) == 1

    def test_json_inside_a_markdown_fence_is_recovered(self) -> None:
        raw = '```json\n[{"op": "trim_whitespace", "columns": ["a"], "description": "trim"}]\n```'
        steps, _ = parse_llm_plan(raw, ["a"])
        assert [s.op for s in steps] == ["trim_whitespace"]

    def test_a_step_with_no_operator_becomes_a_free_text_step(self) -> None:
        steps, _ = parse_llm_plan(
            json.dumps([{"description": "Apply the company's bespoke discount rule."}]), ["a"],
        )
        assert len(steps) == 1 and steps[0].is_typed is False


class TestRenderPlanText:
    def test_empty_plan_renders_a_clear_message(self) -> None:
        assert "already clean" in render_plan_text([]).lower()

    def test_renders_numbered_list(self) -> None:
        plan = [
            {"id": "1", "description": "Remove duplicate rows.", "source": "generated"},
            {"id": "x", "description": "Flag unusual amounts.", "source": "manual"},
        ]
        assert render_plan_text(plan) == "1. Remove duplicate rows.\n2. Flag unusual amounts."


class TestCoderIsOnlyForFreeText:
    def test_typed_plan_never_calls_the_llm(self) -> None:
        def _fail_if_called(*a, **kw):
            raise AssertionError("coder must not be called for a fully typed plan")

        state = {
            "cleaning_plan": [
                {"id": "1", "op": "trim_whitespace", "columns": ["a"], "description": "trim"}
            ],
            "metadata": _profile_for(pd.DataFrame({"a": [" x "]})),
            "retry_count": 0,
        }
        with patch("agents.cleaning.coder.complete", side_effect=_fail_if_called):
            assert coder_node(state)["generated_code"] == ""

    def test_free_text_step_does_call_the_llm(self) -> None:
        captured = {}

        def _fake(purpose, messages, **kwargs):
            captured["user"] = messages[-1]["content"]
            return "```python\ndef clean_data(df):\n    return df\n```"

        state = {
            "cleaning_plan": [{"id": "1", "description": "Apply the bespoke discount rule."}],
            "metadata": _profile_for(pd.DataFrame({"a": [1.0]})),
            "retry_count": 0,
        }
        with patch("agents.cleaning.coder.complete", side_effect=_fake):
            result = coder_node(state)

        assert "bespoke discount rule" in captured["user"]
        assert "def clean_data" in result["generated_code"]

    def test_coder_llm_failure_degrades_to_no_code(self) -> None:
        def _boom(*a, **kw):
            raise RuntimeError("down")

        state = {
            "cleaning_plan": [{"id": "1", "description": "Apply the bespoke rule."}],
            "metadata": _profile_for(pd.DataFrame({"a": [1.0]})),
            "retry_count": 0,
        }
        with patch("agents.cleaning.coder.complete", side_effect=_boom):
            assert coder_node(state)["generated_code"] == ""


class TestProfileCarriesFindings:
    def test_defects_are_attached_to_the_profile(self) -> None:
        profile = DatasetProfile(**_profile_for(_DIRTY_DF))
        kinds = {d["kind"] for d in profile.defects}
        assert "inconsistent_categories" in kinds
        assert "missing_values" in kinds
