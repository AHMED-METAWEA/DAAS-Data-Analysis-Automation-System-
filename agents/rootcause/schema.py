"""Data contracts for root-cause analysis.

Every quantity a root-cause explanation can state is a field on one of these
dataclasses, computed by exact arithmetic in :mod:`agents.rootcause.search`.
Nothing downstream — not the API, not the narration prompt — is allowed to
derive a new number; it may only read these.  That is the same discipline the
insights pipeline uses (compute first, narrate second) applied to a second
agent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SlicePredicate:
    """One ``dimension = value`` condition."""

    dimension: str
    value: str

    def render(self) -> str:
        return f"{self.dimension} = {self.value}"


@dataclass(frozen=True)
class Slice:
    """A conjunction of predicates — a rectangle in the dimension lattice.

    The empty slice (no predicates) is the whole dataset, which is the root of
    the search.
    """

    predicates: tuple[SlicePredicate, ...] = ()

    @property
    def depth(self) -> int:
        return len(self.predicates)

    @property
    def dimensions(self) -> frozenset[str]:
        return frozenset(p.dimension for p in self.predicates)

    def key(self) -> tuple[tuple[str, str], ...]:
        """Order-independent identity, so ``A∧B`` and ``B∧A`` are one node."""
        return tuple(sorted((p.dimension, p.value) for p in self.predicates))

    def with_predicate(self, predicate: SlicePredicate) -> Slice:
        return Slice(predicates=self.predicates + (predicate,))

    def render(self, joiner: str = " AND ") -> str:
        if not self.predicates:
            return "the whole business"
        return joiner.join(p.render() for p in self.predicates)

    def render_compact(self) -> str:
        """Human phrasing: ``Office Bulk Subscription · North · Email``."""
        if not self.predicates:
            return "everything"
        return " · ".join(p.value for p in self.predicates)

    def as_dict(self) -> dict[str, str]:
        return {p.dimension: p.value for p in self.predicates}


@dataclass
class Explanation:
    """One candidate answer to "where did the change come from?".

    The fields divide into three groups, and the distinction between them is the
    whole point of the analysis:

    * **Contribution** (``delta``, ``explanatory_power``) — how much of the
      total change this slice accounts for.  A slice can dominate the
      contribution simply by being big.
    * **Excess** (``expected_current``, ``excess``, ``excess_share``) — how much
      of that is *not* explained by the business-wide trend.  A slice that grew
      at exactly the company rate is a passenger, not a cause, however large its
      contribution.
    * **Concentration** (``rows_share``, ``concentration``) — how small a part of
      the business it is.  "92% of the decline from 4% of the transactions" is a
      finding; "92% of the decline from 89% of the transactions" is a tautology.
    """

    slice: Slice

    prior_value: float
    current_value: float
    delta: float
    change_pct: float | None

    # Share of the total change this slice accounts for (signed; can exceed 1
    # when other slices move the other way and offset it).
    explanatory_power: float
    # What this slice would have been worth had it moved at the business-wide
    # rate, and by how much it missed that.
    expected_current: float
    excess: float
    excess_share: float

    # Footprint
    prior_rows: int
    current_rows: int
    rows_share: float
    # |explanatory_power| ÷ rows_share — "23× more of the damage than its size".
    concentration: float

    # Divergence of this element's share of the dimension between the two
    # periods (Adtributor's surprise term). Ranks *mix shifts* that a pure
    # contribution measure misses.
    surprise: float

    # How many standard errors the excess clears — the guard against reading a
    # small slice's ordinary randomness as a cause. ``None`` when the measure
    # is a distinct count, where a sum-of-rows variance is the wrong model.
    signal_to_noise: float | None = None
    # Two-sided p-value for that z, uncorrected.
    p_value: float | None = None
    # Whether it survives the Bonferroni correction for the number of slices
    # tested in this run. ``False`` means "a lead worth checking", not "a cause".
    robust: bool | None = None

    # How the slice's own change splits into order count vs order size, using
    # the same decomposition as the insights revenue bridge. ``None`` when the
    # measure or the data cannot support it.
    bridge: dict[str, Any] | None = None

    # The rest of the business, measured — this is what licenses the sentence
    # "everything else is flat".
    rest_prior: float = 0.0
    rest_current: float = 0.0
    rest_delta: float = 0.0
    rest_change_pct: float | None = None

    # Ranking score (see search._score). Exposed so the UI can show why one
    # explanation outranked another rather than presenting an opaque order.
    score: float = 0.0
    rank: int = 0

    @property
    def direction(self) -> str:
        return "decline" if self.delta < 0 else "increase"

    def as_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "slice": self.slice.as_dict(),
            "slice_label": self.slice.render_compact(),
            "slice_expression": self.slice.render(),
            "depth": self.slice.depth,
            "prior_value": round(self.prior_value, 2),
            "current_value": round(self.current_value, 2),
            "delta": round(self.delta, 2),
            "change_pct": None if self.change_pct is None else round(self.change_pct, 1),
            "explanatory_power": round(self.explanatory_power, 4),
            "explanatory_power_pct": round(self.explanatory_power * 100, 1),
            "expected_current": round(self.expected_current, 2),
            "excess": round(self.excess, 2),
            "excess_share": round(self.excess_share, 4),
            "excess_share_pct": round(self.excess_share * 100, 1),
            "prior_rows": self.prior_rows,
            "current_rows": self.current_rows,
            "rows_share": round(self.rows_share, 4),
            "rows_share_pct": round(self.rows_share * 100, 1),
            "concentration": round(self.concentration, 2),
            "surprise": round(self.surprise, 5),
            "signal_to_noise": (
                None if self.signal_to_noise is None
                # inf means the measure has no row-level variance at all, so the
                # sum is exact — reported as a very large finite number rather
                # than a JSON-illegal Infinity.
                else (999.0 if not (self.signal_to_noise < 999.0) else round(self.signal_to_noise, 2))
            ),
            "p_value": None if self.p_value is None else float(f"{self.p_value:.3g}"),
            "robust": self.robust,
            "bridge": self.bridge,
            "rest_prior": round(self.rest_prior, 2),
            "rest_current": round(self.rest_current, 2),
            "rest_delta": round(self.rest_delta, 2),
            "rest_change_pct": None if self.rest_change_pct is None else round(self.rest_change_pct, 1),
            "direction": self.direction,
            "score": round(self.score, 4),
        }


@dataclass
class SearchStats:
    """What the search actually did — published, never summarised away.

    A drill-down that quietly stopped early would present a partial answer as a
    complete one.  Every bound that bound is counted here and surfaced in the
    API and the UI.
    """

    dimensions_searched: list[str] = field(default_factory=list)
    dimensions_skipped: list[dict[str, str]] = field(default_factory=list)
    max_depth: int = 1
    beam_width: int = 0
    node_budget: int = 0
    # Distinct slices actually computed.
    nodes_evaluated: int = 0
    # Candidate children looked at, including ones recognised as duplicates of a
    # slice already reached by a different predicate order.
    nodes_examined: int = 0
    duplicate_paths: int = 0
    nodes_expanded: int = 0
    pruned_by_support: int = 0
    pruned_by_magnitude_bound: int = 0
    pruned_by_beam: int = 0
    pruned_by_redundancy: int = 0
    budget_exhausted: bool = False
    elapsed_seconds: float = 0.0
    # Distinct slices in the full lattice — what an exhaustive search would cost.
    exhaustive_combinations: int = 0
    # Slices that passed the materiality filter and were therefore significance-
    # tested; the multiplicity the corrected threshold is derived from.
    slices_tested: int = 0
    corrected_threshold: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "dimensions_searched": self.dimensions_searched,
            "dimensions_skipped": self.dimensions_skipped,
            "max_depth": self.max_depth,
            "beam_width": self.beam_width,
            "node_budget": self.node_budget,
            "nodes_evaluated": self.nodes_evaluated,
            "nodes_examined": self.nodes_examined,
            "duplicate_paths": self.duplicate_paths,
            "nodes_expanded": self.nodes_expanded,
            "pruned_by_support": self.pruned_by_support,
            "pruned_by_magnitude_bound": self.pruned_by_magnitude_bound,
            "pruned_by_beam": self.pruned_by_beam,
            "pruned_by_redundancy": self.pruned_by_redundancy,
            "budget_exhausted": self.budget_exhausted,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "exhaustive_combinations": self.exhaustive_combinations,
            "slices_tested": self.slices_tested,
            "corrected_threshold": round(self.corrected_threshold, 2),
            "search_reduction": (
                round(self.exhaustive_combinations / self.nodes_evaluated, 1)
                if self.nodes_evaluated and self.exhaustive_combinations else None
            ),
        }


@dataclass
class Window:
    """One of the two periods being compared."""

    label: str
    start: str
    end: str
    value: float
    rows: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label, "start": self.start, "end": self.end,
            "value": round(self.value, 2), "rows": self.rows,
        }


@dataclass
class RootCauseResult:
    """Everything one drill-down produced."""

    available: bool
    reason: str = ""

    measure: str = "revenue"
    measure_label: str = "Revenue"
    measure_unit: str = "currency"
    measure_basis: str = ""
    # False for count-distinct measures (orders), where slice values do not sum
    # to the whole because one order can span several products. Stated, never
    # hidden — the contribution percentages are still meaningful, the *sum* of
    # them is not.
    measure_additive: bool = True

    current: Window | None = None
    prior: Window | None = None
    total_delta: float = 0.0
    total_change_pct: float | None = None

    explanations: list[Explanation] = field(default_factory=list)
    # Best single explanation per dimension, for the "which lens explains most"
    # comparison table — this is the classic single-dimension Adtributor result,
    # kept because it is the view a human recognises.
    per_dimension: list[dict[str, Any]] = field(default_factory=list)
    # How the top explanation was reached, one predicate at a time. Shows the
    # search narrowing rather than presenting its conclusion as an oracle.
    drill_path: list[dict[str, Any]] = field(default_factory=list)
    # The dimensions the search had available, for the UI's filter controls.
    dimensions: list[dict[str, Any]] = field(default_factory=list)
    window_basis: str = ""

    stats: SearchStats = field(default_factory=SearchStats)
    warnings: list[str] = field(default_factory=list)
    narrative: str = ""
    figures: list[dict[str, Any]] = field(default_factory=list)
    verification: dict[str, Any] = field(default_factory=dict)

    @property
    def top(self) -> Explanation | None:
        return self.explanations[0] if self.explanations else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "reason": self.reason,
            "measure": self.measure,
            "measure_label": self.measure_label,
            "measure_unit": self.measure_unit,
            "measure_basis": self.measure_basis,
            "measure_additive": self.measure_additive,
            "current": self.current.as_dict() if self.current else None,
            "prior": self.prior.as_dict() if self.prior else None,
            "total_delta": round(self.total_delta, 2),
            "total_change_pct": (
                None if self.total_change_pct is None else round(self.total_change_pct, 1)
            ),
            "explanations": [e.as_dict() for e in self.explanations],
            "per_dimension": self.per_dimension,
            "drill_path": self.drill_path,
            "dimensions": self.dimensions,
            "window_basis": self.window_basis,
            "stats": self.stats.as_dict(),
            "warnings": self.warnings,
            "narrative": self.narrative,
            "figures": self.figures,
            "verification": self.verification,
        }
