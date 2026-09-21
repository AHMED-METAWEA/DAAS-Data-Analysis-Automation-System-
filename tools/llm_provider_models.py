"""Per-purpose model selection for each non-Groq provider in the fallback chain.

Groq is special-cased in ``tools/llm_client.py``: it always honors the
caller's literal ``model`` string (the sidebar's per-agent picker from
``agents.constants.AVAILABLE_MODELS``). Model IDs are not portable across
providers, so every other provider needs its own fixed choice here.

Add a new purpose by adding one key to each provider's dict below.
"""

from __future__ import annotations

_PURPOSES = (
    "planner",
    "coder",
    "viz",
    "dashboard",
    "insights",
    "forecast",
    "marketing",
    "churn",
    "schema_discovery",
    "copilot",
    "explain_chart",
)

PROVIDER_MODELS: dict[str, dict[str, str]] = {
    "anthropic": dict.fromkeys(_PURPOSES, "claude-sonnet-5"),
    "openai": dict.fromkeys(_PURPOSES, "gpt-4o-mini"),
    "openrouter": dict.fromkeys(_PURPOSES, "meta-llama/llama-3.3-70b-instruct"),
}


# ── Reasoning effort ────────────────────────────────────────────────────────
#
# Reasoning models spend part of their completion budget thinking before they
# write, and those tokens are billed against ``max_tokens`` while forming no
# part of the answer. On a per-minute budget that is not a detail: at Groq's
# default effort, `openai/gpt-oss-120b` spent 1,062 tokens reasoning about a
# decision brief, the answer was cut off at the ceiling mid-sentence, and a
# slightly longer prompt pushed reasoning over the whole ceiling — returning an
# empty `content` and surfacing to the user as "Groq returned an empty
# response". The same report at low effort costs 16 reasoning tokens and
# finishes cleanly.
#
# These agents do not need deliberation. Every number is computed in Python and
# inserted after generation, the findings arrive already ranked, and the model's
# job is judgement and prose over a brief that has done the analysis for it.
#
# Keyed by model prefix because Groq's accepted values differ per family —
# gpt-oss takes low/medium/high and rejects "none", which is exactly what
# qwen3.6 needs to stop emitting `<think>` blocks inline in `content`.
GROQ_REASONING_EFFORT: dict[str, str] = {
    "openai/gpt-oss": "low",
    "qwen/qwen3.6": "none",
}


def groq_reasoning_effort(model: str) -> str | None:
    """The ``reasoning_effort`` to send for *model*, or ``None`` to omit it.

    Omitted rather than defaulted: the parameter is rejected outright by models
    that do not reason, and a 400 here would push a working request onto the
    next provider in the chain.
    """
    for prefix, effort in GROQ_REASONING_EFFORT.items():
        if (model or "").startswith(prefix):
            return effort
    return None
