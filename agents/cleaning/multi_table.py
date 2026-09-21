"""Multi-table cleaning orchestration — Stage 5.

Loops the existing, proven single-table ``planner_graph``/``cleaning_graph``
once per table rather than rewriting the graph/sandbox machinery to accept a
``dict[str, DataFrame]`` directly. This satisfies the
``{"orders": df, ...} -> {"orders": cleaned_df, ...}`` contract at the
orchestration boundary while keeping ``executor.py``/``validator.py``/
``coder.py`` and the sandbox completely untouched.

Each table's approved-relationship key columns (Stage 4) are threaded into
its own profiler run so the planner treats join-key columns conservatively
(see ``models.profiler_models.ColumnProfile.is_relationship_key`` and the
"RELATIONSHIP KEY COLUMNS" section of ``prompts/planner_prompt.txt``).

Orchestration contract: a table failing validation after
``graphs.cleaning_graph.MAX_RETRIES`` does NOT halt the others — every table
is attempted independently and failures are reported per table.

``clean_tables_with_reconciliation`` (Stage 6) composes cleaning with the
cross-table reconciliation pass — see ``reconciliation/orphans.py`` — so
callers get tables that are ready for Stage 7 storage in one call.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from core.state import GraphState
from graphs.cleaning_graph import build_cleaning_graph
from graphs.planner_graph import build_planner_graph
from reconciliation.orphans import DEFAULT_ORPHAN_TOLERANCE, ReconciliationCheck, reconcile
from schema_discovery.models import RelationshipCandidate


def key_columns_for_table(
    table_name: str, approved_relationships: list[RelationshipCandidate] | None
) -> list[str]:
    """Columns of `table_name` that participate in an approved relationship."""
    cols: set[str] = set()
    for r in approved_relationships or []:
        if r.table_a == table_name:
            cols.add(r.column_a)
        if r.table_b == table_name:
            cols.add(r.column_b)
    return sorted(cols)


def _initial_state(
    raw_df: pd.DataFrame, name: str, planner_model: str, coder_model: str, key_columns: list[str]
) -> GraphState:
    return {
        "raw_df": raw_df,
        "file_path": name,
        "planner_model": planner_model,
        "coder_model": coder_model,
        "key_columns": key_columns,
        "metadata": {},
        "cleaning_plan": [],
        "generated_code": "",
        "execution_result": {},
        "validation_report": {},
        "transformation_log": [],
        "retry_count": 0,
        "last_error": "",
        "messages": [],
    }


@dataclass
class TableCleaningResult:
    table_name: str
    success: bool
    clean_df: pd.DataFrame | None
    cleaning_plan: list[dict] = field(default_factory=list)
    validation_report: dict = field(default_factory=dict)
    transformation_log: list[str] = field(default_factory=list)
    retry_count: int = 0
    error: str = ""
    ledger: dict = field(default_factory=dict)
    used_fallback: bool = False


def clean_table_deterministically(
    df: pd.DataFrame, name: str, key_columns: list[str]
) -> TableCleaningResult:
    """Clean one table with no LLM whatsoever: detect defects, derive the
    baseline plan, apply it, check the invariants.

    This is the floor the pipeline can always fall back to. Previously a table
    whose graph run failed ended with ``clean_df = None``, which excluded it
    from reconciliation and made the integrity gate reject the entire project —
    one rate-limited LLM call could block a save with no way forward. Now an
    LLM failure costs the LLM's judgement, not the cleaning.
    """
    from agents.cleaning.invariants import check_invariants
    from tools.arabic_text import normalize_arabic_dataframe
    from tools.cleaning_ops import apply_plan
    from tools.defect_detection import detect_defects, plan_from_defects

    normalized, _ = normalize_arabic_dataframe(df)
    keys = set(key_columns)
    findings = detect_defects(normalized, key_columns=keys)
    plan = plan_from_defects(findings)
    cleaned, ledger = apply_plan(normalized, plan, protected_columns=keys, table_name=name)
    report = check_invariants(normalized, cleaned, plan, ledger=ledger, key_columns=keys)

    return TableCleaningResult(
        table_name=name,
        success=report.passed,
        clean_df=cleaned if report.passed else None,
        cleaning_plan=[s.model_dump() for s in plan],
        validation_report={
            "passed": report.passed,
            "rows_before": ledger.rows_before,
            "rows_after": ledger.rows_after,
            "violations": [v.model_dump() for v in report.violations],
            "warnings": [w.model_dump() for w in report.warnings],
            "cells_changed": ledger.total_cells_changed,
        },
        transformation_log=ledger.as_log(),
        error="" if report.passed else "; ".join(v.detail for v in report.violations),
        ledger=ledger.model_dump(),
        used_fallback=True,
    )


def clean_tables(
    tables: dict[str, pd.DataFrame],
    approved_relationships: list[RelationshipCandidate] | None,
    *,
    planner_model: str,
    coder_model: str,
) -> dict[str, TableCleaningResult]:
    """Run the full planner+cleaning pipeline independently for every table.

    A failure (validation never passing after retries, or an unexpected
    exception) in one table is captured and does not stop the others.
    """
    planner_graph = build_planner_graph()
    cleaning_graph = build_cleaning_graph()
    results: dict[str, TableCleaningResult] = {}

    for name, df in tables.items():
        key_columns = key_columns_for_table(name, approved_relationships)
        try:
            plan_state = planner_graph.invoke(
                _initial_state(df, name, planner_model, coder_model, key_columns)
            )
            final_state = cleaning_graph.invoke(plan_state)

            exec_result = final_state.get("execution_result", {})
            validation_report = final_state.get("validation_report", {})
            passed = bool(validation_report.get("passed"))
            if not passed:
                # The graph produced something the invariants rejected. Fall
                # back to the deterministic clean rather than losing the table.
                fallback = clean_table_deterministically(df, name, key_columns)
                if fallback.success:
                    print(f"   [i] '{name}': graph result rejected — using the deterministic clean.")
                    results[name] = fallback
                    continue

            results[name] = TableCleaningResult(
                table_name=name,
                # The validator's verdict, not just "did the code run" — it
                # also catches unauthorized value/row/column changes.
                success=passed,
                clean_df=exec_result.get("clean_df"),
                cleaning_plan=final_state.get("cleaning_plan", []),
                validation_report=validation_report,
                transformation_log=final_state.get("transformation_log", []),
                retry_count=final_state.get("retry_count", 0),
                error=exec_result.get("error") or "",
                ledger=exec_result.get("ledger") or {},
            )
        except Exception as exc:
            print(f"   [!] '{name}': cleaning graph raised {type(exc).__name__} — "
                  "falling back to the deterministic clean.")
            try:
                results[name] = clean_table_deterministically(df, name, key_columns)
            except Exception as inner:
                results[name] = TableCleaningResult(
                    table_name=name, success=False, clean_df=None,
                    error=f"{exc}; deterministic fallback also failed: {inner}",
                )

    return results


def all_succeeded(results: dict[str, TableCleaningResult]) -> bool:
    return all(r.success for r in results.values())


def failed_tables(results: dict[str, TableCleaningResult]) -> list[str]:
    return [name for name, r in results.items() if not r.success]


def clean_tables_with_reconciliation(
    tables: dict[str, pd.DataFrame],
    approved_relationships: list[RelationshipCandidate] | None,
    *,
    planner_model: str,
    coder_model: str,
    orphan_tolerance: float = DEFAULT_ORPHAN_TOLERANCE,
) -> tuple[dict[str, TableCleaningResult], dict[str, pd.DataFrame], list[ReconciliationCheck]]:
    """Clean every table, then reconcile approved-relationship key columns
    across the successfully-cleaned results (Stage 6).

    Returns ``(per_table_results, reconciled_tables, reconciliation_checks)``.
    ``reconciled_tables`` only contains tables that cleaned successfully —
    it's what should move on to Stage 7 storage. Tables that failed cleaning
    are absent, so any relationship touching one is skipped in reconciliation
    (there's nothing coherent to reconcile against).
    """
    results = clean_tables(
        tables, approved_relationships, planner_model=planner_model, coder_model=coder_model,
    )
    cleaned = {name: r.clean_df for name, r in results.items() if r.clean_df is not None}
    reconciled, checks = reconcile(approved_relationships or [], tables, cleaned)
    return results, reconciled, checks
