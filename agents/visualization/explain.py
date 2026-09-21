"""Generic "explain this chart" capability — used both by the standalone
explain button on any chart in the app, and by the Analyst Copilot's
visualization route follow-ups (``agents/copilot/tools.py``).

Grounding discipline: the prompt is built ONLY from ``describe_figure``'s
deterministic summary of the figure's own data (``tools/sandbox.py``) — never
the LLM-generated code that produced it, never a live DataFrame. Same
"narrate, never invent" pattern as the rest of this codebase.
"""

from __future__ import annotations

import json

from tools.llm_client import complete
from tools.sandbox import describe_figure

from ..constants import DEFAULT_EXPLAIN_CHART_MODEL

_EXPLAIN_CHART_SYSTEM = """You are DAAS, explaining a chart to a business user who may not \
have a technical or data-analysis background. You are given ONLY a structured summary of the \
chart's data — not the chart image, not the code that built it. Rules:
1. Use ONLY the numbers/labels in the summary below — never invent or estimate a value that isn't \
there.
2. Say what type of chart this is, what the axes/categories represent, the main pattern (trend, \
biggest/smallest values, notable outliers), and the standout number(s) — in plain, jargon-free \
language, as if explaining it to someone seeing it for the first time.
3. Keep it to 2-5 sentences unless the chart clearly has enough distinct content to warrant more.
4. If a title or extra context is given, use it to say why this chart might be shown — but do not \
invent detail beyond what's given.
5. If the summary is too thin to say anything meaningful (e.g. no numeric data at all), say so \
plainly instead of guessing.
"""


def explain_chart(
    figure: dict,
    *,
    title: str | None = None,
    context: str | None = None,
    model: str | None = None,
) -> str:
    """Return a grounded, plain-language explanation of a Plotly figure."""
    summary = describe_figure(figure)

    parts = []
    if title:
        parts.append(f"Chart title: {title}\n\n")
    if context:
        parts.append(f"Context: {context}\n\n")
    parts.append(f"Chart data summary:\n```json\n{json.dumps(summary, indent=2, default=str)}\n```")

    return complete(
        "explain_chart",
        [
            {"role": "system", "content": _EXPLAIN_CHART_SYSTEM},
            {"role": "user", "content": "".join(parts)},
        ],
        model=model or DEFAULT_EXPLAIN_CHART_MODEL,
        temperature=0.3,
        max_tokens=500,
    ).strip()
