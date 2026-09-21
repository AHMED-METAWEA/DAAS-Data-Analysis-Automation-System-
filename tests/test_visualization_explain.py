from __future__ import annotations

import pytest

from agents.visualization import explain
from tools.llm_client import AllProvidersFailedError


def _fig() -> dict:
    return {
        "data": [{"type": "bar", "x": ["Jan", "Feb"], "y": [10, 20]}],
        "layout": {"title": {"text": "Revenue by Month"}},
    }


class TestExplainChart:
    def test_calls_complete_with_explain_chart_purpose(self, monkeypatch) -> None:
        captured = {}

        def fake_complete(purpose, messages, **kwargs):
            captured["purpose"] = purpose
            captured["messages"] = messages
            return "This bar chart shows revenue rising from 10 in January to 20 in February."

        monkeypatch.setattr(explain, "complete", fake_complete)

        result = explain.explain_chart(_fig())

        assert captured["purpose"] == "explain_chart"
        assert result.startswith("This bar chart")

    def test_prompt_contains_summary_not_raw_arrays(self, monkeypatch) -> None:
        captured = {}

        def fake_complete(purpose, messages, **kwargs):
            captured["messages"] = messages
            return "explanation"

        monkeypatch.setattr(explain, "complete", fake_complete)
        explain.explain_chart(_fig())

        user_content = captured["messages"][1]["content"]
        assert '"chart_title": "Revenue by Month"' in user_content
        assert '"min": 10' in user_content
        # The raw data arrays themselves must never be dumped verbatim.
        assert '"x": [' not in user_content
        assert '"y": [' not in user_content

    def test_title_and_context_are_optional(self, monkeypatch) -> None:
        def fake_complete(purpose, messages, **kwargs):
            return "explanation"

        monkeypatch.setattr(explain, "complete", fake_complete)

        # No title/context at all — must not raise.
        assert explain.explain_chart(_fig()) == "explanation"

    def test_title_and_context_are_used_when_given(self, monkeypatch) -> None:
        captured = {}

        def fake_complete(purpose, messages, **kwargs):
            captured["messages"] = messages
            return "explanation"

        monkeypatch.setattr(explain, "complete", fake_complete)
        explain.explain_chart(_fig(), title="Q1 Revenue", context="Shown on the executive dashboard")

        user_content = captured["messages"][1]["content"]
        assert "Q1 Revenue" in user_content
        assert "executive dashboard" in user_content

    def test_provider_failure_propagates(self, monkeypatch) -> None:
        def fake_complete(purpose, messages, **kwargs):
            raise AllProvidersFailedError("all providers failed: groq, anthropic, openai, openrouter")

        monkeypatch.setattr(explain, "complete", fake_complete)

        with pytest.raises(AllProvidersFailedError):
            explain.explain_chart(_fig())
