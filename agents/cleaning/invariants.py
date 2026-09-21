"""Hard invariants for a cleaning run — the gate that makes the output trustworthy.

The previous validator asserted three things: at least one row survived, the
total null count did not rise, and no column's null count rose. Measured
against deliberately corrupting cleaning code, all of the following passed it:

  * every value in a revenue column multiplied by 100
  * every value in a revenue column overwritten with the column mean
  * the revenue column deleted outright
  * 45% of rows deleted
  * a join key case-folded and rewritten
  * every row duplicated
  * a column's values shuffled against the wrong rows

None of those are visible to a null-count check, because none of them changes
a null count. What they have in common is that they changed data *the plan
never authorized changing* — and that is what this module checks instead.

The model is authorization, not resemblance:

  1. The plan declares which columns each step targets.
  2. Operators declare statically (``OperatorSpec.may_*``) what class of effect
     they are permitted to cause: remove rows, add/drop columns, introduce
     nulls, change dtype.
  3. Every observed difference between the input and output frames must be
     attributable to some step's authorization. Anything else is a violation
     and fails the run.

This works for the LLM fallback path too. Freeform generated code carries no
per-step ledger, so its authorization is derived from the same plan — the code
may only touch columns the plan named. Code that quietly rescales an
unmentioned column fails here exactly as an operator would.
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field

from models.cleaning_ops import CleaningLedger, CleaningStep
from tools.cleaning_ops import KEY_SAFE_OPS, REGISTRY, _changed_mask

_SAMPLE = 5


class Violation(BaseModel):
    """A broken invariant. Any violation fails the run."""

    rule: str
    column: str = ""
    detail: str
    evidence: list = Field(default_factory=list)


class Warning_(BaseModel):
    """Something worth telling the user that is not, by itself, disqualifying."""

    rule: str
    column: str = ""
    detail: str


class InvariantReport(BaseModel):
    passed: bool
    violations: list[Violation] = Field(default_factory=list)
    warnings: list[Warning_] = Field(default_factory=list)
    authorized_columns: list[str] = Field(default_factory=list)

    def repair_feedback(self) -> str:
        """Feedback for the coder's repair attempt — states what rule broke and
        on which column, so the retry is targeted rather than a blind rewrite."""
        if not self.violations:
            return ""
        lines = ["The cleaning output was rejected because it changed data the plan did not authorize:"]
        for v in self.violations:
            where = f" (column '{v.column}')" if v.column else ""
            lines.append(f"- [{v.rule}]{where} {v.detail}")
        lines.append(
            "Only modify the columns named in the cleaning plan. Do not rescale, reorder, "
            "re-derive or delete any other column, and do not drop rows except for the exact "
            "duplicates the plan asks you to drop."
        )
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
# Authorization derived from the plan
# ══════════════════════════════════════════════════════════════════════


def _column_mentioned(description: str, column: str) -> bool:
    """Whether a free-text plan step names a column. Used only for the LLM
    fallback path, where there are no typed target columns."""
    return bool(re.search(rf"(?<![\w]){re.escape(column)}(?![\w])", description, re.IGNORECASE))


class Authorization(BaseModel):
    """What the plan, as written, permits this run to do."""

    columns: set[str] = Field(default_factory=set)
    may_remove_rows: bool = False
    may_drop_columns: set[str] = Field(default_factory=set)
    may_add_columns: bool = False
    null_introducing_columns: set[str] = Field(default_factory=set)
    dtype_changing_columns: set[str] = Field(default_factory=set)
    key_safe_columns: set[str] = Field(default_factory=set)
    # Cell-level scope. Naming a column in the plan is not blanket permission to
    # do anything to it: a step that fills blanks may fill blanks, and a step
    # that adds an outlier flag may not touch a value at all.
    rewrite_columns: set[str] = Field(default_factory=set)
    fill_columns: set[str] = Field(default_factory=set)
    # Column subsets the plan is allowed to de-duplicate on. An empty tuple
    # means whole-row duplicates. Used to verify that rows which disappeared
    # really were duplicates of rows that survived.
    dedupe_subsets: list[tuple[str, ...]] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}


def authorization_from_plan(
    plan: list[CleaningStep], df_columns: list[str]
) -> Authorization:
    """Derive the permitted effects from the plan alone.

    Typed steps contribute their declared target columns and their operator's
    ``may_*`` flags. Untyped (free-text) steps — the LLM fallback — contribute
    every column their description names, and are treated as permissive on
    dtype and nulls for those columns only, since a free-text step cannot say
    more precisely what it intends.
    """
    auth = Authorization()
    for step in plan:
        entry = REGISTRY.get(step.op) if step.op else None
        if entry is not None:
            spec, _ = entry
            targets = set(step.columns)
            # Cross-column operators name their operands in params, not columns.
            for key in ("left", "right", "result"):
                value = step.params.get(key)
                if isinstance(value, str) and value in df_columns:
                    targets.add(value)
            auth.columns |= targets
            auth.may_remove_rows |= spec.may_remove_rows
            auth.may_add_columns |= spec.may_add_columns
            if spec.may_remove_rows:
                auth.dedupe_subsets.append(tuple(sorted(targets)))
            if spec.may_rewrite_values:
                auth.rewrite_columns |= targets
            if spec.may_fill_nulls:
                auth.fill_columns |= targets
            if spec.may_drop_columns:
                auth.may_drop_columns |= targets
            if spec.may_introduce_nulls:
                auth.null_introducing_columns |= targets
            if spec.may_change_dtype:
                auth.dtype_changing_columns |= targets
            if step.op in KEY_SAFE_OPS:
                auth.key_safe_columns |= targets
        else:
            named = {c for c in df_columns if _column_mentioned(step.description, c)}
            auth.columns |= named
            auth.null_introducing_columns |= named
            auth.dtype_changing_columns |= named
            auth.rewrite_columns |= named
            auth.fill_columns |= named
            auth.may_add_columns = True
            if re.search(r"duplicat", step.description, re.IGNORECASE):
                auth.may_remove_rows = True
                auth.dedupe_subsets.append(tuple(sorted(named)))
            if re.search(r"\b(drop|remove|delete)\b.*\bcolumn", step.description, re.IGNORECASE):
                auth.may_drop_columns |= named
    return auth


# ══════════════════════════════════════════════════════════════════════
# The invariants
# ══════════════════════════════════════════════════════════════════════


def _rows_removed_that_were_not_duplicates(
    raw: pd.DataFrame, clean_df: pd.DataFrame, auth: Authorization
) -> list:
    """Row deletion is only ever authorized for duplicates, so every deleted
    row must match a surviving row on one of the plan's dedupe subsets.

    Without this, "remove duplicate rows" in the plan is a blanket licence to
    delete anything: a run that dropped 45% of the rows satisfied a row-count
    check and a null check simultaneously, because deleting rows lowers both.
    """
    removed_idx = raw.index.difference(clean_df.index)
    if not len(removed_idx):
        return []

    shared = [c for c in raw.columns if c in clean_df.columns]
    surviving = raw.index.intersection(clean_df.index)
    if not shared or not len(surviving):
        return list(removed_idx)

    unjustified = set(removed_idx)
    for subset in auth.dedupe_subsets or [()]:
        cols = [c for c in subset if c in shared] or shared
        as_text = raw[cols].astype(str)
        kept = set(map(tuple, as_text.loc[surviving].to_numpy()))
        still_bad = {
            idx for idx in unjustified
            if tuple(as_text.loc[idx].to_numpy()) not in kept
        }
        unjustified &= still_bad
        if not unjustified:
            break
    return sorted(unjustified)


def _sample_values(series: pd.Series, limit: int = _SAMPLE) -> list:
    return [
        v.item() if hasattr(v, "item") else (v if isinstance(v, (int, float, bool, str)) else str(v))
        for v in series.head(limit)
    ]


def check_invariants(
    raw_df: pd.DataFrame,
    clean_df: pd.DataFrame,
    plan: list[CleaningStep],
    *,
    ledger: CleaningLedger | None = None,
    key_columns: set[str] | None = None,
) -> InvariantReport:
    """Verify a cleaning result against what its plan authorized."""
    keys = {k for k in (key_columns or set()) if k in raw_df.columns}
    auth = authorization_from_plan(plan, [str(c) for c in raw_df.columns])
    violations: list[Violation] = []
    warnings: list[Warning_] = []

    raw = raw_df.reset_index(drop=True)

    # ── 1. Row identity ────────────────────────────────────────────────
    if clean_df.index.has_duplicates:
        violations.append(Violation(
            rule="row_identity",
            detail="the cleaned table has duplicate row labels, so rows can no longer be "
                   "matched back to the source",
        ))
    unknown_rows = clean_df.index.difference(raw.index)
    if len(unknown_rows):
        violations.append(Violation(
            rule="rows_invented",
            detail=f"{len(unknown_rows)} row(s) in the output do not correspond to any input row",
            evidence=[str(i) for i in unknown_rows[:_SAMPLE]],
        ))

    # ── 2. Row conservation ────────────────────────────────────────────
    removed = len(raw) - len(clean_df)
    if len(clean_df) > len(raw):
        violations.append(Violation(
            rule="rows_added",
            detail=f"the table grew from {len(raw)} to {len(clean_df)} rows; cleaning "
                   "must never create rows",
        ))
    elif removed > 0 and not auth.may_remove_rows:
        violations.append(Violation(
            rule="unauthorized_row_removal",
            detail=f"{removed} row(s) were deleted but no step in the plan removes rows",
        ))
    elif removed > 0:
        if ledger is not None:
            declared = sum(s.rows_removed for s in ledger.applied_steps)
            if removed != declared:
                violations.append(Violation(
                    rule="row_removal_mismatch",
                    detail=f"{removed} row(s) disappeared but the plan's steps account for only {declared}",
                ))
        unjustified = _rows_removed_that_were_not_duplicates(raw, clean_df, auth)
        if unjustified:
            violations.append(Violation(
                rule="rows_removed_that_were_not_duplicates",
                detail=(
                    f"{len(unjustified)} deleted row(s) are not duplicates of any surviving row. "
                    "The plan only authorizes removing duplicates; every other row must be kept, "
                    "flagged or imputed, never dropped"
                ),
                evidence=[str(i) for i in unjustified[:_SAMPLE]],
            ))

    if len(raw) and removed > 0:
        share = removed / len(raw)
        if share > 0.2:
            warnings.append(Warning_(
                rule="large_row_loss",
                detail=f"{share:.0%} of rows were removed — verify this is really all duplicates",
            ))

    # ── 3. Column conservation ─────────────────────────────────────────
    dropped = [str(c) for c in raw.columns if c not in clean_df.columns]
    unauthorized_drops = [c for c in dropped if c not in auth.may_drop_columns]
    if unauthorized_drops:
        violations.append(Violation(
            rule="unauthorized_column_drop",
            detail=f"column(s) {unauthorized_drops} were deleted but no step in the plan drops them",
            evidence=unauthorized_drops,
        ))

    added = [str(c) for c in clean_df.columns if c not in raw.columns]
    if added and not auth.may_add_columns:
        violations.append(Violation(
            rule="unauthorized_column_add",
            detail=f"column(s) {added} appeared but no step in the plan adds columns",
            evidence=added,
        ))

    # ── 4. Value authorization — the core check ───────────────────────
    surviving = clean_df.index.intersection(raw.index)
    shared = [c for c in raw.columns if c in clean_df.columns]
    if len(surviving) and shared:
        before = raw.loc[surviving, shared]
        after = clean_df.loc[surviving, shared]
        changed = _changed_mask(before, after)

        before_null_all, after_null_all = before.isna(), after.isna()
        for col in shared:
            name = str(col)
            n_changed = int(changed[col].sum())
            if not n_changed:
                continue

            # Split the changes by kind, because the plan authorizes kinds, not
            # just columns: a step that fills blanks may not also rescale the
            # values that were already there.
            rewrote = changed[col] & ~before_null_all[col] & ~after_null_all[col]
            filled = changed[col] & before_null_all[col] & ~after_null_all[col]

            if int(rewrote.sum()) and name not in auth.rewrite_columns:
                rows = rewrote.index[rewrote]
                reason = (
                    "no step in the plan targets this column"
                    if name not in auth.columns
                    else "the plan's step(s) for this column may only add a flag, remove rows, "
                         "or fill blanks — not change values that were already present"
                )
                violations.append(Violation(
                    rule="unauthorized_value_change",
                    column=name,
                    detail=f"{int(rewrote.sum())} existing value(s) in '{name}' were rewritten — {reason}",
                    evidence=[
                        {"row": str(i), "before": _jsonable(before.at[i, col]),
                         "after": _jsonable(after.at[i, col])}
                        for i in rows[:_SAMPLE]
                    ],
                ))

            if int(filled.sum()) and name not in auth.fill_columns:
                rows = filled.index[filled]
                violations.append(Violation(
                    rule="unauthorized_null_fill",
                    column=name,
                    detail=(
                        f"{int(filled.sum())} blank value(s) in '{name}' were filled in, but no "
                        "step in the plan fills this column. Inventing values to make a column "
                        "look complete is never an acceptable substitute for reporting them"
                    ),
                    evidence=[
                        {"row": str(i), "after": _jsonable(after.at[i, col])} for i in rows[:_SAMPLE]
                    ],
                ))

            if name in keys and name not in auth.key_safe_columns:
                rows = changed.index[changed[col]]
                violations.append(Violation(
                    rule="join_key_modified",
                    column=name,
                    detail=(
                        f"{n_changed} value(s) changed in join key '{name}'. Rows in the related "
                        "table are matched against these values, so rewriting them orphans those "
                        "rows and the save will be rejected by the database"
                    ),
                    evidence=[
                        {"row": str(i), "before": _jsonable(before.at[i, col]),
                         "after": _jsonable(after.at[i, col])}
                        for i in rows[:_SAMPLE]
                    ],
                ))

        # ── 5. Null introduction ──────────────────────────────────────
        before_null, after_null = before_null_all, after_null_all
        for col in shared:
            name = str(col)
            introduced = int((~before_null[col] & after_null[col]).to_numpy().sum())
            if introduced and name not in auth.null_introducing_columns:
                violations.append(Violation(
                    rule="unauthorized_null_introduction",
                    column=name,
                    detail=(
                        f"{introduced} value(s) in '{name}' were blanked out. Only parsing and "
                        "placeholder steps may turn a value into NULL"
                    ),
                    evidence=_sample_values(before.loc[~before_null[col] & after_null[col], col]),
                ))
            elif introduced:
                pct = introduced / max(int((~before_null[col]).sum()), 1) * 100
                if pct >= 20:
                    warnings.append(Warning_(
                        rule="high_parse_failure",
                        column=name,
                        detail=(
                            f"{introduced} value(s) ({pct:.0f}%) in '{name}' could not be parsed "
                            "and became NULL — check the source format before relying on this column"
                        ),
                    ))

        # ── 6. Dtype changes ──────────────────────────────────────────
        for col in shared:
            name = str(col)
            if str(before[col].dtype) == str(after[col].dtype):
                continue
            if name not in auth.dtype_changing_columns:
                violations.append(Violation(
                    rule="unauthorized_dtype_change",
                    column=name,
                    detail=(
                        f"'{name}' changed type from {before[col].dtype} to {after[col].dtype} "
                        "without a plan step that converts it"
                    ),
                ))

    # ── 7. Key integrity ──────────────────────────────────────────────
    for key in sorted(keys):
        if key not in clean_df.columns:
            continue  # already reported as an unauthorized drop
        before_dupes = int(raw[key].dropna().duplicated().sum())
        after_dupes = int(clean_df[key].dropna().duplicated().sum())
        if after_dupes > before_dupes:
            violations.append(Violation(
                rule="key_uniqueness_regression",
                column=key,
                detail=(
                    f"duplicate values in join key '{key}' rose from {before_dupes} to "
                    f"{after_dupes}, which breaks the relationship it participates in"
                ),
            ))
        before_nulls = int(raw[key].isna().sum())
        after_nulls = int(clean_df.loc[clean_df.index.intersection(raw.index), key].isna().sum())
        if after_nulls > before_nulls:
            violations.append(Violation(
                rule="key_nulls_introduced",
                column=key,
                detail=f"join key '{key}' gained {after_nulls - before_nulls} NULL value(s)",
            ))

    # ── 8. Output is usable at all ────────────────────────────────────
    if len(clean_df) == 0 and len(raw) > 0:
        violations.append(Violation(
            rule="empty_output", detail="the cleaned table has no rows left",
        ))
    if len(clean_df.columns) == 0:
        violations.append(Violation(
            rule="no_columns", detail="the cleaned table has no columns left",
        ))

    return InvariantReport(
        passed=not violations,
        violations=violations,
        warnings=warnings,
        authorized_columns=sorted(auth.columns),
    )


def _jsonable(value: Any) -> Any:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    if hasattr(value, "item"):
        try:
            return value.item()
        except (ValueError, AttributeError):
            pass
    return value if isinstance(value, (int, float, bool, str)) else str(value)
