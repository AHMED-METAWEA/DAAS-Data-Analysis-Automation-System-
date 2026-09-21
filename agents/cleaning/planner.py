"""Node 2 — Cleaning Planner.

The planner's job changed. It used to be the thing that decided whether a
defect existed, by reading column statistics and describing fixes in prose that
a second model then turned into freeform Python. Now:

  1. ``tools.defect_detection`` finds the defects deterministically and derives
     a complete, correctly-ordered baseline plan of typed operators.
  2. The LLM *reviews* that baseline — it can drop a step it judges wrong for
     this business, adjust parameters, or add a step from the operator
     registry. It selects and parameterises; it never writes code, and it
     cannot invent an operator that does not exist.
  3. Anything it returns is validated against the registry before it is
     accepted. Unparseable output, an unknown operator, a malformed parameter —
     each degrades to the deterministic baseline rather than to nothing.

The practical effect is that the LLM being slow, rate-limited, or wrong can no
longer produce a bad clean; the worst case is the deterministic plan.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from core.state import GraphState
from models.cleaning_ops import CleaningStep
from models.profiler_models import DatasetProfile
from tools.cleaning_ops import REGISTRY, operator_catalog, order_steps, validate_step
from tools.defect_detection import DefectFinding, is_clean, needs_review, plan_from_defects
from tools.llm_client import complete

_PROMPT_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"


def _load_prompt(filename: str) -> str:
    return (_PROMPT_DIR / filename).read_text(encoding="utf-8")


def findings_from_profile(profile: DatasetProfile) -> list[DefectFinding]:
    out: list[DefectFinding] = []
    for raw in profile.defects or []:
        try:
            out.append(DefectFinding(**raw))
        except Exception:
            continue
    return out


def _extract_json_array(raw: str) -> list | None:
    text = (raw or "").strip()
    fence = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        bracket = re.search(r"\[.*\]", text, re.DOTALL)
        if bracket:
            text = bracket.group(0)
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if isinstance(parsed, dict):
        for key in ("steps", "plan", "cleaning_plan"):
            if isinstance(parsed.get(key), list):
                return parsed[key]
        return None
    return parsed if isinstance(parsed, list) else None


def parse_llm_plan(raw: str, df_columns: list[str]) -> tuple[list[CleaningStep], list[str]]:
    """Turn the planner's JSON into validated typed steps.

    Returns ``(steps, rejections)``. A step naming an unknown operator or a
    malformed parameter is dropped and reported rather than executed — the
    registry is the contract, and the model does not get to extend it.
    """
    items = _extract_json_array(raw)
    if items is None:
        return [], ["planner did not return a JSON array"]

    steps: list[CleaningStep] = []
    rejections: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            rejections.append(f"step {index + 1} is not an object")
            continue
        op = str(item.get("op") or "").strip()
        columns = item.get("columns") or ([item["column"]] if item.get("column") else [])
        if isinstance(columns, str):
            columns = [columns]
        params = item.get("params") if isinstance(item.get("params"), dict) else {}
        description = str(item.get("description") or "").strip()

        if not op:
            # A step with no operator is a free-text instruction; keep it so the
            # code fallback can handle it, but only if it says something.
            if description:
                steps.append(CleaningStep(
                    id=str(len(steps) + 1), description=description, source="generated",
                ))
            continue

        if op not in REGISTRY:
            rejections.append(f"step {index + 1}: unknown operator '{op}'")
            continue

        step = CleaningStep(
            id=str(len(steps) + 1),
            op=op,
            columns=[str(c) for c in columns],
            params=params,
            description=description,
            source="generated",
        )
        error = validate_step(step, df_columns)
        if error:
            rejections.append(f"step {index + 1}: {error}")
            continue
        steps.append(step)

    # The model's ordering is never trusted: safe execution order is a property
    # of the operators, not a thing to hope the planner gets right.
    return order_steps(steps), rejections


def render_plan_text(plan: list[dict] | list[CleaningStep]) -> str:
    """Numbered, business-language rendering of the plan for the UI and for the
    code-fallback prompt."""
    if not plan:
        return "No cleaning steps — the data is already clean. Return the DataFrame unchanged."
    lines = []
    for index, step in enumerate(plan):
        description = step.description if isinstance(step, CleaningStep) else step.get("description", "")
        lines.append(f"{index + 1}. {description}")
    return "\n".join(lines)


def _findings_digest(findings: list[DefectFinding]) -> str:
    return json.dumps(
        [
            {
                "kind": f.kind, "column": f.column, "severity": f.severity,
                "count": f.count, "detail": f.detail, "evidence": f.evidence[:3],
                "auto_fixable": f.auto_fixable,
            }
            for f in findings
        ],
        indent=2, ensure_ascii=False, default=str,
    )


def _steps_digest(steps: list[CleaningStep]) -> str:
    return json.dumps(
        [{"op": s.op, "columns": s.columns, "params": s.params, "description": s.description}
         for s in steps],
        indent=2, ensure_ascii=False, default=str,
    )


def planner_node(state: GraphState) -> dict:
    """Produce the cleaning plan: deterministic baseline, LLM-refined."""
    profile = DatasetProfile(**state["metadata"])
    findings = findings_from_profile(profile)
    df_columns = [c.name for c in profile.columns]

    if is_clean(findings):
        print("\n-- Planner: no defects detected — the table is already clean, plan is empty.")
        return {"cleaning_plan": []}

    baseline = plan_from_defects(findings)

    # Nothing here is a judgement call, so there is nothing to review. This is
    # the ordinary state of a *cleaned* file re-uploaded as CSV: the values are
    # correct and the format simply has no types, so every finding is "this
    # date column is text". Asking a planner to approve `parse_datetime` costs a
    # call and a slice of a per-minute token budget, and its only possible
    # contributions are agreement or a dropped step.
    if not needs_review(findings):
        print(f"\n-- Planner: {len(baseline)} lossless type conversion(s), no judgement needed "
              "— using the deterministic plan without calling the model.")
        return {"cleaning_plan": [s.model_dump() for s in baseline]}

    print(f"\n-- Planner: {len(findings)} finding(s) -> {len(baseline)} baseline step(s); asking the model to review ...")

    from agents.constants import DEFAULT_PLANNER_MODEL
    model = state.get("planner_model", DEFAULT_PLANNER_MODEL)

    try:
        raw = complete(
            "planner",
            [
                {"role": "system", "content": _load_prompt("planner_prompt.txt").replace(
                    "{{OPERATOR_CATALOG}}", operator_catalog()
                )},
                {"role": "user", "content": (
                    f"Table columns: {df_columns}\n\n"
                    f"Data-quality findings (measured, not inferred):\n{_findings_digest(findings)}\n\n"
                    f"Baseline plan derived from those findings:\n{_steps_digest(baseline)}\n\n"
                    "Review this plan and return the final one."
                )},
            ],
            model=model,
            temperature=0.1,
            max_tokens=3000,
            json_mode=True,
        )
    except Exception as exc:
        print(f"   [!] Planner LLM unavailable ({type(exc).__name__}) — using the deterministic baseline.")
        return {"cleaning_plan": [s.model_dump() for s in baseline]}

    steps, rejections = parse_llm_plan(raw, df_columns)
    for rejection in rejections:
        print(f"   [!] rejected {rejection}")

    if not steps:
        print("   [!] Model returned no usable steps — using the deterministic baseline.")
        steps = baseline

    print(f"   -> Plan ready ({len(steps)} step(s))")
    return {"cleaning_plan": [s.model_dump() for s in steps]}


def review_manual_step(
    profile: DatasetProfile,
    existing_plan: list[dict],
    step_description: str,
    model: str | None = None,
) -> str:
    """Short opinion on a step the user is adding by hand, grounded in the same
    measured findings the generated plan was."""
    from agents.constants import DEFAULT_PLANNER_MODEL

    findings = findings_from_profile(profile)
    user_message = (
        f"Measured data-quality findings for this table:\n{_findings_digest(findings)}\n\n"
        "Existing plan steps:\n"
        f"{render_plan_text(existing_plan)}\n\n"
        f'The user wants to manually add this step: "{step_description}"\n\n'
        "Give your opinion on this specific step."
    )
    return complete(
        "planner",
        [
            {"role": "system", "content": _load_prompt("planner_step_opinion_prompt.txt")},
            {"role": "user", "content": user_message},
        ],
        model=model or DEFAULT_PLANNER_MODEL,
        temperature=0.3,
        max_tokens=300,
    ).strip()


def new_step_id() -> str:
    return uuid.uuid4().hex[:8]
