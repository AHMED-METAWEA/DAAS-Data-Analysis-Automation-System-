"""Node 4 — Executor.

Runs the cleaning plan. Typed steps (the normal case) execute as deterministic
operators from ``tools.cleaning_ops.REGISTRY``, with every effect *measured* by
diffing the frame rather than reported by the code that made the change.

Free-text steps — a user's hand-written instruction, or something the planner
could not express as an operator — are the only path that still generates
Python, and they run *after* the operators, on the already-cleaned frame, in
the sandbox. Whatever that code does is then subject to the same invariant
gate in ``agents/cleaning/invariants.py``: it may only touch what the plan
named. The old arrangement had it the other way round — all cleaning was
generated code, and the gate was a null count.
"""

from __future__ import annotations

import io
import re
import traceback
from concurrent.futures import TimeoutError as _TimeoutError

import numpy as np
import pandas as pd

from core.state import GraphState
from models.cleaning_ops import CleaningLedger, CleaningStep
from tools.cleaning_ops import apply_plan
from tools.sandbox import SAFE_BUILTINS as _SANDBOX_BUILTINS
from tools.sandbox import _check_code_safety, run_with_timeout

SAFE_BUILTINS = dict(_SANDBOX_BUILTINS)


def parse_plan_steps(plan: list[dict] | None) -> list[CleaningStep]:
    """Coerce the stored plan (plain dicts, possibly from an older session or
    hand-edited in the UI) into typed steps. Anything unrecognised degrades to
    a free-text step rather than raising."""
    steps: list[CleaningStep] = []
    for index, raw in enumerate(plan or []):
        if isinstance(raw, CleaningStep):
            steps.append(raw)
            continue
        if not isinstance(raw, dict):
            steps.append(CleaningStep(id=str(index + 1), description=str(raw)))
            continue
        try:
            steps.append(CleaningStep(**{**raw, "id": str(raw.get("id") or index + 1)}))
        except Exception:
            steps.append(CleaningStep(
                id=str(raw.get("id") or index + 1),
                description=str(raw.get("description", "")),
                source=raw.get("source", "generated") if raw.get("source") in
                       ("generated", "manual", "detector", "fallback") else "generated",
            ))
    return steps


def _define_and_run(code: str, df: pd.DataFrame, safe_globals: dict) -> pd.DataFrame:
    exec(code, safe_globals)
    if "clean_data" not in safe_globals:
        raise RuntimeError(
            "The generated code did not define a 'clean_data' function. "
            "Expected: def clean_data(df): ..."
        )
    return safe_globals["clean_data"](df.copy())


def _run_generated_code(code: str, df: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    """Execute ``clean_data(df)`` in the sandbox. Returns ``(df, error)``."""
    safety_error = _check_code_safety(code)
    if safety_error:
        return df, safety_error

    captured = io.StringIO()

    def sandboxed_print(*args, **kwargs):
        kwargs["file"] = captured
        print(*args, **kwargs)

    safe_globals = {
        # np and re are provided deliberately: generated cleaning code reaches
        # for np.nan and re.sub constantly, and without them every such attempt
        # burned one of the two available repair attempts on a NameError before
        # the table was abandoned.
        "pd": pd,
        "np": np,
        "re": re,
        "__builtins__": {**SAFE_BUILTINS, "print": sandboxed_print},
    }

    try:
        result = run_with_timeout(_define_and_run, code, df, safe_globals)
    except _TimeoutError:
        return df, "Execution timed out (>30s). The generated code likely contains an unbounded loop."
    except Exception:
        return df, traceback.format_exc()

    if not isinstance(result, pd.DataFrame):
        return df, f"clean_data() returned {type(result).__name__} instead of a DataFrame"
    return result, ""


def executor_node(state: GraphState) -> dict:
    """Apply the cleaning plan: deterministic operators, then any generated
    code for free-text steps.

    Returns ``execution_result`` (with the cleaned frame and the measured
    ledger), the transformation log, and on failure the retry bookkeeping.
    """
    raw_df = state["raw_df"]
    steps = parse_plan_steps(state.get("cleaning_plan"))
    key_columns = set(state.get("key_columns") or [])
    retry_count = state.get("retry_count", 0)

    typed = [s for s in steps if s.is_typed]
    untyped = [s for s in steps if not s.is_typed]

    print(f"\n-- Executor: {len(typed)} operator step(s), {len(untyped)} free-text step(s)")

    working, ledger = apply_plan(
        raw_df, typed, protected_columns=key_columns, table_name=state.get("file_path", ""),
    )
    for line in ledger.as_log():
        print(f"   {line}")

    error = ""
    code = state.get("generated_code") or ""
    if untyped and code.strip():
        working, error = _run_generated_code(code, working)
        if error:
            print(f"   [X] Generated code failed (attempt {retry_count + 1})")
            print(f"   {error.strip().splitlines()[-1]}")
        else:
            print(f"   [OK] Generated code applied for {len(untyped)} free-text step(s)")

    failed_steps = [s for s in ledger.steps if s.error]
    if failed_steps and not error:
        error = "; ".join(f"{s.op}: {s.error}" for s in failed_steps)

    result = {
        "execution_result": {
            "success": not error,
            "clean_df": working if not error else None,
            "error": error,
            "ledger": ledger.model_dump(),
        },
        "transformation_log": ledger.as_log(),
    }
    if error:
        result["retry_count"] = retry_count + 1
        result["last_error"] = error
    return result


def ledger_from_state(state: GraphState) -> CleaningLedger | None:
    """Rebuild the measured ledger from graph state, for the validator."""
    raw = (state.get("execution_result") or {}).get("ledger")
    if not raw:
        return None
    try:
        return CleaningLedger(**raw)
    except Exception:
        return None
