from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tools.llm_client import AllProvidersFailedError, complete


def _groq_response(text: str, *, finish_reason: str = "stop", reasoning_tokens: int = 0):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=text), finish_reason=finish_reason,
        )],
        usage=SimpleNamespace(
            prompt_tokens=10, completion_tokens=20, total_tokens=30,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=reasoning_tokens),
        ),
    )


def _openai_response(text: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


def _anthropic_response(text: str):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


class TestGroqPrimary:
    def test_groq_success_uses_literal_model(self, monkeypatch) -> None:
        monkeypatch.setenv("GROQ_API_KEY", "test-key")
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _groq_response("hello from groq")

        with patch("groq.Groq", return_value=mock_client):
            result = complete("planner", [{"role": "user", "content": "hi"}], model="openai/gpt-oss-120b")

        assert result == "hello from groq"
        _, kwargs = mock_client.chat.completions.create.call_args
        assert kwargs["model"] == "openai/gpt-oss-120b"

    def test_groq_empty_response_triggers_fallback(self, monkeypatch) -> None:
        monkeypatch.setenv("GROQ_API_KEY", "test-key")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("LLM_PROVIDER_ORDER", "groq,anthropic")

        groq_client = MagicMock()
        groq_client.chat.completions.create.return_value = _groq_response("   ")

        anthropic_client = MagicMock()
        anthropic_client.messages.create.return_value = _anthropic_response("from anthropic")

        with patch("groq.Groq", return_value=groq_client), \
             patch("anthropic.Anthropic", return_value=anthropic_client):
            result = complete("planner", [{"role": "user", "content": "hi"}], model="openai/gpt-oss-120b")

        assert result == "from anthropic"


class TestFallbackOrdering:
    def test_falls_back_to_anthropic_on_groq_exception(self, monkeypatch) -> None:
        monkeypatch.setenv("GROQ_API_KEY", "test-key")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("LLM_PROVIDER_ORDER", "groq,anthropic,openai,openrouter")

        anthropic_client = MagicMock()
        anthropic_client.messages.create.return_value = _anthropic_response("anthropic saved the day")

        with patch("groq.Groq", side_effect=RuntimeError("rate limited")), \
             patch("anthropic.Anthropic", return_value=anthropic_client):
            result = complete("coder", [{"role": "user", "content": "hi"}])

        assert result == "anthropic saved the day"

    def test_non_groq_providers_use_purpose_model_not_caller_model(self, monkeypatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("LLM_PROVIDER_ORDER", "anthropic")

        anthropic_client = MagicMock()
        anthropic_client.messages.create.return_value = _anthropic_response("ok")

        with patch("anthropic.Anthropic", return_value=anthropic_client):
            complete("planner", [{"role": "user", "content": "hi"}], model="openai/gpt-oss-120b")

        _, kwargs = anthropic_client.messages.create.call_args
        assert kwargs["model"] == "claude-sonnet-5"  # from PROVIDER_MODELS, not the Groq model string

    def test_openrouter_reuses_openai_sdk_with_base_url(self, monkeypatch) -> None:
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.setenv("LLM_PROVIDER_ORDER", "openrouter")

        openai_client = MagicMock()
        openai_client.chat.completions.create.return_value = _openai_response("via openrouter")

        with patch("openai.OpenAI", return_value=openai_client) as mock_openai:
            result = complete("viz", [{"role": "user", "content": "hi"}])

        assert result == "via openrouter"
        _, kwargs = mock_openai.call_args
        assert kwargs["base_url"] == "https://openrouter.ai/api/v1"

    def test_all_providers_failing_raises(self, monkeypatch) -> None:
        monkeypatch.setenv("GROQ_API_KEY", "test-key")
        monkeypatch.setenv("LLM_PROVIDER_ORDER", "groq")

        with patch("groq.Groq", side_effect=RuntimeError("down")):
            with pytest.raises(AllProvidersFailedError):
                complete("planner", [{"role": "user", "content": "hi"}])

    def test_unconfigured_provider_is_skipped_not_fatal(self, monkeypatch) -> None:
        # No GROQ_API_KEY set, but anthropic is configured — should skip straight to it.
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("LLM_PROVIDER_ORDER", "groq,anthropic")

        anthropic_client = MagicMock()
        anthropic_client.messages.create.return_value = _anthropic_response("ok")

        with patch("anthropic.Anthropic", return_value=anthropic_client):
            result = complete("planner", [{"role": "user", "content": "hi"}])

        assert result == "ok"


class TestJsonMode:
    def test_json_mode_sets_response_format_for_groq(self, monkeypatch) -> None:
        monkeypatch.setenv("GROQ_API_KEY", "test-key")
        monkeypatch.setenv("LLM_PROVIDER_ORDER", "groq")

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _groq_response('{"a": 1}')

        with patch("groq.Groq", return_value=mock_client):
            complete("marketing", [{"role": "user", "content": "hi"}], model="m", json_mode=True)

        _, kwargs = mock_client.chat.completions.create.call_args
        assert kwargs["response_format"] == {"type": "json_object"}


class TestReasoningModelsDoNotEatTheirOwnAnswer:
    """A reasoning model bills its thinking against ``max_tokens`` while none of
    it reaches ``content``. Left at Groq's default effort, `gpt-oss-120b` spent
    1,062 tokens reasoning about a decision brief, the answer was cut off at the
    ceiling, and a slightly longer prompt pushed reasoning over the whole
    ceiling — a successful request that returned nothing, surfacing to the user
    as "Groq returned an empty response"."""

    def _groq_only(self, monkeypatch) -> None:
        monkeypatch.setenv("GROQ_API_KEY", "test-key")
        monkeypatch.setenv("LLM_PROVIDER_ORDER", "groq")

    def test_reasoning_effort_is_sent_for_a_reasoning_model(self, monkeypatch) -> None:
        self._groq_only(monkeypatch)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _groq_response("a report")

        with patch("groq.Groq", return_value=mock_client):
            complete(
                "insights", [{"role": "user", "content": "hi"}], model="openai/gpt-oss-120b",
            )

        _, kwargs = mock_client.chat.completions.create.call_args
        assert kwargs["reasoning_effort"] == "low"

    def test_the_parameter_is_omitted_for_a_model_that_would_reject_it(self, monkeypatch) -> None:
        """A 400 here would push a working request onto the next provider."""
        self._groq_only(monkeypatch)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _groq_response("an answer")

        with patch("groq.Groq", return_value=mock_client):
            complete("planner", [{"role": "user", "content": "hi"}], model="llama-3.3-70b")

        _, kwargs = mock_client.chat.completions.create.call_args
        assert "reasoning_effort" not in kwargs

    def test_an_answer_lost_to_reasoning_says_so(self, monkeypatch) -> None:
        """"Groq returned an empty response" was true and useless: it sends a
        reader to check the API key, the prompt and a retry, none of which is
        the problem."""
        self._groq_only(monkeypatch)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _groq_response(
            "", finish_reason="length", reasoning_tokens=2393,
        )

        with (
            patch("groq.Groq", return_value=mock_client),
            pytest.raises(AllProvidersFailedError) as excinfo,
        ):
            complete(
                "insights", [{"role": "user", "content": "hi"}], model="openai/gpt-oss-120b",
            )

        message = str(excinfo.value)
        assert "reasoning" in message
        assert "2393" in message

    def test_a_genuinely_empty_answer_is_still_reported_as_empty(self, monkeypatch) -> None:
        self._groq_only(monkeypatch)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _groq_response("", finish_reason="stop")

        with (
            patch("groq.Groq", return_value=mock_client),
            pytest.raises(AllProvidersFailedError) as excinfo,
        ):
            complete(
                "insights", [{"role": "user", "content": "hi"}], model="openai/gpt-oss-120b",
            )

        assert "empty response" in str(excinfo.value)

    def test_a_truncated_answer_is_returned_but_logged_as_incomplete(
        self, monkeypatch, caplog,
    ) -> None:
        """A cut-off brief beats no brief, and there is nowhere better to fall
        back to — but it is a real defect and must not pass silently."""
        self._groq_only(monkeypatch)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _groq_response(
            "half a report", finish_reason="length", reasoning_tokens=1062,
        )

        with patch("groq.Groq", return_value=mock_client), caplog.at_level("WARNING"):
            result = complete(
                "insights", [{"role": "user", "content": "hi"}], model="openai/gpt-oss-120b",
            )

        assert result == "half a report"
        assert "cut off" in caplog.text


class TestReasoningEffortPolicy:
    def test_both_gpt_oss_sizes_are_covered(self) -> None:
        from tools.llm_provider_models import groq_reasoning_effort

        assert groq_reasoning_effort("openai/gpt-oss-120b") == "low"
        assert groq_reasoning_effort("openai/gpt-oss-20b") == "low"

    def test_qwen_keeps_the_setting_that_stops_inline_think_blocks(self) -> None:
        from tools.llm_provider_models import groq_reasoning_effort

        assert groq_reasoning_effort("qwen/qwen3.6-27b") == "none"

    def test_an_unknown_model_gets_no_reasoning_parameter(self) -> None:
        from tools.llm_provider_models import groq_reasoning_effort

        assert groq_reasoning_effort("some-future-model") is None
        assert groq_reasoning_effort("") is None
