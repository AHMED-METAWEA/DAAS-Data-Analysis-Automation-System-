"""Pydantic contracts for the deterministic cleaning-operator pipeline.

The cleaning agent used to work by asking an LLM to write freeform
``clean_data(df)`` Python, running it, and checking afterwards that the null
count hadn't gone up. That gate cannot see value-level change, so silently
corrupting transformations (rescaling a column, overwriting every value with
the mean, shuffling a column against the wrong rows, deleting a column,
mangling a join key) all passed it — and the "transformation log" was whatever
the model chose to ``print()``, i.e. unverified narration.

This module defines the contract that replaces that arrangement:

  * ``CleaningStep``   — one *typed, parameterised* operation the planner asks
    for, drawn from a fixed registry. The planner chooses and parameterises;
    it never writes code.
  * ``OperatorSpec``   — the static declaration of what an operator is allowed
    to do (remove rows? drop columns? introduce nulls?). This is what makes
    the validator's authorization check possible: observed effects must be a
    subset of what the plan's operators were permitted to cause.
  * ``StepLedger`` / ``CleaningLedger`` — what *actually happened*, measured by
    diffing the before/after frames in ``tools.cleaning_ops.apply_step``.
    An operator never reports its own numbers, so an operator bug shows up as
    a ledger/validator failure rather than as a confident false log line.

See ``tools/cleaning_ops.py`` for the registry and the apply engine, and
``agents/cleaning/validator.py`` for the invariants enforced over the ledger.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

StepSource = Literal["generated", "manual", "detector", "fallback"]


class ParamSpec(BaseModel):
    """Declared shape of one operator parameter, used to validate a planned
    step *before* anything runs — an unknown or malformed parameter is a
    planning error we can reject cheaply, not a runtime traceback."""

    name: str
    type: Literal["str", "int", "float", "bool", "list", "dict"]
    required: bool = False
    default: Any = None
    choices: list[Any] | None = None
    description: str = ""


class OperatorSpec(BaseModel):
    """Static, machine-readable declaration of a cleaning operator.

    The ``may_*`` flags are the operator's *authorization*: the validator
    treats any observed effect not covered by some step's operator spec as a
    hard failure. Defaults are deliberately the restrictive ones — a new
    operator is assumed to change values in place, touch no other column, add
    and remove nothing, and never introduce a null.
    """

    name: str
    summary: str = Field(description="One line the planner prompt shows to the model")
    params: list[ParamSpec] = Field(default_factory=list)

    # ── Authorization ──────────────────────────────────────────────────
    may_remove_rows: bool = False
    may_add_columns: bool = False
    may_drop_columns: bool = False
    may_introduce_nulls: bool = Field(
        default=False,
        description="True only for operators where turning an unparseable value into "
                    "NULL is the correct, honest outcome (date/number/bool parsing, "
                    "sentinel replacement). Never a licence to fabricate a "
                    "replacement value to keep a null count flat.",
    )
    may_change_dtype: bool = False

    # ── Change scope ───────────────────────────────────────────────────
    # How far into a column an operator is allowed to reach. Column-level
    # authorization alone is too coarse: an `impute` step would otherwise
    # license rescaling every value in the column it was meant to fill.
    may_rewrite_values: bool = Field(
        default=True,
        description="May change a cell that already held a non-null value. False for "
                    "flag_*/dedupe operators, which must leave existing values untouched.",
    )
    may_fill_nulls: bool = Field(
        default=False,
        description="May turn a null into a value. Only imputation and explicit "
                    "value-mapping operators do this.",
    )

    # ── Targeting ──────────────────────────────────────────────────────
    requires_column: bool = True
    accepts_multiple_columns: bool = False

    def param(self, name: str) -> ParamSpec | None:
        return next((p for p in self.params if p.name == name), None)


class CleaningStep(BaseModel):
    """One planned operation.

    ``description`` is the business-language sentence shown in the UI; it is
    display only. ``op`` + ``columns`` + ``params`` are what actually executes,
    so a step whose prose and parameters disagree executes the parameters —
    and the ledger reports what the parameters did.
    """

    id: str
    op: str = Field(default="", description="Operator name from tools.cleaning_ops.REGISTRY")
    columns: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    description: str = ""
    source: StepSource = "generated"

    @property
    def is_typed(self) -> bool:
        """False for a legacy/free-text step that carries no operator."""
        return bool(self.op)


class CellChange(BaseModel):
    """A single before/after pair, sampled for the audit trail."""

    row: str
    column: str
    before: Any = None
    after: Any = None


class StepLedger(BaseModel):
    """Measured effect of one step. Every count here is computed by diffing
    the frame before and after the operator ran — see
    ``tools.cleaning_ops.apply_step``."""

    step_id: str
    op: str
    columns: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)

    applied: bool = True
    skipped_reason: str = ""
    error: str = ""

    rows_before: int = 0
    rows_after: int = 0
    rows_removed: int = 0
    rows_added: int = 0

    cells_changed: int = 0
    nulls_filled: int = 0
    nulls_introduced: int = 0

    columns_added: list[str] = Field(default_factory=list)
    columns_dropped: list[str] = Field(default_factory=list)
    dtype_changes: dict[str, str] = Field(default_factory=dict)

    samples: list[CellChange] = Field(default_factory=list)
    summary: str = ""

    @property
    def touched_columns(self) -> set[str]:
        return set(self.columns) | set(self.columns_added) | set(self.columns_dropped)


class CleaningLedger(BaseModel):
    """The full, measured audit trail for one table's cleaning run."""

    table_name: str = ""
    steps: list[StepLedger] = Field(default_factory=list)
    rows_before: int = 0
    rows_after: int = 0

    @property
    def applied_steps(self) -> list[StepLedger]:
        return [s for s in self.steps if s.applied]

    @property
    def total_cells_changed(self) -> int:
        return sum(s.cells_changed for s in self.applied_steps)

    def as_log(self) -> list[str]:
        """Human-readable transformation log — every line derived from measured
        counts, so it cannot claim a change that did not happen."""
        lines: list[str] = []
        for s in self.steps:
            if s.applied:
                lines.append(s.summary)
            elif s.error:
                lines.append(f"[failed] {s.op}: {s.error}")
            elif s.skipped_reason:
                lines.append(f"[skipped] {s.op}: {s.skipped_reason}")
        return lines
