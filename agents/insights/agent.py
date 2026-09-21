"""
Insights Agent — writes a decision brief from figures it is not allowed to compute.

The division of labour is absolute:

  * **Python computes.**  Every number in the output comes from
    :mod:`agents.analytics` and :mod:`agents.insights.decision_metrics`, is
    registered in a :class:`~agents.insights.figures.FigureRegistry` with the
    formula that produced it, and is substituted into the text after generation.
  * **The model writes.**  It receives a ranked brief and an allow-list of
    citation tokens, and its entire job is judgement and prose — which finding
    leads, what a business owner should do about it, how to say it in one clear
    sentence.

Two prompt-level consequences of that split are easy to miss and matter a lot:

  1. The analytics JSON is **no longer dumped into the prompt**.  A model shown a
     nested payload will lift plausible numbers out of it; a model shown only a
     curated token list cannot.
  2. The dataset preview is **structure only** — column names, types and detected
     roles, never sample rows or ``describe()`` output.  Sample values are the
     other place a model finds a number to "remember" and later state as a fact.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from agents.constants import DEFAULT_INSIGHTS_MODEL
from tools.llm_client import complete, first_available_provider
from tools.token_budget import estimate_tokens, prompt_budget

from .decision_metrics import compute_decision_metrics
from .evidence import EvidenceItem, build_evidence, render_evidence
from .figures import FigureRegistry, build_registry
from .template_selector import select_template

logger = logging.getLogger(__name__)

_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)

# Generation ceiling for one report, and half of a sizing problem that is easy
# to get wrong. Groq bills a completion as ``prompt + max_tokens`` against an
# 8,000-token/minute budget on the on-demand tier, so this number is not a
# harmless upper bound — it is budget spent whether the model writes that much
# or not. A finished decision brief runs roughly 1,000–1,600 tokens; 3,000 left
# so little room for the prompt that the request was rejected 413 before the
# model ever ran. 2,400 keeps a comfortable margin over the longest real report
# while returning ~600 tokens of budget to the context.
MAX_REPORT_TOKENS = 2400


def strip_reasoning(text: str) -> str:
    """Remove chain-of-thought blocks some models (e.g. qwen) emit."""
    return _THINK_RE.sub("", text).strip()


_PROMPT_DIR = Path(__file__).resolve().parent.parent.parent / "prompts" / "insights"

_TEMPLATES = {
    "decision_brief": "decision_brief.md",
    "non_technical": "non_technical.md",
    "executive": "executive.md",
    "detailed": "detailed.md",
}


def _load_template(stem: str) -> str:
    path = _PROMPT_DIR / _TEMPLATES.get(stem, "decision_brief.md")
    return path.read_text(encoding="utf-8")


# The contract is repeated in the user turn (not only in the template) because
# it is the one rule whose violation silently produces a wrong report, and the
# last instruction before the task is the one models follow most reliably.
CITATION_CONTRACT = """\
HOW TO WRITE NUMBERS — this overrides every other instruction:

1. You may NOT type digits. Every number is a citation token from ALLOWED FIGURES,
   written exactly as `{{key}}`. The system replaces it with the exact computed value.
2. Only tokens in the list exist. Inventing `{{key}}` names produces a visible
   "figure unavailable" marker in the published report.
3. Do NOT do arithmetic. Do not add, subtract, divide, average, annualise, convert
   or "roughly estimate" any figure. If the number you want is not in the list, the
   correct sentence is "not measurable from this data".
4. Product names, weekday names and dates are tokens too — use them, do not retype
   them from memory.
5. A figure marked `[scenario: ...]` is arithmetic under an assumption, never a
   prediction. If you cite one, you MUST state its assumption in the same bullet.
