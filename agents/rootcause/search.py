"""The drill-down search: find the smallest slice that explains the largest part
of a metric's change.

## The objective

A dataset with dimensions ``D₁…Dₖ`` defines a lattice of *slices* — conjunctions
of ``dimension = value`` predicates.  The whole business is the root; each level
down adds one predicate.  For a slice ``S`` and an additive measure ``M``:

    delta(S)     = M(current ∧ S) − M(prior ∧ S)
    EP(S)        = delta(S) / delta(∅)          "explanatory power"
    expected(S)  = M(prior ∧ S) × M(current) / M(prior)
    excess(S)    = M(current ∧ S) − expected(S)

The distinction between the last two lines is what separates this from a
group-by.  ``EP`` says how much of the change a slice *accounts for*; a slice can
top that list by being large.  ``excess`` says how much of it the business-wide
trend does **not** explain — a segment that shrank at exactly the company rate
contributed a lot and caused nothing.  A root cause has to score on both, and on
a third axis: how small a part of the business it is.  Explaining 92% of a
decline from 4% of the transactions is a finding; explaining 92% of it from 89%
of the transactions is a restatement of the total.

:func:`_score` combines the three.  Every factor is named and separately visible
in the output, so a ranking can be argued with rather than trusted.

## The combinatorial problem

Exhaustive evaluation is ``Π(cardinalityᵢ + 1)`` slices — eight dimensions
averaging 12 values each is ~800 million.  Four things make it tractable, and
only the first three are exact:

1. **Vectorised sibling evaluation.**  Children are never evaluated one at a
   time.  For a parent slice and a dimension, one ``bincount`` over the parent's
   rows produces the prior value, current value and row count of *every* child
   along that dimension at once.  Cost is O(rows in the parent), not
   O(rows × cardinality).

2. **Support pruning (exact).**  Row count is monotone non-increasing down the
   lattice, so a slice below the support floor has no descendant above it.

3. **Magnitude-bound pruning (exact).**  For a descendant ``S' ⊆ S``, each
   period's value is bounded by that period's positive/negative mass inside
   ``S``.  Hence ``|delta(S')| ≤ max(pos_cur(S) − neg_pri(S), pos_pri(S) −
   neg_cur(S))``, which for a non-negative measure is just
   ``max(M(current ∧ S), M(prior ∧ S))``.  A slice whose bound is below the
   materiality floor cannot contain a material finding, so the entire subtree is
   dropped without being visited.  Distinct-count measures are monotone too, so
   the same bound holds.

4. **Beam (approximate, and the only approximate step).**  Nodes are expanded
   best-first in decreasing order of that same bound — an admissible ordering:
   the bound is the most any sub-slice could still be worth.  Only the top
   ``beam_width`` survive per level.  With an unbounded beam the search is
   exhaustive; with a finite one it can in principle miss a small anomaly hiding
   under an unremarkable parent.  Both the width and how often it bound are
   reported in :class:`~agents.rootcause.schema.SearchStats`, never summarised
   away.
"""

from __future__ import annotations

import itertools
import math
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.stats import norm

from agents.rootcause.dimensions import Dimension, materialise
from agents.rootcause.measures import Measure, WindowPair
from agents.rootcause.schema import (
    Explanation,
    SearchStats,
    Slice,
    SlicePredicate,
)

