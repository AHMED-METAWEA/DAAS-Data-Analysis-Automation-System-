"""
Shared LangGraph state definition for the cleaning pipeline.

Every node in the graph receives and returns a subset of this TypedDict.
This is the single source of truth for inter-node communication.
"""

from typing import Any, TypedDict

import pandas as pd


class GraphState(TypedDict):
    """Typed state shared across all nodes in the cleaning pipeline."""

    # ── Input ──────────────────────────────────────────────────────────
    raw_df: pd.DataFrame                # Original DataFrame loaded from CSV
    file_path: str                      # Path to the source CSV file
    planner_model: str                  # Groq model for the planner agent
    coder_model: str                    # Groq model for the code-generation agent
    key_columns: list[str]              # Columns in an approved cross-table relationship (Stage 4/5)

    # ── Profiler output ────────────────────────────────────────────────
    metadata: dict                      # DatasetProfile.model_dump() output

    # ── Planner output ─────────────────────────────────────────────────
    cleaning_plan: list[dict]           # [{"id", "description", "source"}, ...] — empty means "already clean"

    # ── Coder output ───────────────────────────────────────────────────
    generated_code: str                 # Python code string for clean_data(df)

    # ── Executor output ────────────────────────────────────────────────
    execution_result: dict              # {"success": bool, "clean_df": df | None, "error": str}

    # ── Validator output ───────────────────────────────────────────────
    validation_report: dict             # Comparison report with passed flag

    # ── Pipeline metadata ──────────────────────────────────────────────
    transformation_log: list[str]       # print() outputs captured from generated code
    retry_count: int                    # Number of coder→executor retry cycles
    last_error: str                     # Last execution traceback for repair prompt

    # ── LangGraph messages ─────────────────────────────────────────────
    messages: list[Any]                 # LangGraph message history for LLM nodes
