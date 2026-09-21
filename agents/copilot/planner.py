"""Planner — decomposes a copilot turn into an ordered list of tool steps.

The single-tool ``router`` (``router.py``) still powers the blocking ``/ask``
endpoint. The streaming endpoint uses THIS planner instead, which is a superset:
one structured-output LLM call that returns either

  * a ``clarify`` (ambiguous / off-topic),
  * a ``follow_up`` (explain the previous answer — same as the router), or
  * an ordered list of 1–``_MAX_STEPS`` tool steps to run and then synthesize.

Most questions still resolve to a single step (identical cost + latency to the
old router — it IS the router for these). Multi-step is reserved for questions
that genuinely need more than one analysis, e.g. "forecast revenue AND tell me
who's about to churn". Reuses the router's tool catalogue verbatim so the two
never drift.
"""

from __future__ import annotations

from agents.constants import DEFAULT_COPILOT_MODEL
from tools.llm_client import complete

from .router import _BASE_TOOLS, _FOLLOW_UP_TOOL, _extract_json

_MAX_STEPS = 3
_TOOL_ROUTES = {"visualization", "insights", "forecasting", "marketing", "churn", "general"}


_PLAN_SHAPE = """
Respond with a single JSON object only — no prose, no markdown fences:
{"steps": [{"tool": "<one tool name>", "forecast_horizon_days": <int, forecasting only, infer from phrasing: "next month"~30, "next quarter"~90, "next year"~365, default 30>, "forecast_metric_hint": "<forecasting only: the single metric the user named in their own words, e.g. \\"revenue\\"; empty string if none>"}], "clarify_question": "<only when a step's tool is clarify, else empty>"}

Rules for "steps":
- Put ONE step per DISTINCT analysis the question asks for, in the order they \
should run. MOST questions need exactly ONE step — only add more when the user \
clearly asks for several different things at once (e.g. a forecast AND a churn \
list AND a campaign). Never add a step the user didn't ask for.
- Use AT MOST 3 steps. Never repeat the same tool twice.
- If the question is ambiguous/off-topic, return a single step with tool \
"clarify" and fill "clarify_question".
- If the user is asking you to explain/dig into your MOST RECENT answer (not a \
new question), return a single step with tool "follow_up".
"""


def _build_planner_prompt(has_previous_result: bool, last_route: str | None) -> str:
    intro = (
        "You are the planning supervisor for DAAS, a business "
        "intelligence platform. Read the user's question and decide which "
        "internal analytics tool(s) must run to answer it, and in what order. "
        "Respond with a single JSON object only — no prose, no markdown fences.\n\n"
    )
    tools = _BASE_TOOLS + (_FOLLOW_UP_TOOL if has_previous_result else "")
    context = (
        f'\n\nYour previous answer was produced by the "{last_route}" tool.'
        if has_previous_result and last_route
        else "\n\nNote: this is the first question in the conversation — there is no "
        'previous answer yet, so "follow_up" is not a valid choice right now.'
        if not has_previous_result
        else ""
    )
    return intro + tools + context + "\n" + _PLAN_SHAPE


def _coerce_horizon(raw: object) -> int:
    try:
        return max(7, min(365, int(raw)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 30


def plan_steps(
    message: str,
    history: list[dict[str, str]] | None,
    has_previous_result: bool,
    last_route: str | None,
    model: str | None,
) -> dict:
    """Return ``{"mode": "clarify"|"follow_up"|"tools", "clarify_question": str,
    "steps": [ {tool, forecast_horizon_days, forecast_metric_hint} ]}``.

    Never raises: a router/LLM hiccup degrades to a single ``general`` step,
    exactly like ``router._route`` degrades to the general fallback.
    """
    history = history or []
    convo = "\n".join(f"{m['role']}: {m['content']}" for m in history[-6:])
    user_msg = (f"Recent conversation:\n{convo}\n\n" if convo else "") + f"New question: {message}"
    system_prompt = _build_planner_prompt(has_previous_result, last_route)

    try:
        raw = complete(
            "copilot",
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            model=model or DEFAULT_COPILOT_MODEL,
            temperature=0.1,
            max_tokens=400,
            json_mode=True,
        )
        data = _extract_json(raw)
    except Exception:
        # Never hard-fail a chat turn on a planner hiccup.
        return {"mode": "tools", "clarify_question": "", "steps": [_general_step()]}

    return _normalize_plan(data, has_previous_result)


def _general_step() -> dict:
    return {"tool": "general", "forecast_horizon_days": 30, "forecast_metric_hint": ""}


def _normalize_plan(data: dict, has_previous_result: bool) -> dict:
    raw_steps = data.get("steps")
    if not isinstance(raw_steps, list):
        raw_steps = []

    tools_in_order = [str((s or {}).get("tool", "")).strip().lower() for s in raw_steps if isinstance(s, dict)]

    # follow_up and clarify are single-mode outcomes — they never mix with tools.
    if "follow_up" in tools_in_order:
        if has_previous_result:
            return {"mode": "follow_up", "clarify_question": "", "steps": []}
        # Model asked to explain a previous answer that doesn't exist — treat as
        # a fresh general question instead of explaining nothing.
        tools_in_order = [t for t in tools_in_order if t != "follow_up"] or ["general"]

    if "clarify" in tools_in_order or not any(t in _TOOL_ROUTES for t in tools_in_order):
        return {
            "mode": "clarify",
            "clarify_question": str(data.get("clarify_question") or "").strip()
            or "Could you clarify what you'd like to know?",
            "steps": [],
        }

    steps: list[dict] = []
    seen: set[str] = set()
    for src in raw_steps:
        if not isinstance(src, dict):
            continue
        tool = str(src.get("tool", "")).strip().lower()
        if tool not in _TOOL_ROUTES or tool in seen:
            continue
        seen.add(tool)
        steps.append(
            {
                "tool": tool,
                "forecast_horizon_days": _coerce_horizon(src.get("forecast_horizon_days", 30)),
                "forecast_metric_hint": str(src.get("forecast_metric_hint") or "").strip(),
            }
        )
        if len(steps) >= _MAX_STEPS:
            break

    if not steps:
        steps = [_general_step()]
    return {"mode": "tools", "clarify_question": "", "steps": steps}