# ── Defaults ────────────────────────────────────────────────────────────────
# A slice must move at least this share of the total change to be reported at
# all. Below it, the "finding" is smaller than the rounding on the headline.
DEFAULT_MIN_EXPLANATORY_POWER = 0.08
# Subtrees whose *upper bound* on any descendant's change is below this share of
# the total change are dropped whole. Lower than the reporting floor on purpose:
# pruning must never remove something that could still have been reported.
DEFAULT_PRUNE_FLOOR_SHARE = 0.04
# A slice needs this many rows across both periods before its change is a
# pattern rather than a handful of transactions.
DEFAULT_MIN_ROWS = 12
DEFAULT_MAX_DEPTH = 3
DEFAULT_BEAM_WIDTH = 24
DEFAULT_NODE_BUDGET = 40_000
DEFAULT_TOP_K = 6
# A child covering essentially all of its parent's rows is the parent with a
# redundant predicate bolted on; reporting both says the same thing twice.
REDUNDANCY_ROW_SHARE = 0.98
# Explanatory power is capped before scoring: a slice that over-explains by 4×
# (because other slices offset it) is not four times more interesting than one
# that over-explains by 2×, and uncapped it would dominate every ranking.
EP_CAP = 2.0
# Each extra predicate must earn its place: a depth-2 slice has to beat a
# depth-1 slice by this much to outrank it.
DEPTH_PENALTY = 0.15
# A slice carrying this many times its own weight in the change is as
# concentrated as a finding needs to be. Past it, more lift almost always means
# a smaller sample rather than a better answer, so the reward stops growing.
FOCUS_SATURATION_LIFT = 15.0
# A longer condition has to buy something. A descendant is only kept alongside
# its ancestor when it concentrates the change at least this much more tightly;
# otherwise it is the same finding with a redundant clause attached.
REFINEMENT_LIFT_GAIN = 1.25
# How many standard errors the excess must clear before a slice is reported.
# Small slices swing on their own randomness; without this, a 14-row segment
# that happened to have a quiet fortnight outranks a real segment failure.
DEFAULT_MIN_SIGNAL_TO_NOISE = 2.0
# Family-wise error rate the Bonferroni correction holds across every slice
# tested in one run. Explanations clearing the corrected bar are marked robust.
FAMILY_ALPHA = 0.05


@dataclass
class SearchConfig:
    max_depth: int = DEFAULT_MAX_DEPTH
    beam_width: int = DEFAULT_BEAM_WIDTH
    node_budget: int = DEFAULT_NODE_BUDGET
    min_rows: int = DEFAULT_MIN_ROWS
    min_explanatory_power: float = DEFAULT_MIN_EXPLANATORY_POWER
    prune_floor_share: float = DEFAULT_PRUNE_FLOOR_SHARE
    top_k: int = DEFAULT_TOP_K
    # 0 disables the noise test (useful on tiny demo datasets where every slice
    # is thin); the default keeps random swings out of the published list.
    min_signal_to_noise: float = DEFAULT_MIN_SIGNAL_TO_NOISE


@dataclass
class _Node:
    """One evaluated slice, plus the bookkeeping the search needs."""

    slice: Slice
    rows: np.ndarray          # integer positions into the frame
    prior: float
    current: float
    prior_rows: int
    current_rows: int
    bound: float              # max |delta| any descendant could still have
    parent_key: tuple = ()
    used_dimensions: frozenset[str] = field(default_factory=frozenset)


def _js_term(p: float, q: float) -> float:
    """One element's contribution to the Jensen–Shannon divergence between the
    prior and current share distributions — Adtributor's *surprise*.

    High when an element's share of the business moved, which catches a mix
    shift that leaves the total flat and therefore has no contribution at all.
    """
    if p <= 0 and q <= 0:
        return 0.0
    m = (p + q) / 2
    out = 0.0
    if p > 0:
        out += 0.5 * p * math.log2(p / m)
    if q > 0:
        out += 0.5 * q * math.log2(q / m)
    return float(out)


def _score(
    explanatory_power: float,
    excess_share: float,
    rows_share: float,
    depth: int,
) -> float:
    """Rank an explanation on contribution × isolation × parsimony × focus.

    Each factor answers a different objection to the finding:

    * **contribution** — "does this even matter?"  Capped |EP|.
    * **isolation** — "or did everything move together?"  How much of the change
      the business-wide trend fails to explain.
    * **parsimony** — "is this one cause or three coincidences?"  Each extra
      predicate is taxed.
    * **focus** — "is this a segment or just most of the business?"  Rewards
      explaining a lot of the change from a little of the volume.
    """
    contribution = min(abs(explanatory_power), EP_CAP)
    isolation = min(abs(excess_share), 1.0)
    parsimony = 1.0 / (1.0 + DEPTH_PENALTY * max(0, depth - 1))
    concentration = abs(explanatory_power) / rows_share if rows_share > 0 else 0.0
    focus = min(concentration / FOCUS_SATURATION_LIFT, 1.0)
    return contribution * (0.5 + 0.5 * isolation) * parsimony * (0.35 + 0.65 * focus)


