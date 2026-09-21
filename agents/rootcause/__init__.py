"""Automated root-cause analysis — find the smallest slice of the business that
explains the largest part of a metric's change.

The Insights agent already answers *what* moved and *which lever* moved it
("revenue fell 6,656 — 4,514 of that from order count, not basket size").  The
next question a human analyst always asks is *where*: which product, which
region, which channel, which combination of the three.  Answering it by hand
means slicing the data along every dimension and every pair of dimensions until
something stands out.  That is a search problem with a well-defined objective,
which means it can be done exactly, in code, and proven — see
:mod:`agents.rootcause.search` for the objective and the pruning that makes the
search tractable.

Public entry point: :func:`agents.rootcause.engine.run_root_cause`.
"""

from agents.rootcause.engine import run_root_cause
from agents.rootcause.schema import (
    Explanation,
    RootCauseResult,
    SearchStats,
    Slice,
    SlicePredicate,
)

__all__ = [
    "Explanation",
    "RootCauseResult",
    "SearchStats",
    "Slice",
    "SlicePredicate",
    "run_root_cause",
]
