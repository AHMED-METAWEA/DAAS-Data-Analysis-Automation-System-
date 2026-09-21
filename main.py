"""
DAAS — Data Cleaning Pipeline (Phase 1, Stage 1)

Entry point that loads raw CSV data, runs it through the LangGraph
cleaning pipeline, and prints the results.

Usage:
    python main.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from agents.constants import DEFAULT_CODER_MODEL, DEFAULT_PLANNER_MODEL
from graphs.cleaning_graph import build_cleaning_graph
from graphs.planner_graph import build_planner_graph


def main() -> None:
    load_dotenv()

    if not os.environ.get("GROQ_API_KEY") or os.environ["GROQ_API_KEY"] == "your_groq_api_key_here":
        print("ERROR: Please set a valid GROQ_API_KEY in .env (see .env.example)")
        sys.exit(1)

    data_path = Path(__file__).resolve().parent / "data" / "sample.csv"
    if not data_path.exists():
        print(f"ERROR: Sample data not found at {data_path}")
        print("Run: python Generate_Data/generate_sample_data.py")
        sys.exit(1)

    print("=" * 60)
    print("  DAAS - Data Cleaning Pipeline")
    print("=" * 60)

    raw_df = pd.read_csv(str(data_path))
    print(f"\nLoaded {len(raw_df)} rows x {len(raw_df.columns)} cols from {data_path.name}")

    # ── Stage 1: Planning ──────────────────────────────────────────────
    print("\n--- STAGE 1: Planning ---\n")

    planner_graph = build_planner_graph()
    plan_state = planner_graph.invoke({
        "raw_df": raw_df,
        "file_path": str(data_path),
        "planner_model": DEFAULT_PLANNER_MODEL,
        "coder_model": DEFAULT_CODER_MODEL,
        "key_columns": [],
        "metadata": {},
        "cleaning_plan": [],
        "generated_code": "",
        "execution_result": {},
        "validation_report": {},
        "transformation_log": [],
        "retry_count": 0,
        "last_error": "",
        "messages": [],
    })

    plan: list[dict] = plan_state.get("cleaning_plan", []) or []
    metadata = plan_state.get("metadata", {})
    raw_df = plan_state.get("raw_df", raw_df)  # Arabic-normalised version

    # ── Human review ───────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  HUMAN REVIEW — Cleaning Plan Generated")
    print("=" * 60)

    if not plan:
        print("\nNo cleaning steps — the profiler found no data-quality defects.")
    else:
        for index, step in enumerate(plan, start=1):
            operator = step.get("op") or "custom"
            columns = ", ".join(step.get("columns") or []) or "whole table"
            print(f"\n  {index}. {step.get('description', '')}")
            print(f"     [{operator}] on {columns}")

    print("\n" + "-" * 60)
    print("Options:")
    print("  [enter]  Approve plan as-is and continue")
    print("  d N,M    Drop step numbers N and M")
    print("  q        Quit")
    choice = input("\nChoice: ").strip().lower()

    if choice == "q":
        print("\nPipeline cancelled by user.")
        sys.exit(0)

    edited_plan = plan
    if choice.startswith("d"):
        # Steps are typed operators with validated parameters, so the review
        # affordance is choosing which of them to run — not retyping the plan
        # as prose, which the pipeline no longer executes as prose anyway.
        try:
            drop = {int(part) for part in choice[1:].replace(",", " ").split()}
        except ValueError:
            drop = set()
        edited_plan = [s for i, s in enumerate(plan, start=1) if i not in drop]
        print(f"\nDropped {len(plan) - len(edited_plan)} step(s); {len(edited_plan)} remain.")

    # ── Stage 2: Cleaning ──────────────────────────────────────────────
    print("\n--- STAGE 2: Cleaning ---\n")

    cleaning_graph = build_cleaning_graph()
    final_state = cleaning_graph.invoke({
        "raw_df": raw_df,
        "file_path": str(data_path),
        "planner_model": DEFAULT_PLANNER_MODEL,
        "coder_model": DEFAULT_CODER_MODEL,
        "key_columns": [],
        "metadata": metadata,
        "cleaning_plan": edited_plan,
        "generated_code": "",
        "execution_result": {},
        "validation_report": {},
        "transformation_log": [],
        "retry_count": 0,
        "last_error": "",
        "messages": [],
    })

    # ── Print results ──────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  PIPELINE RESULTS")
    print("=" * 60)

    print("\n--- CLEANING PLAN ---")
    for index, step in enumerate(final_state.get("cleaning_plan") or [], start=1):
        print(f"  {index}. [{step.get('op') or 'custom'}] {step.get('description', '')}")

    print("\n--- TRANSFORMATION LOG (measured) ---")
    log = final_state.get("transformation_log", [])
    if log:
        for i, entry in enumerate(log, 1):
            print(f"  {i}. {entry}")
    else:
        print("  No transformations logged.")

    print("\n--- VALIDATION REPORT ---")
    report = final_state.get("validation_report", {})
    if report:
        printable = {k: v for k, v in report.items() if k != "clean_df"}
        print(json.dumps(printable, indent=2, default=str))
    else:
        print("  No validation report available.")

    print("\n" + "=" * 60)
    passed = report.get("passed", False)
    status = "PASSED" if passed else "FAILED"
    print(f"  Pipeline complete - Validation: {status}")
    print(f"  Retries used: {final_state.get('retry_count', 0)}")
    print("=" * 60)

    clean_df = final_state.get("execution_result", {}).get("clean_df")
    if clean_df is not None:
        output_dir = Path(__file__).resolve().parent / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "cleaned_data.csv"
        clean_df.to_csv(output_path, index=False)
        print(f"\n[INFO] Saved cleaned data ({len(clean_df)} rows) to: {output_path}")


if __name__ == "__main__":
    main()