class RootCauseSearch:
    """Best-first beam search over the slice lattice for one (measure, window)."""

    def __init__(
        self,
        df: pd.DataFrame,
        measure: Measure,
        dimensions: list[Dimension],
        windows: WindowPair,
        dt: pd.Series,
        *,
        config: SearchConfig | None = None,
        time_column: str | None = None,
    ) -> None:
        self.config = config or SearchConfig()
        self.measure = measure
        self.windows = windows
        self.stats = SearchStats(
            max_depth=self.config.max_depth,
            beam_width=self.config.beam_width,
            node_budget=self.config.node_budget,
            dimensions_searched=[d.name for d in dimensions],
        )

        n = len(df)
        cur_mask = windows.current.mask(dt).fillna(False).to_numpy(dtype=bool)
        pri_mask = windows.prior.mask(dt).fillna(False).to_numpy(dtype=bool)
        # −1 = outside both windows, 0 = prior, 1 = current. Rows in neither
        # period are carried through the frame but never aggregated.
        self._period = np.full(n, -1, dtype=np.int8)
        self._period[pri_mask] = 0
        self._period[cur_mask] = 1
        self._in_window = self._period >= 0

        self._additive = measure.values is not None
        if self._additive:
            self._values = np.nan_to_num(
                measure.values.to_numpy(dtype=float), nan=0.0, posinf=0.0, neginf=0.0,
            )
            self._signed = bool((self._values < 0).any())
            self._pos = np.clip(self._values, 0.0, None)
            self._neg = np.clip(self._values, None, 0.0)
        else:
            self._values = None
            self._signed = False
            self._distinct_codes = pd.factorize(measure.distinct.astype(str))[0]

        # Dimensions are factorised once. Every later aggregation is an integer
        # bincount rather than a string group-by, which is what keeps a
        # thousand-node search inside a second.
        self._dims: dict[str, Dimension] = {d.name: d for d in dimensions}
        self._codes: dict[str, np.ndarray] = {}
        self._labels: dict[str, np.ndarray] = {}
        for dim in dimensions:
            keys = materialise(df, dim, time_column)
            codes, uniques = pd.factorize(keys, use_na_sentinel=False)
            self._codes[dim.name] = codes.astype(np.int64)
            self._labels[dim.name] = np.asarray([str(u) for u in uniques], dtype=object)

        all_rows = np.arange(n, dtype=np.int64)
        self.total_prior = self._aggregate(all_rows, 0)
        self.total_current = self._aggregate(all_rows, 1)
        self.total_delta = self.total_current - self.total_prior
        self.total_prior_rows = int(pri_mask.sum())
        self.total_current_rows = int(cur_mask.sum())
        self.total_rows = self.total_prior_rows + self.total_current_rows
        self.growth = (
            self.total_current / self.total_prior if self.total_prior else float("nan")
        )

        self.stats.exhaustive_combinations = self._exhaustive_size(dimensions)
        self._evaluated: dict[tuple, _Node] = {}
        # Material slices dropped because their move was inside their own noise.
        # Counted so the result can say so rather than silently showing fewer.
        self.rejected_as_noise = 0

    # ── Aggregation primitives ──────────────────────────────────────────────

    def _aggregate(self, rows: np.ndarray, period: int) -> float:
        sel = rows[self._period[rows] == period]
        if sel.size == 0:
            return 0.0
        if self._additive:
            return float(self._values[sel].sum())
        return float(np.unique(self._distinct_codes[sel]).size)

    def _children(self, node: _Node, dim_name: str) -> dict[str, np.ndarray]:
        """Prior/current value, row counts and mass bounds for EVERY child of
        *node* along *dim_name*, in one pass over the node's rows."""
        rows = node.rows
        sel = rows[self._in_window[rows]]
        if sel.size == 0:
            return {}
        codes = self._codes[dim_name][sel]
        period = self._period[sel].astype(np.int64)
        n_values = self._labels[dim_name].size
        comb = codes * 2 + period
        size = n_values * 2

        counts = np.bincount(comb, minlength=size).reshape(n_values, 2)
        if self._additive:
            sums = np.bincount(comb, weights=self._values[sel], minlength=size).reshape(n_values, 2)
            if self._signed:
                pos = np.bincount(comb, weights=self._pos[sel], minlength=size).reshape(n_values, 2)
                neg = np.bincount(comb, weights=self._neg[sel], minlength=size).reshape(n_values, 2)
            else:
                pos, neg = sums, np.zeros_like(sums)
        else:
            # Distinct counts cannot be summed; fall back to a group-by. Still
            # one pass per (node, dimension), just a slower one.
            frame = pd.DataFrame({
                "code": codes, "period": period, "key": self._distinct_codes[sel],
            })
            grouped = frame.groupby(["code", "period"])["key"].nunique()
            sums = np.zeros((n_values, 2), dtype=float)
            for (code, per), value in grouped.items():
                sums[int(code), int(per)] = float(value)
            pos, neg = sums, np.zeros_like(sums)

        return {
            "counts": counts, "sums": sums, "pos": pos, "neg": neg,
            "codes": np.flatnonzero(counts.sum(axis=1) > 0),
        }

    def _bound(self, pos: np.ndarray, neg: np.ndarray) -> float:
        """Largest |delta| any descendant of a slice with this mass could have.

        Exact: a descendant's value in each period lies within that period's
        [negative mass, positive mass], and the two periods vary independently.
        """
        return float(max(pos[1] - neg[0], pos[0] - neg[1]))

    def _exhaustive_size(self, dimensions: list[Dimension]) -> int:
        """How many distinct slices an exhaustive search of this lattice holds.

        Σ over every combination of ``depth`` dimensions of the product of their
        cardinalities.  Reported next to the number actually evaluated so the
        pruning is quantified rather than asserted.

        Enumerated exactly for schemas narrow enough to enumerate, and bounded
        (largest cardinalities first, which over-counts) beyond that — the
        alternative is spending real time computing a diagnostic.
        """
        cards = [d.cardinality for d in dimensions]
        depth_cap = min(self.config.max_depth, len(cards))
        total = 0.0
        if len(cards) <= 20:
            for depth in range(1, depth_cap + 1):
                for combo in itertools.combinations(cards, depth):
                    total += math.prod(combo)
                if total > 1e15:
                    return int(1e15)
            return int(total)
        for depth in range(1, depth_cap + 1):
            top = sorted(cards, reverse=True)[:depth]
            total += math.comb(len(cards), depth) * math.prod(top)
            if total > 1e15:
                return int(1e15)
        return int(total)

    # ── The search ──────────────────────────────────────────────────────────

    def run(self) -> list[Explanation]:
        started = time.perf_counter()
        if not np.isfinite(self.total_delta) or self.total_delta == 0:
            self.stats.elapsed_seconds = time.perf_counter() - started
            return []

        abs_delta = abs(self.total_delta)
        prune_floor = abs_delta * self.config.prune_floor_share
        root = _Node(
            slice=Slice(), rows=np.arange(len(self._period), dtype=np.int64),
            prior=self.total_prior, current=self.total_current,
            prior_rows=self.total_prior_rows, current_rows=self.total_current_rows,
            bound=abs_delta * 10,  # the root is never pruned
        )

        frontier = [root]
        candidates: list[_Node] = []

        for depth in range(1, self.config.max_depth + 1):
            next_frontier: list[_Node] = []
            for node in frontier:
                if self.stats.nodes_evaluated >= self.config.node_budget:
                    self.stats.budget_exhausted = True
                    break
                self.stats.nodes_expanded += 1
                for dim_name in self._dims:
                    if dim_name in node.used_dimensions:
                        continue
                    agg = self._children(node, dim_name)
                    if not agg:
                        continue
                    for code in agg["codes"]:
                        if self.stats.nodes_evaluated >= self.config.node_budget:
                            self.stats.budget_exhausted = True
                            break
                        self.stats.nodes_examined += 1

                        # A∧B is reachable from A and from B. Recognising the
                        # duplicate here — before any row filtering — is what
                        # keeps the work proportional to the number of distinct
                        # slices rather than to their permutations (a factor of
                        # depth! at the deepest level).
                        label = str(self._labels[dim_name][code])
                        child_key = tuple(sorted(node.slice.key() + ((dim_name, label),)))
                        if child_key in self._evaluated:
                            self.stats.duplicate_paths += 1
                            continue
                        self.stats.nodes_evaluated += 1

                        counts = agg["counts"][code]
                        prior_rows, current_rows = int(counts[0]), int(counts[1])
                        if prior_rows + current_rows < self.config.min_rows:
                            self.stats.pruned_by_support += 1
                            continue
                        if (prior_rows + current_rows) >= REDUNDANCY_ROW_SHARE * (
                            node.prior_rows + node.current_rows
                        ) and node.slice.depth > 0:
                            # Identical footprint to the parent — same finding.
                            self.stats.pruned_by_redundancy += 1
                            continue

                        bound = self._bound(agg["pos"][code], agg["neg"][code])
                        if bound < prune_floor:
                            self.stats.pruned_by_magnitude_bound += 1
                            continue

                        child = _Node(
                            slice=node.slice.with_predicate(SlicePredicate(dim_name, label)),
                            rows=node.rows[self._codes[dim_name][node.rows] == code],
                            prior=float(agg["sums"][code][0]),
                            current=float(agg["sums"][code][1]),
                            prior_rows=prior_rows, current_rows=current_rows,
                            bound=bound, parent_key=node.slice.key(),
                            used_dimensions=node.used_dimensions | {dim_name},
                        )
                        self._evaluated[child_key] = child
                        candidates.append(child)
                        next_frontier.append(child)
                if self.stats.budget_exhausted:
                    break

            if self.stats.budget_exhausted or depth == self.config.max_depth:
                break
            frontier = self._beam(next_frontier)
            if not frontier:
                break

        self.stats.elapsed_seconds = time.perf_counter() - started
        return self._select(candidates)

    def _beam(self, frontier: list[_Node]) -> list[_Node]:
        """Choose which nodes get expanded — split between mass and anomaly.

        A single ordering is not enough here, and getting this wrong is how a
        drill-down quietly fails.  Ranking by the magnitude bound alone is
        ordering by slice *size*: the search then drills relentlessly into the
        biggest segments and never reaches the small, sharp anomaly that is the
        entire point.  Ranking by the node's own score alone is worse in the
        other direction — it chases whatever already looks odd and never opens
        the large, unremarkable parent that a real failure is hiding inside.

        So the beam is split.  Half of it goes to the highest magnitude bounds,
        which guarantees every slice that could still contain a large
        contribution is opened.  Half goes to the highest current scores, which
        guarantees the anomalous branches are followed even when they are small.
        """
        width = self.config.beam_width
        if len(frontier) <= width:
            return frontier

        by_mass = sorted(frontier, key=lambda nd: -nd.bound)
        by_merit = sorted(frontier, key=lambda nd: -self._merit(nd))
        half = max(1, width // 2)

        chosen: list[_Node] = []
        seen: set[tuple] = set()
        for pool, quota in ((by_mass, half), (by_merit, width - half)):
            taken = 0
            for node in pool:
                if taken >= quota:
                    break
                key = node.slice.key()
                if key in seen:
                    continue
                seen.add(key)
                chosen.append(node)
                taken += 1
        # Either pool may run dry against the other's picks; top up from mass
        # order so the beam is never narrower than it was configured to be.
        for node in by_mass:
            if len(chosen) >= width:
                break
            if node.slice.key() not in seen:
                seen.add(node.slice.key())
                chosen.append(node)

        self.stats.pruned_by_beam += len(frontier) - len(chosen)
        return chosen

    # ── Turning nodes into published explanations ───────────────────────────

    def _signal_to_noise(self, node: _Node) -> float | None:
        """How many standard errors the slice's excess clears.

        A slice's value in a period is a random sum: an uncertain *number* of
        transactions, each of an uncertain *size*.  Modelling only the sizes
        would call a segment with perfectly uniform prices noiseless, when in
        fact its whole variability is in how many orders arrived.  The compound
        Poisson variance covers both — ``Var(Σx) ≈ n·E[x²]``, which is simply
        ``Σx²`` over the slice's rows — and needs no extra pass.

        The excess compares the current sum against ``prior × growth``, so

            Var(excess) ≈ Σx²(current) + growth² · Σx²(prior)

        Dividing through separates "this segment broke" from "this segment is
        small and had a quiet fortnight" — the most common way a contribution
        analysis produces a confident wrong answer.

        ``None`` for distinct-count measures, where a sum-over-rows variance is
        the wrong model and asserting one would be worse than declining to.
        """
        if not self._additive or not np.isfinite(self.growth):
            return None
        rows = node.rows
        cur = self._values[rows[self._period[rows] == 1]]
        pri = self._values[rows[self._period[rows] == 0]]
        if cur.size + pri.size < 2:
            return None
        variance = float(np.square(cur).sum()) + (self.growth ** 2) * float(np.square(pri).sum())
        if variance <= 0:
            # Every row in the slice is worth exactly zero in both periods, so
            # there is no movement to test.
            return float("inf")
        excess = node.current - node.prior * self.growth
        return float(abs(excess) / math.sqrt(variance))

    def _corrected_threshold(self) -> float:
        """The z a slice must clear once the number of slices tested is allowed for.

        Bonferroni: to hold the family-wise error rate at ``FAMILY_ALPHA`` across
        ``n`` two-sided tests, each test runs at ``alpha/n``. It is the
        conservative choice, and conservative is the right bias here — the cost
        of a missed lead is that the user drills manually, while the cost of a
        false cause is that they act on it.
        """
        n = max(1, self.stats.slices_tested)
        return float(norm.isf(FAMILY_ALPHA / (2 * n)))

    def _merit(self, node: _Node) -> float:
        """The node's own score, cheaply — no complement aggregation.

        Used to steer the beam, where it is needed for every surviving node and
        the exact figures are not.
        """
        if not self.total_delta:
            return 0.0
        rows_share = (
            (node.prior_rows + node.current_rows) / self.total_rows if self.total_rows else 0.0
        )
        ep = (node.current - node.prior) / self.total_delta
        excess_share = (
            (node.current - node.prior * self.growth) / self.total_delta
            if np.isfinite(self.growth) else 0.0
        )
        return _score(ep, excess_share, rows_share, node.slice.depth)

    def _explain(self, node: _Node) -> Explanation:
        delta = node.current - node.prior
        expected = node.prior * self.growth if np.isfinite(self.growth) else float("nan")
        excess = node.current - expected if np.isfinite(expected) else 0.0
        rows_share = (
            (node.prior_rows + node.current_rows) / self.total_rows if self.total_rows else 0.0
        )
        ep = delta / self.total_delta if self.total_delta else 0.0
        excess_share = excess / self.total_delta if self.total_delta else 0.0
        p = node.prior / self.total_prior if self.total_prior else 0.0
        q = node.current / self.total_current if self.total_current else 0.0

        # The complement is aggregated, not subtracted: for a distinct-count
        # measure "everything else" is not "total minus this slice".
        complement = np.setdiff1d(
            np.flatnonzero(self._in_window), node.rows, assume_unique=False,
        )
        rest_prior = self._aggregate(complement, 0)
        rest_current = self._aggregate(complement, 1)

        return Explanation(
            slice=node.slice,
            prior_value=node.prior,
            current_value=node.current,
            delta=delta,
            change_pct=(delta / node.prior * 100) if node.prior else None,
            explanatory_power=ep,
            expected_current=expected if np.isfinite(expected) else 0.0,
            excess=excess,
            excess_share=excess_share,
            prior_rows=node.prior_rows,
            current_rows=node.current_rows,
            rows_share=rows_share,
            concentration=(abs(ep) / rows_share) if rows_share else 0.0,
            surprise=_js_term(max(p, 0.0), max(q, 0.0)),
            signal_to_noise=self._signal_to_noise(node),
            rest_prior=rest_prior,
            rest_current=rest_current,
            rest_delta=rest_current - rest_prior,
            rest_change_pct=(
                (rest_current - rest_prior) / rest_prior * 100 if rest_prior else None
            ),
            score=_score(ep, excess_share, rows_share, node.slice.depth),
        )

    def _select(self, candidates: list[_Node]) -> list[Explanation]:
        """Rank, filter to material same-direction slices, and drop repeats."""
        if not candidates:
            return []
        direction = 1 if self.total_delta > 0 else -1
        scored: list[Explanation] = []
        for node in candidates:
            delta = node.current - node.prior
            if delta == 0 or (1 if delta > 0 else -1) != direction:
                continue
            if abs(delta / self.total_delta) < self.config.min_explanatory_power:
                continue
            self.stats.slices_tested += 1
            explanation = self._explain(node)
            threshold = self.config.min_signal_to_noise
            if (
                threshold > 0
                and explanation.signal_to_noise is not None
                and explanation.signal_to_noise < threshold
            ):
                # The slice moved, but not by more than its own week-to-week
                # randomness. Publishing it as a cause would be the analysis
                # equivalent of reading tea leaves.
                self.rejected_as_noise += 1
                continue
            scored.append(explanation)

        # ── Correct for how many slices were tested ────────────────────────
        # A 2σ bar applied independently to 300 slices produces roughly fifteen
        # "findings" from data with no cause in it at all. That is not a corner
        # case — it is what a drill-down does by default, and it is the reason
        # automated attribution has a reputation for confident nonsense. The bar
        # is raised by Bonferroni over the number of slices actually tested, and
        # explanations are labelled by whether they clear it rather than being
        # silently dropped: a lead worth checking is still worth showing, it
        # just must not be presented as a conclusion.
        self.stats.corrected_threshold = self._corrected_threshold()
        for explanation in scored:
            z = explanation.signal_to_noise
            if z is None:
                explanation.robust = None
                continue
            explanation.p_value = float(2.0 * norm.sf(min(z, 40.0)))
            explanation.robust = bool(z >= self.stats.corrected_threshold)

        # Robust first, then by score. A smaller slice that provably moved
        # outranks a larger one that merely might have: the reader acts on rank 1,
        # and it has to be the finding that survives being argued with.
        scored.sort(key=lambda e: (0 if e.robust else 1, -e.score))

        chosen: list[Explanation] = []
        for candidate in scored:
            keys = set(candidate.slice.key())
            redundant = False
            for kept in chosen:
                kept_keys = set(kept.slice.key())
                if keys > kept_keys:
                    # A refinement of something already chosen. It earns a place
                    # of its own only by isolating the change materially more
                    # tightly; otherwise it is the same finding with an extra
                    # clause, and the drill path already shows the refinement.
                    if candidate.concentration <= kept.concentration * REFINEMENT_LIFT_GAIN:
                        redundant = True
                        break
                elif keys < kept_keys:
                    # A generalisation of something already chosen — which
                    # outscored it, so it is the sharper answer. The broader
                    # slice survives in that explanation's drill path.
                    redundant = True
                    break
                elif keys == kept_keys:
                    redundant = True
                    break
            if redundant:
                continue
            chosen.append(candidate)
            if len(chosen) >= self.config.top_k:
                break
        for i, explanation in enumerate(chosen, start=1):
            explanation.rank = i
        return chosen

    # ── Ancillary views ─────────────────────────────────────────────────────

    def drill_path(self, explanation: Explanation) -> list[dict]:
        """The refinement chain that reached this slice, one predicate at a time.

        Shows how much each added condition sharpened the explanation — the
        difference between "Office Bulk Subscription is down" and "Office Bulk
        Subscription is down *only in the North, only via Email*".
        """
        path: list[dict] = []
        prefix: list[SlicePredicate] = []
        for predicate in explanation.slice.predicates:
            prefix.append(predicate)
            node = self._evaluated.get(Slice(tuple(prefix)).key())
            if node is None:
                # A prefix the beam never visited on its own (the chain was
                # discovered in a different predicate order). Evaluate it
                # directly rather than leaving a hole in the path.
                node = self._node_for(Slice(tuple(prefix)))
            step = self._explain(node)
            path.append({
                "added": predicate.render(),
                "slice_label": step.slice.render_compact(),
                "depth": step.slice.depth,
                "delta": round(step.delta, 2),
                "explanatory_power_pct": round(step.explanatory_power * 100, 1),
                "rows_share_pct": round(step.rows_share * 100, 1),
                "concentration": round(step.concentration, 2),
            })
        return path

    def _node_for(self, target: Slice) -> _Node:
        rows = np.arange(len(self._period), dtype=np.int64)
        for predicate in target.predicates:
            labels = self._labels[predicate.dimension]
            matches = np.flatnonzero(labels == predicate.value)
            code = int(matches[0]) if matches.size else -1
            rows = rows[self._codes[predicate.dimension][rows] == code]
        prior, current = self._aggregate(rows, 0), self._aggregate(rows, 1)
        period = self._period[rows]
        return _Node(
            slice=target, rows=rows, prior=prior, current=current,
            prior_rows=int((period == 0).sum()), current_rows=int((period == 1).sum()),
            bound=abs(current - prior), used_dimensions=target.dimensions,
        )

    def per_dimension_summary(self) -> list[dict]:
        """Single-dimension Adtributor, one row per dimension.

        This is the view a human recognises — "by product it takes 2 items to
        explain two thirds of the drop; by region it takes 5" — and it is the
        justification for the multi-dimensional search: the dimension that needs
        the fewest elements is the one worth drilling into.
        """
        root = _Node(
            slice=Slice(), rows=np.arange(len(self._period), dtype=np.int64),
            prior=self.total_prior, current=self.total_current,
            prior_rows=self.total_prior_rows, current_rows=self.total_current_rows,
            bound=abs(self.total_delta),
        )
        out: list[dict] = []
        for dim_name, dim in self._dims.items():
            agg = self._children(root, dim_name)
            if not agg:
                continue
            rows: list[dict] = []
            divergence = 0.0
            for code in agg["codes"]:
                prior, current = float(agg["sums"][code][0]), float(agg["sums"][code][1])
                delta = current - prior
                p = prior / self.total_prior if self.total_prior else 0.0
                q = current / self.total_current if self.total_current else 0.0
                divergence += _js_term(max(p, 0.0), max(q, 0.0))
                rows.append({
                    "value": str(self._labels[dim_name][code]),
                    "prior": round(prior, 2), "current": round(current, 2),
                    "delta": round(delta, 2),
                    "explanatory_power_pct": round(
                        delta / self.total_delta * 100 if self.total_delta else 0.0, 1
                    ),
                })
            direction = 1 if self.total_delta > 0 else -1
            same_way = sorted(
                [r for r in rows if (1 if r["delta"] > 0 else -1) == direction and r["delta"] != 0],
                key=lambda r: -abs(r["delta"]),
            )
            # Adtributor's TEEP: how many elements it takes to reach two thirds
            # of the change. Fewer is a cleaner explanation.
            cumulative, needed = 0.0, 0
            for row in same_way:
                cumulative += abs(row["delta"])
                needed += 1
                if cumulative >= abs(self.total_delta) * (2 / 3):
                    break
            out.append({
                "dimension": dim_name,
                "role": dim.role,
                "cardinality": dim.cardinality,
                "divergence": round(divergence, 5),
                "elements_for_two_thirds": needed if same_way else None,
                "top_elements": same_way[:5],
                "offsetting_elements": sorted(
                    [r for r in rows if (1 if r["delta"] > 0 else -1) != direction and r["delta"] != 0],
                    key=lambda r: -abs(r["delta"]),
                )[:3],
            })
        # Fewest elements first, then by how much the mix actually shifted.
        out.sort(key=lambda d: (d["elements_for_two_thirds"] or 10**6, -d["divergence"]))
        return out
