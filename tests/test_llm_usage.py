"""Token accounting on top of the provider fallback chain.

The streaming cases carry most of the weight here. A streamed call reports its
token counts on a trailing chunk whose ``choices`` list is EMPTY, and the stream
loops skip empty-choice chunks — so before ``_absorb_stream_usage`` ran ahead of
that guard, every streamed call recorded zero tokens while looking healthy.
Copilot is the heaviest LLM surface in the product and it is entirely streamed,
so that failure mode would have quietly hidden most of the real spend.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tools.llm_client import complete, stream_complete
from tools.llm_usage import UsageEvent, usage_sink


def _openai_response(text: str, prompt=0, completion=0, total=0):
    usage = SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion, total_tokens=total)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))], usage=usage
    )


def _anthropic_response(text: str, input_tokens=0, output_tokens=0):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


def _text_chunk(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=content))], usage=None, x_groq=None
    )


def _usage_chunk(prompt, completion, total):
    """The trailing chunk: real token counts, and no choices at all."""
    return SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion, total_tokens=total),
        x_groq=None,
    )


@pytest.fixture
def groq_only(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER_ORDER", "groq")


class TestBlockingUsage:
    def test_records_provider_reported_tokens(self, groq_only) -> None:
        client = MagicMock()
        client.chat.completions.create.return_value = _openai_response("hi", 120, 34, 154)

        events: list[UsageEvent] = []
        with patch("groq.Groq", return_value=client), usage_sink(events.append):
            assert complete("planner", [{"role": "user", "content": "x"}], model="m") == "hi"

        assert len(events) == 1
        e = events[0]
        assert (e.prompt_tokens, e.completion_tokens, e.total_tokens) == (120, 34, 154)
        assert (e.provider, e.purpose, e.model, e.status) == ("groq", "planner", "m", "ok")
        assert e.streamed is False
        assert e.counted is True

    def test_anthropic_total_is_derived_from_its_two_fields(self, monkeypatch) -> None:
        # Anthropic reports input/output and no total. Without normalisation a
        # dashboard summing total_tokens reads every Anthropic call as free.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("LLM_PROVIDER_ORDER", "anthropic")
        client = MagicMock()
        client.messages.create.return_value = _anthropic_response("ok", 200, 50)

        events: list[UsageEvent] = []
        with patch("anthropic.Anthropic", return_value=client), usage_sink(events.append):
            complete("insights", [{"role": "user", "content": "x"}])

        assert (events[0].prompt_tokens, events[0].completion_tokens) == (200, 50)
        assert events[0].total_tokens == 250

    def test_response_without_usage_is_marked_uncounted_not_zero(self, groq_only) -> None:
        # A proxied deployment that strips usage must be distinguishable from a
        # genuinely free call, or the dashboard silently under-reports.
        client = MagicMock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="hi"))]
        )

        events: list[UsageEvent] = []
        with patch("groq.Groq", return_value=client), usage_sink(events.append):
            complete("planner", [{"role": "user", "content": "x"}], model="m")

        assert events[0].counted is False
        assert events[0].total_tokens == 0

    def test_failed_attempt_is_recorded_with_fallback_depth(self, monkeypatch) -> None:
        monkeypatch.setenv("GROQ_API_KEY", "test-key")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("LLM_PROVIDER_ORDER", "groq,anthropic")
        anthropic_client = MagicMock()
        anthropic_client.messages.create.return_value = _anthropic_response("saved", 10, 5)

        events: list[UsageEvent] = []
        with patch("groq.Groq", side_effect=RuntimeError("rate limited")), \
             patch("anthropic.Anthropic", return_value=anthropic_client), \
             usage_sink(events.append):
            # A model must be passed for the Groq leg to be attempted at all:
            # it is the one provider that takes the caller's literal model
            # string, so `model=None` makes the chain skip it silently.
            complete("coder", [{"role": "user", "content": "x"}], model="m")

        assert [(e.provider, e.status, e.fallback_depth) for e in events] == [
            ("groq", "error", 0),
            ("anthropic", "ok", 1),
        ]
        assert "rate limited" in events[0].error

    def test_sink_failure_never_breaks_the_call(self, groq_only) -> None:
        # Metering is bookkeeping. A broken sink must not turn a working answer
        # into an error.
        client = MagicMock()
        client.chat.completions.create.return_value = _openai_response("still fine", 1, 1, 2)

        def exploding_sink(_event):
            raise RuntimeError("database is down")

        with patch("groq.Groq", return_value=client), usage_sink(exploding_sink):
            assert complete("planner", [{"role": "user", "content": "x"}], model="m") == "still fine"

    def test_no_sink_installed_is_a_no_op(self, groq_only) -> None:
        client = MagicMock()
        client.chat.completions.create.return_value = _openai_response("fine", 1, 1, 2)
        with patch("groq.Groq", return_value=client):
            assert complete("planner", [{"role": "user", "content": "x"}], model="m") == "fine"


class TestStreamingUsage:
    def test_usage_chunk_with_empty_choices_is_counted(self, groq_only) -> None:
        # The regression this whole module exists for.
        client = MagicMock()
        client.chat.completions.create.return_value = iter(
            [_text_chunk("Hel"), _text_chunk("lo"), _usage_chunk(900, 100, 1000)]
        )

        events: list[UsageEvent] = []
        with patch("groq.Groq", return_value=client), usage_sink(events.append):
            assert "".join(stream_complete("copilot", [{"role": "user", "content": "x"}], model="m")) == "Hello"

        assert len(events) == 1
        assert events[0].total_tokens == 1000
        assert (events[0].prompt_tokens, events[0].completion_tokens) == (900, 100)
        assert events[0].streamed is True
        assert events[0].status == "ok"

    def test_include_usage_is_requested_from_the_provider(self, groq_only) -> None:
        # Without this flag the provider never sends the usage chunk at all, so
        # the fix above would have nothing to absorb.
        client = MagicMock()
        client.chat.completions.create.return_value = iter([_text_chunk("hi"), _usage_chunk(5, 2, 7)])

        with patch("groq.Groq", return_value=client), usage_sink(lambda _e: None):
            list(stream_complete("copilot", [{"role": "user", "content": "x"}], model="m"))

        _, kwargs = client.chat.completions.create.call_args
        assert kwargs["stream_options"] == {"include_usage": True}
        assert kwargs["stream"] is True

    def test_provider_rejecting_stream_options_still_streams(self, groq_only) -> None:
        # Degrading to an unmetered stream beats failing the request over to a
        # different model because of a bookkeeping flag.
        client = MagicMock()

        def create(**kwargs):
            if "stream_options" in kwargs:
                raise TypeError("unexpected keyword argument 'stream_options'")
            return iter([_text_chunk("still"), _text_chunk(" works")])

        client.chat.completions.create.side_effect = create

        events: list[UsageEvent] = []
        with patch("groq.Groq", return_value=client), usage_sink(events.append):
            out = "".join(stream_complete("copilot", [{"role": "user", "content": "x"}], model="m"))

        assert out == "still works"
        assert events[0].status == "ok"
        assert events[0].counted is False

    def test_abandoned_stream_is_recorded_as_cancelled(self, groq_only) -> None:
        # The copilot's stop button. Those tokens were spent even though the
        # provider never got to report a count.
        client = MagicMock()
        client.chat.completions.create.return_value = iter(
            [_text_chunk("a"), _text_chunk("b"), _usage_chunk(10, 10, 20)]
        )

        events: list[UsageEvent] = []
        with patch("groq.Groq", return_value=client), usage_sink(events.append):
            gen = stream_complete("copilot", [{"role": "user", "content": "x"}], model="m")
            assert next(gen) == "a"
            gen.close()

        assert len(events) == 1
        assert events[0].status == "cancelled"
        assert events[0].streamed is True

    def test_empty_stream_falls_back_and_records_both_attempts(self, monkeypatch) -> None:
        monkeypatch.setenv("GROQ_API_KEY", "test-key")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("LLM_PROVIDER_ORDER", "groq,anthropic")

        groq_client = MagicMock()
        groq_client.chat.completions.create.return_value = iter([])

        anthropic_client = MagicMock()
        stream_ctx = MagicMock()
        stream_ctx.__enter__.return_value = SimpleNamespace(
            text_stream=iter(["from ", "anthropic"]),
            get_final_message=lambda: _anthropic_response("x", 40, 12),
        )
        stream_ctx.__exit__.return_value = False
        anthropic_client.messages.stream.return_value = stream_ctx

        events: list[UsageEvent] = []
        with patch("groq.Groq", return_value=groq_client), \
             patch("anthropic.Anthropic", return_value=anthropic_client), \
             usage_sink(events.append):
            out = "".join(stream_complete("copilot", [{"role": "user", "content": "x"}], model="m"))

        assert out == "from anthropic"
        assert [(e.provider, e.status) for e in events] == [("groq", "error"), ("anthropic", "ok")]
        assert events[1].total_tokens == 52  # derived from Anthropic's two fields
