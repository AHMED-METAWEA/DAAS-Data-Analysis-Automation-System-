"""Narration node — turns a tool's structured result into a short grounded answer.

Grounding discipline: the prompt is built ONLY from the tool's deterministic
``raw_payload`` (never from another LLM's already-generated ``report_md``),
matching this codebase's core AI-safety pattern — the LLM narrates numbers
it's shown, it never invents them.
"""

from __future__ import annotations

import json

from agents.constants import DEFAULT_COPILOT_MODEL
from tools.llm_client import complete

from .state import CopilotState

_NARRATE_SYSTEM = """You are DAAS's copilot, giving a short conversational \
answer after running one internal analytics tool for the user. You will be shown \
ONLY the structured data that tool actually produced. Rules:
1. Use ONLY the numbers in the JSON payload below — never invent or estimate a \
figure that isn't there.
2. Directly answer the user's question in 2-4 sentences, citing concrete numbers.
3. A fuller report/chart is being shown to the user right below your answer — do \
not repeat it verbatim, just introduce/interpret it.
4. If the payload has no numeric content (e.g. a chart was generated but you cannot \
see its rendered values), describe what the chart depicts based on the user's \
request and any printed output given, and do not state specific numeric values you \
cannot see.
"""

_SYNTHESIS_SYSTEM = """You are DAAS's copilot. To answer ONE question from \
the user you just ran SEVERAL internal analytics tools, and you're shown each tool's \
structured output below. Write ONE coherent answer that ties them together. Rules:
1. Use ONLY numbers present in the payloads below — never invent, estimate, or carry \
a figure from one tool's payload into a claim about another.
2. Answer the user's question directly, weaving the analyses into a single narrative \
(don't just list each tool's result separately). Make the connection between them \
explicit where the data supports one — but never assert a causal/associative link the \
payloads don't actually show.
3. Cite concrete numbers, and when a figure could be ambiguous, name which analysis it \
came from. Charts/tables/reports for each analysis are shown to the user below your \
answer, so introduce and interpret them rather than repeating them verbatim.
4. Keep it tight — a few short paragraphs, not an essay.
"""

_EXPLAIN_SYSTEM = """You are DAAS's copilot, continuing a conversation with \
a user about analysis you already gave them. They are asking you to explain, \
clarify, or dig deeper into your MOST RECENT answer — not asking a new question \
that needs fresh data. You are given the SAME structured payload that answer was \
grounded in, plus the conversation so far. Rules:
1. Use ONLY the numbers in the JSON payload below — never invent or estimate a \
figure that isn't there. If answering properly needs a number that genuinely isn't \
in this payload, say so plainly — don't fabricate one to seem helpful.
2. Write like an analyst who's actually in the room with them: as long and as \
detailed as the question needs. You are NOT limited to 2-4 sentences here — that \
constraint was only for the first-pass summary, not for a real follow-up.
3. If the user seems confused, or is pushing back on something / says it doesn't \
match what they expected, address that directly using the numbers already shown — \
don't just restate the original answer in different words.
4. You only have this one payload to work from — don't imply you ran any new \
analysis, and don't reference other tools/data you weren't given.
"""


# ── Prompt planners (single source of truth for narration/explain prompts) ────
# Both the blocking graph nodes below and the streaming endpoint
# (`backend/app/services/copilot_stream.py`) build their prompts here, so the
# streamed answer is grounded exactly like the non-streamed one. A planner
# returns either ``{"final": <text>}`` — an answer that needs no LLM call and so
# can't be token-streamed — or ``{"messages": [...], "model", "temperature",
# "max_tokens"}`` describing the (streamable) call to make.


