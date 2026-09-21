"""Hermetic tests for the multi-step, token-streaming copilot (no network, no DB).

Covers four layers:
  1. ``tools.llm_client.stream_complete`` — provider fallback + mid-stream rules.
  2. ``agents.copilot.narrate`` planners — narration/explain/synthesis prompt builders.
  3. ``agents.copilot.planner._normalize_plan`` — turning a raw LLM plan into an
     ordered, validated step list (clarify / follow_up / 1–3 tools).
  4. ``backend.app.services.copilot_stream.stream_copilot_events`` — the full SSE
     frame sequence (meta → step* → token* → done, or a terminal error), with the
     planner, tools, and LLM stream mocked out. The in-memory session store is
     used for real (needs no DB), so the follow-up caching path runs end-to-end.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agents.copilot.narrate import plan_explain, plan_narration, plan_synthesis
from agents.copilot.planner import _normalize_plan
from tools.llm_client import AllProvidersFailedError, stream_complete


# ── stream_complete ──────────────────────────────────────────────────────────


def _groq_chunk(text: str | None):
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))])


class _FakeAnthropicStream:
    """Stand-in for the context manager returned by ``client.messages.stream``."""

    def __init__(self, texts):
        self._texts = texts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def text_stream(self):
        return iter(self._texts)


class TestStreamComplete:
    def test_groq_streams_deltas_in_order(self, monkeypatch) -> None:
        monkeypatch.setenv("GROQ_API_KEY", "test-key")
        monkeypatch.setenv("LLM_PROVIDER_ORDER", "groq")

        client = MagicMock()
        # Includes an empty delta (real streams send these) — must be skipped.
        client.chat.completions.create.return_value = [
            _groq_chunk("Rev"), _groq_chunk(None), _groq_chunk("enue up"),
        ]

        with patch("groq.Groq", return_value=client):
            out = list(stream_complete("copilot", [{"role": "user", "content": "hi"}], model="m"))

        assert out == ["Rev", "enue up"]
        _, kwargs = client.chat.completions.create.call_args
        assert kwargs["stream"] is True
        assert kwargs["model"] == "m"

    def test_falls_back_to_anthropic_when_groq_fails_before_first_token(self, monkeypatch) -> None:
        monkeypatch.setenv("GROQ_API_KEY", "test-key")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("LLM_PROVIDER_ORDER", "groq,anthropic")

        anthropic_client = MagicMock()
        anthropic_client.messages.stream.return_value = _FakeAnthropicStream(["from ", "anthropic"])

        with patch("groq.Groq", side_effect=RuntimeError("rate limited")), \
             patch("anthropic.Anthropic", return_value=anthropic_client):
            out = list(stream_complete("copilot", [{"role": "user", "content": "hi"}], model="m"))

        assert "".join(out) == "from anthropic"

    def test_empty_stream_triggers_fallback(self, monkeypatch) -> None:
        monkeypatch.setenv("GROQ_API_KEY", "test-key")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("LLM_PROVIDER_ORDER", "groq,anthropic")

        groq_client = MagicMock()
        groq_client.chat.completions.create.return_value = [_groq_chunk(None), _groq_chunk("")]
        anthropic_client = MagicMock()
        anthropic_client.messages.stream.return_value = _FakeAnthropicStream(["ok"])

        with patch("groq.Groq", return_value=groq_client), \
             patch("anthropic.Anthropic", return_value=anthropic_client):
            out = list(stream_complete("copilot", [{"role": "user", "content": "hi"}], model="m"))

        assert out == ["ok"]

    def test_mid_stream_failure_reraises_after_partial_output(self, monkeypatch) -> None:
        # Once a provider has emitted a token we must NOT silently continue on a
        # different provider — the partial answer is already on the wire.
        monkeypatch.setenv("GROQ_API_KEY", "test-key")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("LLM_PROVIDER_ORDER", "groq,anthropic")

        def _boom():
            yield _groq_chunk("partial ")
            raise RuntimeError("mid-stream boom")

        groq_client = MagicMock()
        groq_client.chat.completions.create.return_value = _boom()
        anthropic_client = MagicMock()
        anthropic_client.messages.stream.return_value = _FakeAnthropicStream(["SHOULD NOT APPEAR"])

        with patch("groq.Groq", return_value=groq_client), \
             patch("anthropic.Anthropic", return_value=anthropic_client):
            gen = stream_complete("copilot", [{"role": "user", "content": "hi"}], model="m")
            assert next(gen) == "partial "
            with pytest.raises(RuntimeError, match="mid-stream boom"):
                next(gen)

    def test_all_providers_failing_raises(self, monkeypatch) -> None:
        monkeypatch.setenv("GROQ_API_KEY", "test-key")
        monkeypatch.setenv("LLM_PROVIDER_ORDER", "groq")

        with patch("groq.Groq", side_effect=RuntimeError("down")):
            with pytest.raises(AllProvidersFailedError):
                list(stream_complete("copilot", [{"role": "user", "content": "hi"}], model="m"))


# ── planners ─────────────────────────────────────────────────────────────────


class TestPlanners:
    def test_plan_narration_streams_for_data_tool(self) -> None:
        tool_result = {"tool": "forecasting", "raw_payload": {"outputs": [{"metric": "revenue"}]}}
        plan = plan_narration("forecast revenue", tool_result, "m")
        assert "final" not in plan
        assert plan["model"] == "m"
        assert plan["max_tokens"] == 400
        assert plan["messages"][0]["role"] == "system"
        assert "revenue" in plan["messages"][1]["content"]

    def test_plan_narration_passthrough_for_general(self) -> None:
        tool_result = {"tool": "general", "raw_payload": {"answer": "Total revenue is $5k."}}
        plan = plan_narration("total revenue?", tool_result, "m")
        assert plan == {"final": "Total revenue is $5k."}

    def test_plan_narration_final_on_tool_error(self) -> None:
        tool_result = {"tool": "churn", "raw_payload": {"error": "no data"}}
        plan = plan_narration("who will churn?", tool_result, "m")
        assert plan["final"].startswith("I ran into a problem")

    def test_plan_explain_builds_grounded_prompt(self) -> None:
        last = {"tool": "forecasting", "raw_payload": {"outputs": [{"metric": "revenue", "forecasted_value": 42}]}}
        plan = plan_explain("why?", [{"role": "user", "content": "forecast"}], last, "m")
        assert plan["max_tokens"] == 700
        assert "42" in plan["messages"][1]["content"]

    def test_plan_synthesis_includes_every_payload(self) -> None:
        results = [
            {"tool": "forecasting", "raw_payload": {"forecasted_value": 42}},
            {"tool": "churn", "raw_payload": {"at_risk": 17}},
        ]
        plan = plan_synthesis("forecast and churn?", results, "m")
        body = plan["messages"][1]["content"]
        assert "forecasting" in body and "churn" in body
        assert "42" in body and "17" in body


# ── planner normalization (pure, no LLM) ─────────────────────────────────────


class TestNormalizePlan:
    def test_single_tool(self) -> None:
        out = _normalize_plan({"steps": [{"tool": "insights"}]}, has_previous_result=False)
        assert out["mode"] == "tools"
        assert [s["tool"] for s in out["steps"]] == ["insights"]

    def test_multi_tool_preserves_order_and_dedupes(self) -> None:
        out = _normalize_plan(
            {"steps": [{"tool": "forecasting"}, {"tool": "churn"}, {"tool": "forecasting"}]},
            has_previous_result=False,
        )
        assert [s["tool"] for s in out["steps"]] == ["forecasting", "churn"]

    def test_caps_at_three_steps(self) -> None:
        out = _normalize_plan(
            {"steps": [{"tool": t} for t in ["visualization", "insights", "forecasting", "marketing", "churn"]]},
            has_previous_result=False,
        )
        assert len(out["steps"]) == 3

    def test_forecast_horizon_clamped(self) -> None:
        out = _normalize_plan(
            {"steps": [{"tool": "forecasting", "forecast_horizon_days": 9999}]}, has_previous_result=False
        )
        assert out["steps"][0]["forecast_horizon_days"] == 365

    def test_clarify_mode(self) -> None:
        out = _normalize_plan({"steps": [{"tool": "clarify"}], "clarify_question": "Which?"}, has_previous_result=False)
        assert out["mode"] == "clarify"
        assert out["clarify_question"] == "Which?"

    def test_follow_up_allowed_only_with_history(self) -> None:
        with_hist = _normalize_plan({"steps": [{"tool": "follow_up"}]}, has_previous_result=True)
        assert with_hist["mode"] == "follow_up"
        # No prior answer → cannot explain nothing → falls back to a fresh general step.
        without = _normalize_plan({"steps": [{"tool": "follow_up"}]}, has_previous_result=False)
        assert without["mode"] == "tools"
        assert without["steps"][0]["tool"] == "general"

    def test_garbage_falls_back_to_general(self) -> None:
        out = _normalize_plan({"steps": "not a list"}, has_previous_result=False)
        assert out["mode"] == "clarify" or out["steps"][0]["tool"] == "general"


# ── stream_copilot_events (SSE frame sequence) ───────────────────────────────


def _collect(gen) -> list[dict]:
    """Parse ``data: {json}\\n\\n`` frames into a list of event dicts."""
    events = []
    for frame in gen:
        assert frame.startswith("data: ")
        assert frame.endswith("\n\n")
        events.append(json.loads(frame[len("data: "):].strip()))
    return events


def _run_events(monkeypatch, *, plan, run_tool_fn=None, tool_result=None, tokens=None,
                conversation_id=None, seed=None, message="hello"):
    import backend.app.services.copilot_stream as cs
    from backend.app.services.copilot_sessions import get_or_create_session, update_session

    if seed is not None:
        session = get_or_create_session(conversation_id, "proj-1")
        update_session(session.id, route=seed[0], tool_result=seed[1])
        conversation_id = session.id

    monkeypatch.setattr(cs, "plan_steps", lambda *a, **k: plan)
    if run_tool_fn is not None:
        monkeypatch.setattr(cs, "run_tool", run_tool_fn)
    elif tool_result is not None:
        monkeypatch.setattr(cs, "run_tool", lambda tool, state: {"tool_result": tool_result})
    if tokens is not None:
        monkeypatch.setattr(cs, "stream_complete", lambda *a, **k: iter(tokens))

    gen = cs.stream_copilot_events(
        project_id="proj-1", message=message, history=[], model="m",
        model_prefs={}, conversation_id=conversation_id,
    )
    return _collect(gen)


def _tools_plan(*tools):
    return {
        "mode": "tools", "clarify_question": "",
        "steps": [{"tool": t, "forecast_horizon_days": 30, "forecast_metric_hint": ""} for t in tools],
    }


class TestStreamCopilotEvents:
    def test_single_data_tool_meta_step_tokens_done(self, monkeypatch) -> None:
        tool_result = {
            "tool": "forecasting",
            "figure": {"data": [], "layout": {}},
            "table": [{"metric": "revenue", "forecasted_value": 42}],
            "report_md": "# Forecast",
            "raw_payload": {"outputs": [{"metric": "revenue"}], "grounded": True},
        }
        events = _run_events(
            monkeypatch, plan=_tools_plan("forecasting"), tool_result=tool_result,
            tokens=["Revenue ", "is up."],
        )

        assert events[0]["type"] == "meta"
        assert [p["route_label"] for p in events[0]["plan"]] == ["Forecasting Agent"]

        step_frames = [e for e in events if e["type"] == "step"]
        assert [s["status"] for s in step_frames] == ["running", "done"]

        assert "".join(e["text"] for e in events if e["type"] == "token") == "Revenue is up."

        done = events[-1]
        assert done["type"] == "done"
        assert done["answer"] == "Revenue is up."
        assert done["route"] == "forecasting"
        assert len(done["artifacts"]) == 1
        art = done["artifacts"][0]
        assert art["figure"] == {"data": [], "layout": {}}
        assert art["table"] == [{"metric": "revenue", "forecasted_value": 42}]
        assert art["report_md"] == "# Forecast"
        assert art["grounded"] is True
        assert art["tool_error"] is None

    def test_multi_step_runs_all_and_synthesizes(self, monkeypatch) -> None:
        results = {
            "forecasting": {"tool": "forecasting", "figure": {"data": []}, "table": None,
                            "report_md": "# F", "raw_payload": {"grounded": True}},
            "churn": {"tool": "churn", "figure": None, "table": [{"customer": "c1"}],
                      "report_md": "# C", "raw_payload": {"grounded": True}},
        }
        events = _run_events(
            monkeypatch, plan=_tools_plan("forecasting", "churn"),
            run_tool_fn=lambda tool, state: {"tool_result": results[tool]},
            tokens=["Revenue rises ", "while 1 customer is at risk."],
        )

        # Whole plan announced up front.
        assert [p["route"] for p in events[0]["plan"]] == ["forecasting", "churn"]
        # Both steps report running + done.
        step_frames = [(s["route"], s["status"]) for s in events if s["type"] == "step"]
        assert step_frames == [
            ("forecasting", "running"), ("forecasting", "done"),
            ("churn", "running"), ("churn", "done"),
        ]
        assert "".join(e["text"] for e in events if e["type"] == "token").startswith("Revenue rises")

        done = events[-1]
        assert done["route"] == "churn"  # last step is the primary
        assert [a["route"] for a in done["artifacts"]] == ["forecasting", "churn"]
        assert done["artifacts"][1]["table"] == [{"customer": "c1"}]

    def test_general_typewriter_no_llm_stream(self, monkeypatch) -> None:
        import backend.app.services.copilot_stream as cs
        monkeypatch.setattr(cs, "_TYPEWRITER_DELAY_S", 0)  # keep the test instant

        def _poison(*a, **k):
            raise AssertionError("stream_complete must not run for a verified general answer")

        monkeypatch.setattr(cs, "stream_complete", _poison)
        tool_result = {
            "tool": "general", "figure": None, "table": None, "report_md": None,
            "raw_payload": {"answer": "Total revenue is $5,000 across 320 orders.", "grounded": True},
        }
        events = _run_events(monkeypatch, plan=_tools_plan("general"), tool_result=tool_result)

        token_text = "".join(e["text"] for e in events if e["type"] == "token")
        assert token_text == "Total revenue is $5,000 across 320 orders."
        assert events[-1]["answer"] == "Total revenue is $5,000 across 320 orders."
        assert events[-1]["artifacts"][0]["grounded"] is True

    def test_clarify_emits_question_without_running_tools(self, monkeypatch) -> None:
        import backend.app.services.copilot_stream as cs

        def _poison(*a, **k):
            raise AssertionError("no tool should run for clarify")

        monkeypatch.setattr(cs, "run_tool", _poison)
        events = _run_events(
            monkeypatch,
            plan={"mode": "clarify", "clarify_question": "Which metric did you mean?", "steps": []},
        )

        assert events[0]["type"] == "meta"
        assert events[0]["plan"][0]["route_label"] == "Clarifying question"
        assert not any(e["type"] == "step" for e in events)
        assert events[1]["type"] == "token"
        assert events[1]["text"] == "Which metric did you mean?"
        assert events[-1]["answer"] == "Which metric did you mean?"
        assert events[-1]["artifacts"] == []

    def test_follow_up_uses_cached_payload_and_labels_it(self, monkeypatch) -> None:
        seed_result = {"tool": "forecasting", "raw_payload": {"outputs": [{"metric": "revenue", "forecasted_value": 42}]}}
        events = _run_events(
            monkeypatch,
            plan={"mode": "follow_up", "clarify_question": "", "steps": []},
            tokens=["Because ", "revenue is 42."],
            conversation_id="conv-followup",
            seed=("forecasting", seed_result),
        )

        assert events[0]["plan"][0]["route_label"] == "Forecasting Agent · follow-up"
        assert "".join(e["text"] for e in events if e["type"] == "token") == "Because revenue is 42."
        done = events[-1]
        assert done["route"] == "follow_up"
        assert done["artifacts"] == []

    def test_llm_failure_before_first_token_yields_fallback(self, monkeypatch) -> None:
        import backend.app.services.copilot_stream as cs

        def _boom(*a, **k):
            raise AllProvidersFailedError("all down")

        monkeypatch.setattr(cs, "stream_complete", _boom)
        tool_result = {"tool": "insights", "figure": None, "table": None,
                       "report_md": "# Insights", "raw_payload": {"grounded": True}}
        events = _run_events(monkeypatch, plan=_tools_plan("insights"), tool_result=tool_result)

        assert any(e["type"] == "token" and "couldn't phrase it" in e["text"] for e in events)
        assert events[-1]["type"] == "done"
        assert events[-1]["artifacts"][0]["report_md"] == "# Insights"

    def test_planner_failure_yields_terminal_error_frame(self, monkeypatch) -> None:
        import backend.app.services.copilot_stream as cs

        def _explode(*a, **k):
            raise RuntimeError("db down")

        monkeypatch.setattr(cs, "plan_steps", _explode)
        gen = cs.stream_copilot_events(
            project_id="proj-1", message="x", history=[], model="m",
            model_prefs={}, conversation_id=None,
        )
        events = _collect(gen)
        assert len(events) == 1
        assert events[0]["type"] == "error"
