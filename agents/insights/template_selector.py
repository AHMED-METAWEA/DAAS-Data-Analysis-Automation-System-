"""
Template Selector — analyzes analytics payload + business context to
pick the best insight template for the audience.

Detection strategy:
  1. Check business context for explicit tone/audience keywords.
  2. Otherwise write the decision brief — the default.
  3. Fall through to detailed only when explicitly requested.

The default is ``decision_brief`` rather than ``non_technical`` because the
question a user actually arrives with is "what should I do?", not "what does my
data say?". The plain-language, executive and technical templates remain for
readers who explicitly ask for that register.

Returns the template file stem (e.g. "decision_brief", "executive", "detailed").
"""

from __future__ import annotations

import re

KEYWORD_MAP: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(board|executive|c[eé]o|cfo|leadership|director)\b", re.IGNORECASE), "executive"),
    (re.compile(r"\b(brief|concise|summary|high.level|tl;dr|snapshot)\b", re.IGNORECASE), "executive"),
    (re.compile(r"\b(detailed|technical|comprehensive|in.depth|full|deep.dive)\b", re.IGNORECASE), "detailed"),
    (re.compile(r"\b(auditor|analyst|data.team|expert)\b", re.IGNORECASE), "detailed"),
    (re.compile(r"\b(plain.language|simple|beginner|explain.like|non.technical)\b", re.IGNORECASE),
     "non_technical"),
]

DEFAULT_TEMPLATE = "decision_brief"


def select_template(
    business_context: str = "",
    analytics_payload: dict | None = None,
) -> str:
    """
    Pick the best insight template.

    Parameters
    ----------
    business_context : str
        Free-text business context from the user (industry, goals, etc.).
    analytics_payload : dict or None
        Output of ``run_analytics()`` — used for heuristic fallback when
        there is no explicit audience signal.

    Returns
    -------
    str
        One of ``"decision_brief"``, ``"non_technical"``, ``"executive"``,
        ``"detailed"``.
    """
    # 1. Explicit keyword match in business context — the user asked for a register
    for pattern, template in KEYWORD_MAP:
        if pattern.search(business_context):
            return template

    # 2. Very wide datasets aimed at a data-literate reader still warrant the
    #    full technical breakdown.
    if analytics_payload:
        col_count = (analytics_payload.get("metadata") or {}).get("column_count", 0)
        kpi = analytics_payload.get("kpi", {})
        if col_count > 30 and kpi.get("revenue") is not None:
            return "detailed"

    # 3. Default: answer "what should I do?", which is what a user came for.
    return DEFAULT_TEMPLATE


def describe_template(template: str) -> str:
    """Return a human-readable label for the selected template."""
    descriptions = {
        "decision_brief": "Decision brief — what changed, why, and what to do",
        "non_technical": "Plain-language story for non-technical readers",
        "executive": "Concise board-level executive briefing",
        "detailed": "Comprehensive technical BI report (10 sections)",
    }
    return descriptions.get(template, template)