"""


def build_schema_brief(df: pd.DataFrame | None, payload: dict) -> str:
    """Structure-only description of the dataset for the prompt.

    Deliberately excludes sample rows and summary statistics: those are numbers,
    and every number in the prompt is a number the model might quote as a
    finding. The model needs to know what the business *has* (a cost column, a
    channel column), not what any particular row says.
    """
    meta = payload.get("metadata", {}) or {}
    schema = (payload.get("kpi", {}) or {}).get("_schema") or payload.get("schema") or {}
    lines = [
        f"Rows: {meta.get('row_count')}   Columns: {meta.get('column_count')}",
        f"Columns present: {', '.join(str(c) for c in (meta.get('columns') or []))}",
    ]
    roles = [
        ("date", schema.get("time_column")),
        ("revenue", (schema.get("monetary_columns") or [None])[0]),
        ("quantity", schema.get("quantity_column")),
        ("product", schema.get("product_column")),
        ("customer", schema.get("customer_column")),
        ("order id", schema.get("order_column")),
    ]
    detected = ", ".join(f"{role} = '{col}'" for role, col in roles if col)
    if detected:
        lines.append(f"Detected roles: {detected}")
    cats = schema.get("category_columns") or []
    if cats:
        lines.append(f"Grouping columns available: {', '.join(str(c) for c in cats[:6])}")
    return "\n".join(lines)


@dataclass
class InsightsDraft:
    """One generated draft plus everything needed to verify and correct it."""

    raw: str
    template_stem: str
    registry: FigureRegistry
    decision: dict[str, Any]
    evidence: list[EvidenceItem] = field(default_factory=list)
    system_prompt: str = ""
    user_prompt: str = ""


def prepare_context(
    data_df: pd.DataFrame | None,
    analytics_payload: dict,
) -> tuple[FigureRegistry, dict, list[EvidenceItem]]:
    """Run the deterministic half: decision metrics → registry → ranked brief."""
    decision = compute_decision_metrics(data_df) if data_df is not None else {"available": False}
    registry = build_registry(analytics_payload, decision)
    evidence = build_evidence(analytics_payload, decision, registry)
    return registry, decision, evidence


def build_user_prompt(
    *,
    schema_brief: str,
    business_context: str,
    registry: FigureRegistry,
    evidence: list[EvidenceItem],
    blind_spots: list[str],
    figure_table: str | None = None,
) -> str:
    """Assemble the user turn: context, allow-list, ranked brief, contract.

    ``figure_table`` overrides the allow-list rendering when the caller has
    already sized it to a token budget; omitted, the full table is used.
    """
    parts = [
        "## THE DATASET (structure only — no values, so you cannot quote from it)\n",
        schema_brief,
        "\n\n## BUSINESS CONTEXT\n",
        business_context.strip() if business_context.strip() else
        "None supplied. Say so in one sentence at the top and write the report from the data alone.",
        "\n\n## ALLOWED FIGURES — the complete set of numbers that exist for this report\n",
        "Cite by token. The value shown is what will be printed.\n\n",
        registry.prompt_table() if figure_table is None else figure_table,
        "\n\n## THE BRIEF — findings already ranked by money at stake\n",
        "This ordering is computed, not editorial. Lead with item 1. Do not give every "
        "item equal weight, and do not reorder them to be gentler.\n\n",
        render_evidence(evidence),
    ]
    if blind_spots:
        parts.append(
            "\n\n## WHAT THIS DATA PROVABLY CANNOT ANSWER\n"
            "Report these honestly in the blind-spots section. Never fill one of these gaps "
            "with an assumption.\n\n"
            + "\n".join(f"- {b}" for b in blind_spots)
        )
    parts.append("\n\n## TASK\n")
    parts.append(
        "Write the report in the required structure. Judgement is yours; numbers are not.\n\n"
    )
    parts.append(CITATION_CONTRACT)
    return "".join(parts)


def size_figure_table(
    *,
    registry: FigureRegistry,
    evidence: list[EvidenceItem],
    system_prompt: str,
    user_prompt_without_table: str,
    reserved_completion: int = MAX_REPORT_TOKENS,
) -> tuple[str, int]:
    """Render the allow-list at the largest size the token budget allows.

    Returns ``(table, offered_count)``.

    The budget belongs to whichever provider will actually answer, and it covers
    the *whole* request — system template, brief, contract, allow-list and the
    reserved completion together. Everything except the allow-list is fixed, so
    the allow-list is what gets sized: it is both the largest block and the only
    one that degrades gracefully, since the registry is ordered by how much a
    figure matters to the decision.

    Figures the ranked brief already cites are kept first regardless of length.
    Showing the model a number in the brief while denying it the token to cite
    it is precisely the pressure that makes it type digits instead.
    """
    provider = first_available_provider()
    budget = prompt_budget(provider, reserved_completion) if provider else 0
    if budget <= 0:
        return registry.prompt_table(), len(registry.all())
    fixed = estimate_tokens(system_prompt) + estimate_tokens(user_prompt_without_table)
    return registry.prompt_table_within(
        budget - fixed, priority=registry.used_keys(render_evidence(evidence)),
    )


def generate_insights_draft(
    data_df: pd.DataFrame | None,
    analytics_payload: dict,
    *,
    business_context: str = "",
    model: str | None = None,
    template_override: str | None = None,
) -> InsightsDraft:
    """Generate one draft report (tokens un-substituted) and its verification context."""
    registry, decision, evidence = prepare_context(data_df, analytics_payload)
    template_stem = template_override or select_template(
        business_context=business_context, analytics_payload=analytics_payload,
    )
    system_prompt = _load_template(template_stem)
    prompt_parts = {
        "schema_brief": build_schema_brief(data_df, analytics_payload),
        "business_context": business_context,
        "registry": registry,
        "evidence": evidence,
        "blind_spots": (decision or {}).get("blind_spots", []) or [],
    }
    # Two passes: the first measures everything that is not the allow-list, the
    # second rebuilds with an allow-list sized to whatever budget is left.
    figure_table, offered = size_figure_table(
        registry=registry,
        evidence=evidence,
        system_prompt=system_prompt,
        user_prompt_without_table=build_user_prompt(**prompt_parts, figure_table=""),
    )
    if offered < len(registry.all()):
        # Worth a line in the log: it explains a report that cites fewer figures
        # than the audit trail lists, and it is the first thing to check after
        # raising LLM_TPM_LIMIT on a new provider plan.
        logger.info(
            "Insights allow-list trimmed to %s of %s figures to fit the token budget",
            offered, len(registry.all()),
        )
    user_prompt = build_user_prompt(**prompt_parts, figure_table=figure_table)

    raw = complete(
        "insights",
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        model=model or DEFAULT_INSIGHTS_MODEL,
        temperature=0.2,
        # Kept close to the real length of a finished brief: providers reserve
        # `max_tokens` against a per-minute token budget, so an oversized ceiling
        # is what starves the correction pass that may follow.
        max_tokens=MAX_REPORT_TOKENS,
    )
    return InsightsDraft(
        raw=strip_reasoning(raw),
        template_stem=template_stem,
        registry=registry,
        decision=decision,
        evidence=evidence,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
    )


def generate_insights(
    data_df: pd.DataFrame | None,
    table_name: str,
    business_context: str = "",
    analytics_payload: dict | None = None,
    model: str | None = None,
    template_override: str | None = None,
) -> tuple[str, str]:
    """Backwards-compatible wrapper: returns ``(rendered_report_md, template_stem)``.

    Callers that need the verification certificate should use
    ``backend.app.services.insights_service.generate_grounded_insights`` instead —
    this wrapper renders the citations but does not run the correction loop.
    """
    from agents.analytics.engine import run_analytics

    payload = analytics_payload if analytics_payload is not None else run_analytics(data_df, table_name)
    draft = generate_insights_draft(
        data_df, payload,
        business_context=business_context, model=model, template_override=template_override,
    )
    rendered, _unknown = draft.registry.render(draft.raw)
    return rendered, draft.template_stem
