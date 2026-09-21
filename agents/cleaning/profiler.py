"""Node 1 — Data Profiler (no LLM).

Builds the ``DatasetProfile`` every downstream node reads, and runs the
deterministic defect detectors in ``tools.defect_detection``.

The profiler no longer *modifies* the data. It used to replace placeholder
strings with NaN and widen every integer column to float64 before anything had
approved either change — so the pipeline's first act was an unrecorded,
unauthorized mutation of the user's data, and `order_id` 1001 became 1001.0 in
every dataset whether or not it needed cleaning. Both are now explicit
operators (``standardize_null_placeholders``, ``cast_integer``) that appear in
the plan, run through the ledger, and are checked by the validator.

The one transformation still applied here is Arabic normalization, which is
meaning-preserving (diacritics, tatweel, alef variants, Arabic-Indic digits)
and has to happen before detection so that placeholder matching and category
grouping see canonical text.
"""

from __future__ import annotations

from core.state import GraphState
from tools.arabic_text import normalize_arabic_dataframe
from tools.defect_detection import detect_defects, is_clean
from tools.profiler_tools import build_dataset_profile


def profiler_node(state: GraphState) -> dict:
    """Normalize Arabic text, profile the DataFrame, and detect defects."""
    raw_df, arabic_cols = normalize_arabic_dataframe(state["raw_df"])
    if arabic_cols:
        cols = ", ".join(f"{c} ({r:.0%})" for c, r in arabic_cols.items())
        print(f"-- Profiler: Arabic text detected and normalized in: {cols}")

    key_columns = set(state.get("key_columns", []) or [])

    print("-- Profiler: analysing dataset ...")
    profile = build_dataset_profile(raw_df, key_columns=key_columns)

    findings = detect_defects(raw_df, key_columns=key_columns)
    profile.defects = [f.model_dump() for f in findings]

    print(f"   -> {profile.total_rows} rows, {profile.total_cols} cols, "
          f"{len(findings)} data-quality finding(s)")
    for finding in findings:
        where = f"{finding.column}: " if finding.column else ""
        print(f"   [{finding.severity}] {where}{finding.detail}")
    if is_clean(findings):
        print("   -> no defects found; this table needs no cleaning")

    return {"raw_df": raw_df, "metadata": profile.model_dump()}
