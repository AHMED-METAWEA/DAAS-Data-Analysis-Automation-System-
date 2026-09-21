"""Node 3 — Code Generator (fallback path only).

Called only when the plan contains free-text steps that no operator covers —
in practice, an instruction the user typed by hand. Everything else is executed
deterministically by ``tools.cleaning_ops``, so most runs never call an LLM
here at all.

On a retry it receives the reason the previous attempt was rejected. That
reason is now either a traceback *or* a list of invariant violations naming the
exact columns and rows the code touched without authorization, which makes the
repair targeted instead of a blind rewrite.
"""

from __future__ import annotations

from pathlib import Path

from agents.cleaning.executor import parse_plan_steps
from core.state import GraphState
from models.cleaning_ops import CleaningStep
from tools.llm_client import complete
from tools.sandbox import strip_fences as _strip_markdown_fences

_PROMPT_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"


def _load_prompt(filename: str) -> str:
    return (_PROMPT_DIR / filename).read_text(encoding="utf-8")


def _render_steps(steps: list[CleaningStep]) -> str:
    return "\n".join(f"{i + 1}. {s.description}" for i, s in enumerate(steps))


def coder_node(state: GraphState) -> dict:
    """Generate (or repair) ``clean_data(df)`` for the plan's free-text steps."""
    retry_count = state.get("retry_count", 0)
    steps = parse_plan_steps(state.get("cleaning_plan"))
    untyped = [s for s in steps if not s.is_typed]

    if not untyped:
        # Every step is a typed operator — there is nothing for generated code
        # to do, and asking for some would only invite an unrequested change.
        if retry_count == 0:
            print("\n-- Coder: every step is a deterministic operator — no code needed.")
        return {"generated_code": ""}

    columns = [c.name for c in _profile_columns(state)]
    typed = [s for s in steps if s.is_typed]

    if retry_count == 0:
        print(f"\n-- Coder: generating code for {len(untyped)} free-text step(s) ...")
        system_prompt = _load_prompt("coder_prompt.txt")
        user_message = (
            "## Free-text steps you must implement\n\n"
            f"{_render_steps(untyped)}\n\n"
            "## Columns you are allowed to modify\n\n"
            f"{sorted({c for s in untyped for c in columns if c.lower() in s.description.lower()}) or columns}\n\n"
            "## Columns present in the DataFrame\n\n"
            f"{columns}\n\n"
            "## Already applied by deterministic operators (do NOT redo these)\n\n"
            f"{_render_steps(typed) or 'nothing'}"
        )
    else:
        print(f"\n-- Coder: repairing code (attempt {retry_count + 1}) ...")
        system_prompt = _load_prompt("repair_prompt.txt")
        user_message = (
            "## Previous Code (REJECTED)\n\n"
            f"```python\n{state.get('generated_code', '')}\n```\n\n"
            "## Why it was rejected\n\n"
            f"```\n{state.get('last_error', '')}\n```\n\n"
            "## Free-text steps you must implement\n\n"
            f"{_render_steps(untyped)}\n\n"
            "## Columns present in the DataFrame\n\n"
            f"{columns}"
        )

    from agents.constants import DEFAULT_CODER_MODEL
    model = state.get("coder_model", DEFAULT_CODER_MODEL)

    try:
        raw_code = complete(
            "coder",
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            model=model,
            temperature=0.1,
            max_tokens=4096,
        )
    except Exception as exc:
        print(f"   [!] Coder LLM unavailable ({type(exc).__name__}) — free-text steps will be skipped.")
        return {"generated_code": ""}

    code = _strip_markdown_fences(raw_code)
    print(f"   -> Generated {code.count(chr(10)) + 1} lines of code")
    return {"generated_code": code}


def _profile_columns(state: GraphState):
    from models.profiler_models import DatasetProfile

    try:
        return DatasetProfile(**state["metadata"]).columns
    except Exception:
        return []