def plan_narration(message: str, tool_result: dict | None, model: str | None) -> dict:
    """Plan the first-pass narration of one tool's structured result."""
    tool_result = tool_result or {}
    payload = tool_result.get("raw_payload") or {}

    if tool_result.get("tool") == "general":
        # run_grounded_chat's own answer is already grounded and already has
        # any execution error appended inline as text — narrating over it
        # again would be a redundant, ungrounded second hop, and checking
        # payload["error"] here would wrongly discard a perfectly good answer
        # (that field is informational, not a hard-failure marker).
        return {"final": payload.get("answer", "")}

    if payload.get("error"):
        return {"final": f"I ran into a problem answering that: {payload['error']}"}

    return {
        "messages": [
            {"role": "system", "content": _NARRATE_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"User's question: {message}\n\n"
                    f"Tool used: {tool_result.get('tool')}\n\n"
                    f"Structured payload:\n```json\n{json.dumps(payload, indent=2, default=str)}\n```"
                ),
            },
        ],
        "model": model or DEFAULT_COPILOT_MODEL,
        "temperature": 0.3,
        "max_tokens": 400,
    }


def plan_synthesis(message: str, tool_results: list[dict], model: str | None) -> dict:
    """Plan a synthesis over MULTIPLE tools' structured results (multi-step turn).

    Always returns a streamable ``{"messages", ...}`` plan — grounding is over the
    union of every step's ``raw_payload``, so no numbers outside the tools' own
    output can slip in.
    """
    sections = []
    for tr in tool_results:
        tr = tr or {}
        payload = tr.get("raw_payload") or {}
        sections.append(
            f"### Tool: {tr.get('tool', 'unknown')}\n```json\n{json.dumps(payload, indent=2, default=str)}\n```"
        )
    return {
        "messages": [
            {"role": "system", "content": _SYNTHESIS_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"User's question: {message}\n\n"
                    f"You ran {len(tool_results)} tools. Their structured outputs:\n\n"
                    + "\n\n".join(sections)
                ),
            },
        ],
        "model": model or DEFAULT_COPILOT_MODEL,
        "temperature": 0.3,
        "max_tokens": 700,
    }


def plan_explain(message: str, history: list[dict] | None, last_tool_result: dict | None, model: str | None) -> dict:
    """Plan a follow-up explanation grounded in the previous turn's payload."""
    last = last_tool_result or {}
    payload = last.get("raw_payload") or {}
    tool = last.get("tool", "the previous analysis")
    convo = "\n".join(f"{m['role']}: {m['content']}" for m in (history or [])[-6:])

    return {
        "messages": [
            {"role": "system", "content": _EXPLAIN_SYSTEM},
            {
                "role": "user",
                "content": (
                    (f"Conversation so far:\n{convo}\n\n" if convo else "")
                    + f"Follow-up question: {message}\n\n"
                    f"Tool your last answer came from: {tool}\n\n"
                    f"Structured payload it was grounded in:\n```json\n{json.dumps(payload, indent=2, default=str)}\n```"
                ),
            },
        ],
        "model": model or DEFAULT_COPILOT_MODEL,
        "temperature": 0.3,
        "max_tokens": 700,
    }


def _clarify(state: CopilotState) -> dict:
    return {"narration": state.get("clarify_question") or "Could you clarify what you'd like to know?"}


def _explain(state: CopilotState) -> dict:
    plan = plan_explain(
        state["message"], state.get("history", []), state.get("last_tool_result"), state.get("model"),
    )
    try:
        raw = complete(
            "copilot", plan["messages"],
            model=plan["model"], temperature=plan["temperature"], max_tokens=plan["max_tokens"],
        )
        return {"narration": raw.strip()}
    except Exception as exc:
        return {"narration": f"I couldn't put together an explanation for that ({exc})."}


def _narrate(state: CopilotState) -> dict:
    plan = plan_narration(state["message"], state.get("tool_result"), state.get("model"))
    if "final" in plan:
        return {"narration": plan["final"]}
    try:
        raw = complete(
            "copilot", plan["messages"],
            model=plan["model"], temperature=plan["temperature"], max_tokens=plan["max_tokens"],
        )
        return {"narration": raw.strip()}
    except Exception as exc:
        return {"narration": f"I found the answer but couldn't phrase it ({exc}). See the details below."}
