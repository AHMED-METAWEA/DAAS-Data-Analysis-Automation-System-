"""Router node — one structured-output LLM call picks exactly one tool per turn.

Uses the same ``complete(..., json_mode=True)`` structured-output convention
already established in ``agents/marketing/agent.py``'s campaign planner —
there is no native provider function-calling in this codebase, so routing
goes through this same pattern instead. The returned tool name is always
validated against a server-side allow-list before anything branches on it.
"""

from __future__ import annotations

import json
import re

from agents.constants import DEFAULT_COPILOT_MODEL
from tools.llm_client import complete

from .state import CopilotState

_VALID_ROUTES = {
    "visualization",
    "insights",
    "forecasting",
    "marketing",
    "churn",
    "general",
    "clarify",
    "follow_up",
}

_BASE_TOOLS = """Tools:
- "visualization": the user wants a NEW chart/plot of specific data (e.g. "show me \
revenue by month as a bar chart", "plot churn risk vs recency").
- "insights": the user wants a broad "what's happening / why / what should we do" \
business narrative synthesized from many KPIs at once (e.g. "why did revenue \
decline last month?", "give me a business health summary").
- "forecasting": the user wants a projection of a metric into the future (e.g. \
"forecast next quarter's revenue", "what will orders look like in 30 days?").
- "marketing": the user wants customer segmentation, campaign, or \
retention-marketing strategy (e.g. "who are our best customers?", "build a \
win-back campaign").
- "churn": the user is asking specifically about churn risk / which customers are \
likely to leave (e.g. "who is about to churn?", "why is churn high?").
- "general": a single precise factual/numeric lookup or simple computation that \
doesn't need a full report or a new chart (e.g. "what's our total revenue?", \
"how many customers do we have?", "what's the average order value?").
- "clarify": the question is too ambiguous or off-topic to route confidently — you \
will ask ONE short clarifying question instead of guessing."""

_FOLLOW_UP_TOOL = """
- "follow_up": the user is asking YOU to explain, clarify, or dig deeper into your \
MOST RECENT answer above — not a brand-new question. Use this ONLY for things like \
"why is that", "what do you mean by that number", "I don't understand this part", \
"that seems to contradict what I saw earlier", "can you explain more", "so what \
should I actually do about it". If the message could be answered fresh without \
needing your last answer as context, it is NOT a follow_up — route it normally."""

_NO_FOLLOW_UP_NOTE = """
Note: this is the first question in the conversation — there is no previous answer \
yet, so "follow_up" is not a valid choice right now."""

_RESPONSE_SHAPE = """
Respond with this exact JSON shape:
{"tool": "<one of the above>", "reasoning": "<one short internal sentence>", \
"clarifying_question": "<only when tool is clarify, else empty string>", \
"forecast_horizon_days": <int, only meaningful when tool is forecasting; infer \
from phrasing — "next month"~30, "next quarter"~90, "next year"~365; default 30>, \
"forecast_metric_hint": "<only when tool is forecasting: the single metric name the \
user means, in their own words, e.g. "revenue", "orders", "quantity" — empty string \
if they didn't name one (forecasting ALL metrics is expensive, so only ever name ONE)>}
"""


def _build_system_prompt(has_previous_result: bool, last_route: str | None) -> str:
    intro = (
        "You are the routing supervisor for DAAS, a business intelligence "
        "platform. Read the user's question and pick EXACTLY ONE tool to answer it. "
        "Respond with a single JSON object only — no prose, no markdown fences.\n\n"
    )
    tools = _BASE_TOOLS + (_FOLLOW_UP_TOOL if has_previous_result else "")
    context = (
        f'\n\nYour previous answer was produced by the "{last_route}" tool.'
        if has_previous_result and last_route
        else _NO_FOLLOW_UP_NOTE if not has_previous_result else ""
    )
    return intro + tools + context + "\n" + _RESPONSE_SHAPE


def _extract_json(raw: str) -> dict:
    raw = raw.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fence:
        raw = fence.group(1)
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        brace = re.search(r"\{.*\}", raw, re.DOTALL)
        if brace:
            return json.loads(brace.group(0))
        raise


def _route(state: CopilotState) -> dict:
    history = state.get("history", [])[-6:]
    convo = "\n".join(f"{m['role']}: {m['content']}" for m in history)
    user_msg = (f"Recent conversation:\n{convo}\n\n" if convo else "") + f"New question: {state['message']}"
    has_previous_result = bool(state.get("has_previous_result"))
    system_prompt = _build_system_prompt(has_previous_result, state.get("last_route"))

    try:
        raw = complete(
            "copilot",
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            model=state.get("model") or DEFAULT_COPILOT_MODEL,
            temperature=0.1,
            max_tokens=300,
            json_mode=True,
        )
        data = _extract_json(raw)
        tool = str(data.get("tool", "")).strip().lower()
        if tool not in _VALID_ROUTES:
            tool = "general"
        if tool == "follow_up" and not has_previous_result:
            # Defensive: the prompt already forbids this, but never let a
            # follow_up route reach `_explain` with nothing to explain.
            tool = "general"
        horizon = data.get("forecast_horizon_days", 30)
        try:
            horizon = max(7, min(365, int(horizon)))
        except (TypeError, ValueError):
            horizon = 30
        return {
            "route": tool,
            "forecast_horizon_days": horizon,
            "forecast_metric_hint": str(data.get("forecast_metric_hint") or "").strip(),
            "clarify_question": str(data.get("clarifying_question") or "").strip(),
            "route_error": "",
        }
    except Exception as exc:
        # Never hard-fail a chat turn on a router hiccup — degrade to the
        # general grounded-chat fallback, which is always available.
        return {
            "route": "general",
            "forecast_horizon_days": 30,
            "forecast_metric_hint": "",
            "clarify_question": "",
            "route_error": f"Router error: {exc}",
        }
