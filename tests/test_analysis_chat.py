from __future__ import annotations

import pandas as pd

from backend.app.services import analysis_chat


def _df() -> pd.DataFrame:
    return pd.DataFrame({
        "customer_id": ["C1", "C2", "C3"],
        "total_price": [100.0, 200.0, 300.0],
    })


def test_fabricated_number_with_no_code_triggers_correction(monkeypatch) -> None:
    """The core bug this fix closes: df_context() only gives schema/head/
    describe (no exact sums), so a model answering a numeric question without
    writing any code is stating a number it never actually computed."""
    calls: list[str] = []

    def fake_complete(purpose, messages, **kwargs):
        calls.append(messages[-1]["content"])
        if len(calls) == 1:
            # No ```python``` block — this number was never computed.
            return "Your total revenue is $45,230."
        # Correction pass: honestly declines instead of restating a number.
        return "I don't have a verified total for that from what was computed here."

    monkeypatch.setattr(analysis_chat, "complete", fake_complete)

    result = analysis_chat.run_grounded_chat(
        system_context="You are a BI assistant.",
        history=[],
        message="What's our total revenue?",
        df=_df(),
        model="test-model",
    )

    assert len(calls) == 2, "an unverified material figure must trigger exactly one correction pass"
    assert "45,230" not in result["answer"] and "45230" not in result["answer"]
    assert result["grounded"] is True


def test_code_backed_number_is_not_corrected(monkeypatch) -> None:
    """When the model's own code actually computed the number it states,
    nothing should be rewritten — nor should the correction call fire."""
    calls: list[str] = []

    def fake_complete(purpose, messages, **kwargs):
        calls.append(messages[-1]["content"])
        return (
            "```python\nprint(df['total_price'].sum())\n```\n"
            "Total revenue is $600.00 across all customers."
        )

    monkeypatch.setattr(analysis_chat, "complete", fake_complete)

    result = analysis_chat.run_grounded_chat(
        system_context="You are a BI assistant.",
        history=[],
        message="What's our total revenue?",
        df=_df(),
        model="test-model",
    )

    assert len(calls) == 1, "a figure that traces to the actual executed output needs no correction"
    assert result["grounded"] is True
    assert "$600.00" in result["answer"]


def test_qualitative_answer_skips_grounding_entirely(monkeypatch) -> None:
    calls: list[str] = []

    def fake_complete(purpose, messages, **kwargs):
        calls.append(messages[-1]["content"])
        return "This dataset covers coffee and equipment purchases across three customers."

    monkeypatch.setattr(analysis_chat, "complete", fake_complete)

    result = analysis_chat.run_grounded_chat(
        system_context="You are a BI assistant.",
        history=[],
        message="What kind of data is this?",
        df=_df(),
        model="test-model",
    )

    assert len(calls) == 1, "an answer with no material numeric claims needs no grounding check at all"
    assert result["grounded"] is True


def test_correction_call_failure_falls_back_to_draft(monkeypatch) -> None:
    """If the corrective LLM call itself fails, the turn must still return the
    original draft rather than losing the answer entirely — but marked as
    not grounded, since it was never actually verified."""
    def fake_complete(purpose, messages, **kwargs):
        if len(messages) == 2 and messages[0]["content"] == "You are a BI assistant.":
            return "Revenue hit $99,999 last quarter."
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(analysis_chat, "complete", fake_complete)

    result = analysis_chat.run_grounded_chat(
        system_context="You are a BI assistant.",
        history=[],
        message="What's our revenue?",
        df=_df(),
        model="test-model",
    )

    assert "$99,999" in result["answer"]
    assert result["grounded"] is False
