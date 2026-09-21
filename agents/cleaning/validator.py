"""Node 5 — Validator.

Produces the report the UI and the API consume, and — the part that matters —
decides whether the cleaning run is allowed to stand. The verdict comes from
``agents.cleaning.invariants.check_invariants``: every difference between the
input and the output must be attributable to something the plan authorized.

The descriptive numbers below (rows, nulls, duplicates) are kept because the UI
shows them, but they are no longer the verdict. They were the verdict once, and
a run that deleted 45% of the rows or multiplied a revenue column by 100
satisfied all of them.
"""

from __future__ import annotations

import pandas as pd

from agents.cleaning.executor import ledger_from_state, parse_plan_steps
from agents.cleaning.invariants import check_invariants
from core.state import GraphState


def _empty_report(raw_df: pd.DataFrame, error: str) -> dict:
    return {
        "passed": False,
        "reason": "Cleaning did not produce a usable table",
        "error": error,
        "rows_before": len(raw_df),
        "rows_after": 0,
        "rows_removed": len(raw_df),
        "nulls_before": int(raw_df.isna().sum().sum()),
        "nulls_after": None,
        "duplicates_before": int(raw_df.duplicated().sum()),
        "duplicates_after": None,
        "column_details": {},
        "violations": [],
        "warnings": [],
        "regression_warnings": [],
        "dropped_columns": [],
        "new_columns": [],
        "transformation_log": [],
    }


def validator_node(state: GraphState) -> dict:
    """Validate the cleaned DataFrame against what the plan authorized."""
    print("\n-- Validator: checking invariants ...")

    raw_df = state["raw_df"]
    execution_result = state.get("execution_result") or {}
    clean_df = execution_result.get("clean_df")

    if not execution_result.get("success", False) or clean_df is None:
        print("   [X] No clean DataFrame available — execution failed")
        return {
            "validation_report": _empty_report(
                raw_df, execution_result.get("error", "Unknown error")
            )
        }

    plan = parse_plan_steps(state.get("cleaning_plan"))
    ledger = ledger_from_state(state)
    key_columns = set(state.get("key_columns") or [])

    invariants = check_invariants(
        raw_df, clean_df, plan, ledger=ledger, key_columns=key_columns,
    )

    raw = raw_df.reset_index(drop=True)
    column_details = {}
    for col in sorted(set(raw.columns) & set(clean_df.columns), key=str):
        before = int(raw[col].isna().sum())
        after = int(clean_df[col].isna().sum())
        column_details[str(col)] = {
            "nulls_before": before,
            "nulls_after": after,
            "nulls_reduced": before - after,
        }

    dropped = sorted(str(c) for c in raw.columns if c not in clean_df.columns)
    added = sorted(str(c) for c in clean_df.columns if c not in raw.columns)

    report = {
        "passed": invariants.passed,
        "rows_before": len(raw),
        "rows_after": len(clean_df),
        "rows_removed": len(raw) - len(clean_df),
        "nulls_before": int(raw.isna().sum().sum()),
        "nulls_after": int(clean_df.isna().sum().sum()),
        "nulls_reduced": int(raw.isna().sum().sum()) - int(clean_df.isna().sum().sum()),
        "duplicates_before": int(raw.duplicated().sum()),
        "duplicates_after": int(clean_df.duplicated().sum()),
        "column_details": column_details,
        "dropped_columns": dropped,
        "new_columns": added,
        "violations": [v.model_dump() for v in invariants.violations],
        "warnings": [w.model_dump() for w in invariants.warnings],
        # Kept for backwards compatibility with existing UI code that reads it.
        "regression_warnings": [v.detail for v in invariants.violations],
        "authorized_columns": invariants.authorized_columns,
        "cells_changed": ledger.total_cells_changed if ledger else None,
        "transformation_log": state.get("transformation_log", []),
        "repair_feedback": invariants.repair_feedback(),
    }

    for v in invariants.violations:
        print(f"   [X] {v.rule}: {v.detail}")
    for w in invariants.warnings:
        print(f"   [!] {w.rule}: {w.detail}")

    print(f"\n   Validation {'PASSED' if invariants.passed else 'FAILED'}")
    print(f"   Rows: {report['rows_before']} -> {report['rows_after']}")
    print(f"   Nulls: {report['nulls_before']} -> {report['nulls_after']}")
    print(f"   Cells changed: {report['cells_changed']}")

    return {"validation_report": report}
