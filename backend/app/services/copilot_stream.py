"""Server-Sent-Events generator for the streaming Analyst Copilot endpoint.

This is where the copilot's three "best copilot" capabilities live:

  * **Multi-step reasoning** — one question can trigger several agents. A planner
    (``agents.copilot.planner.plan_steps``) decomposes the turn into an ordered
    list of 1–3 tool steps; each runs in turn (with a live ``step`` progress
    frame), then a single grounded ``synthesis`` ties them together. Single-step
    questions cost exactly what they did before (the planner IS the router then).
  * **Streaming** — the synthesis / narration / explain LLM calls are streamed
    token-by-token via ``stream_complete``.
  * **Stream everything (safely)** — a ``general`` factual answer is produced by
    ``run_grounded_chat``, whose numbers are *verified/corrected after* the LLM
    finishes (the codebase's anti-fabrication guarantee). Streaming raw draft
    tokens would defeat that, so instead the already-VERIFIED answer is revealed
    with a genuine typewriter (``_typewriter``). We never stream an unverified
    token.

The heavy work (``run_tool`` — which may fit a forecasting/churn model) is
synchronous, so this is a **sync** generator: Starlette drives it in a threadpool
and each ``data:`` frame is flushed the instant it's yielded.

Event protocol — every frame is one ``data: <json>\\n\\n`` line with a ``type``:
  - ``meta``  {plan:[{route,route_label}], conversation_id} — the whole plan, up
    front, so the UI can show every agent that's about to run.
  - ``step``  {index, total, route, route_label, status:"running"|"done"} — per
    step progress (multi-step turns).
  - ``token`` {text} — one chunk of the answer. Emitted repeatedly.
  - ``done``  {answer, artifacts:[{route,route_label,figure,table,report_md,
    grounded,tool_error}], route, route_label, conversation_id}.
  - ``error`` {message} — a fatal error; no ``done`` follows.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Iterator
from typing import Any

from agents.copilot.narrate import plan_explain, plan_narration, plan_synthesis
from agents.copilot.planner import plan_steps
from agents.copilot.tools import run_tool
from backend.app.services.copilot_sessions import get_or_create_session, update_session
from backend.app.services.json_safe import json_safe
from tools.llm_client import stream_complete

logger = logging.getLogger(__name__)

ROUTE_LABELS = {
    "visualization": "Visualization Agent",
    "insights": "Business Insights Agent",
    "forecasting": "Forecasting Agent",
    "marketing": "Marketing Agent",
    "churn": "Churn Agent",
    "general": "General Q&A",
    "clarify": "Clarifying question",
}

# Routes whose structured result is worth caching for a follow-up turn. Mirrors
# the non-streaming /ask endpoint: "clarify" computed nothing, and "follow_up"
# answers *from* the cache without overwriting it.
CACHEABLE_ROUTES = {"visualization", "insights", "forecasting", "marketing", "churn", "general"}

_WORD_RE = re.compile(r"\S+\s*")
_TYPEWRITER_MAX_CHUNKS = 60
_TYPEWRITER_DELAY_S = 0.02


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, default=str)}\n\n"


def _label(route: str) -> str:
    return ROUTE_LABELS.get(route, "General Q&A")


def _follow_up_label(last_route: str | None) -> str:
    return f"{ROUTE_LABELS.get(last_route or '', 'Copilot')} · follow-up"


def _typewriter(text: str) -> Iterator[str]:
    """Reveal already-computed, already-verified text as a typewriter. Batches
    words so a long answer never emits more than ~``_TYPEWRITER_MAX_CHUNKS``
    frames (keeps the reveal ~1s regardless of length)."""
    words = _WORD_RE.findall(text)
    if not words:
        if text:
            yield text
        return
    batch = max(1, (len(words) + _TYPEWRITER_MAX_CHUNKS - 1) // _TYPEWRITER_MAX_CHUNKS)
    for i in range(0, len(words), batch):
        yield "".join(words[i : i + batch])
        if i + batch < len(words):
            time.sleep(_TYPEWRITER_DELAY_S)


def _stream_answer(plan: dict, fallback: str) -> Iterator[str]:
    """Stream one LLM narration/synthesis/explain plan as ``token`` frames.
    Yields SSE strings; the generator's *return value* is the final answer text.
    Handles provider failure: a failure before the first token emits ``fallback``;
    a mid-answer failure keeps the partial text already delivered."""
    parts: list[str] = []
    produced = False
    try:
        for delta in stream_complete(
            "copilot", plan["messages"],
            model=plan["model"], temperature=plan["temperature"], max_tokens=plan["max_tokens"],
        ):
            produced = True
            parts.append(delta)
            yield _sse({"type": "token", "text": delta})
    except Exception as exc:  # noqa: BLE001
        if not produced:
            yield _sse({"type": "token", "text": fallback})
            return fallback
        logger.warning("copilot stream failed mid-answer: %s", exc)

    text = "".join(parts).strip()
    if not text:
        yield _sse({"type": "token", "text": fallback})
        return fallback
    return text


def _artifact(tool_result: dict, route: str) -> dict:
    raw_payload = tool_result.get("raw_payload") or {}
    table = tool_result.get("table")
    return {
        "route": route,
        "route_label": _label(route),
        "figure": tool_result.get("figure"),
        "table": json_safe(table) if table else None,
        "report_md": tool_result.get("report_md"),
        "grounded": raw_payload.get("grounded"),
        "tool_error": raw_payload.get("error"),
    }


def stream_copilot_events(
    *,
    project_id: str,
    message: str,
    history: list[dict[str, str]],
    model: str,
    model_prefs: dict[str, str],
    conversation_id: str | None,
) -> Iterator[str]:
    """Yield SSE frames for one copilot turn. Never raises: any failure is
    surfaced as a terminal ``error`` frame so the stream always closes cleanly."""
    try:
        session = get_or_create_session(conversation_id, project_id)
        last_route = session.last_route
        last_tool_result = session.last_tool_result
        has_previous_result = last_tool_result is not None

        plan = plan_steps(message, history, has_previous_result, last_route, model)
        mode = plan["mode"]

        # ── clarify — a single short question, no tools, no LLM answer call ──
        if mode == "clarify":
            question = plan["clarify_question"]
            yield _sse({"type": "meta", "plan": [{"route": "clarify", "route_label": _label("clarify")}],
                        "conversation_id": session.id})
            yield _sse({"type": "token", "text": question})
            yield _sse({
                "type": "done", "route": "clarify", "route_label": _label("clarify"),
                "answer": question, "artifacts": [], "conversation_id": session.id,
            })
            return

        # ── follow_up — explain the previous turn, streamed from its cache ──
        if mode == "follow_up":
            label = _follow_up_label(last_route)
            yield _sse({"type": "meta", "plan": [{"route": "follow_up", "route_label": label}],
                        "conversation_id": session.id})
            eplan = plan_explain(message, history, last_tool_result, model)
            answer = yield from _stream_answer(eplan, "I couldn't put together an explanation for that.")
            yield _sse({
                "type": "done", "route": "follow_up", "route_label": label,
                "answer": answer, "artifacts": [], "conversation_id": session.id,
            })
            return

        # ── tools — run each planned step, then answer over the result(s) ──
        steps = plan["steps"]
        plan_labels = [{"route": s["tool"], "route_label": _label(s["tool"])} for s in steps]
        yield _sse({"type": "meta", "plan": plan_labels, "conversation_id": session.id})

        tool_results: list[dict] = []
        total = len(steps)
        for i, step in enumerate(steps):
            route = step["tool"]
            yield _sse({"type": "step", "index": i, "total": total, "route": route,
                        "route_label": _label(route), "status": "running"})
            state = {
                "project_id": project_id,
                "message": message,
                "history": history,
                "model": model,
                "model_prefs": model_prefs,
                "forecast_horizon_days": step.get("forecast_horizon_days", 30),
                "forecast_metric_hint": step.get("forecast_metric_hint", ""),
            }
            result = run_tool(route, state)
            tool_result = result.get("tool_result") or {}
            tool_results.append(tool_result)
            yield _sse({"type": "step", "index": i, "total": total, "route": route,
                        "route_label": _label(route), "status": "done"})

        # ── produce the answer text ──
        single = total == 1
        first_route = steps[0]["tool"]

        if single and first_route == "general":
            # Already-verified grounded answer — reveal it, never re-narrate it.
            raw_payload = tool_results[0].get("raw_payload") or {}
            answer = raw_payload.get("answer") or "I couldn't process that question."
            for chunk in _typewriter(answer):
                yield _sse({"type": "token", "text": chunk})
        elif single:
            nplan = plan_narration(message, tool_results[0], model)
            if "final" in nplan:
                answer = nplan["final"]
                if answer:
                    yield _sse({"type": "token", "text": answer})
            else:
                answer = yield from _stream_answer(
                    nplan, "I found the answer but couldn't phrase it. See the details below.",
                )
        else:
            splan = plan_synthesis(message, tool_results, model)
            answer = yield from _stream_answer(
                splan, "I ran the analyses but couldn't summarize them. See the details below.",
            )

        answer = (answer or "").strip() or "I couldn't process that question."

        # Cache the LAST step's result so a follow-up ("explain that") digs into
        # the analysis the user just saw finish.
        primary_route = steps[-1]["tool"]
        if primary_route in CACHEABLE_ROUTES:
            update_session(session.id, route=primary_route, tool_result=tool_results[-1])

        artifacts = [_artifact(tr, s["tool"]) for tr, s in zip(tool_results, steps)]
        yield _sse({
            "type": "done",
            "route": primary_route,
            "route_label": _label(primary_route),
            "answer": answer,
            "artifacts": artifacts,
            "conversation_id": session.id,
        })
    except Exception:  # noqa: BLE001
        logger.exception("copilot stream failed")
        yield _sse({"type": "error", "message": "The copilot ran into an internal error. Please try again."})
