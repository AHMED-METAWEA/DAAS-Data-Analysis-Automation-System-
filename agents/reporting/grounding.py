"""
Report grounding / faithfulness check.

Every LLM-written report in DAAS is *grounded*: the deterministic
engines (KPIs, RFM, forecasts, churn) compute the numbers and the model only
narrates them.  This module verifies that promise by extracting every material
figure from a generated report and checking it against the numeric values that
actually appear in the deterministic payload(s).

It is intentionally a *heuristic* guard, not a proof — it catches the common
failure mode where a model quietly invents or mis-states a figure.  The result
is surfaced in the UI ("14 of 14 figures traced to your data") and embedded in
the exported report, giving the output a measurable trust signal.

Pure standard-library; no Streamlit / pandas import so it stays unit-testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from math import floor, isfinite, log10

# ── Number extraction ──────────────────────────────────────────────────────

# Matches an optional currency symbol, a number (with thousands separators or
# plain), an optional magnitude suffix (K/M/B) and an optional trailing percent.
_NUM_RE = re.compile(
    r"(?P<cur>[$€£])?\s*"
    r"(?P<num>-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?)"
    r"\s*(?P<suffix>[KkMmBb])?"
    r"\s*(?P<pct>%)?"
)

_SUFFIX = {"k": 1e3, "m": 1e6, "b": 1e9}

# Materiality is decided by CONTEXT, not magnitude: a bare number is treated as
# report *scaffolding* (not a data claim) only when the surrounding words say so
# — a ranking/section cue before it, a time-horizon or enumeration noun after
# it, a calendar year, or a list ordinal. Every other number, however small, is
# a data claim and must be verified. (Currency and percentages are always data.)
# This closes the old loophole where any plain number below 1,000 silently
# bypassed grounding.

# A ranking / section cue immediately before the number ("top 5", "section 3",
# "Decision 2"). These label a piece of the report's own structure, so the digit
# is a heading counter rather than a claim about the business.
_STRUCTURAL_BEFORE = re.compile(
    r"(?<![a-z])(?:top|bottom|next|last|first|past|previous|prior|upcoming|"
    r"section|step|phase|part|chapter|tier|figure|fig|table|page|no|number|"
    r"rank|priority|priorities|item|decision|action|recommendation|option|"
    r"scenario|q[1-4]?)\s*[:#.\-]?\s*$",
    re.IGNORECASE,
)
# A time-horizon unit right after the number ("30 days", "7d", "next 3 months").
_TIME_UNIT_AFTER = re.compile(
    r"^\s*[-_/]?\s*(?:d|w|m|q|y|h|days?|weeks?|months?|quarters?|years?|hours?|"
    r"mins?|minutes?|business\s+days?)(?![a-z])",
    re.IGNORECASE,
)
# A report-structure noun right after the number ("3 recommendations",
# "2 key risks"). Deliberately EXCLUDES data entities (customers, orders,
# products, revenue, segments) so real counts of those stay verifiable.
_ENUM_NOUN_AFTER = re.compile(
    r"^\s*(?:key\s+|main\s+|core\s+|top\s+|major\s+)?(?:priorit(?:y|ies)|"
    r"recommendations?|actions?|risks?|opportunit(?:y|ies)|insights?|points?|"
    r"reasons?|factors?|steps?|areas?|ways?|tips?|strateg(?:y|ies)|initiatives?|"
    r"options?|sections?|takeaways?|highlights?|findings?|bullets?|themes?|"
    r"phases?|stages?)(?![a-z])",
    re.IGNORECASE,
)
_ORDINAL_AFTER = re.compile(r"^[.)]\s")


@dataclass
class NumericClaim:
    """A single numeric figure asserted in the report text."""

    raw: str
    value: float
    kind: str  # "currency" | "percent" | "number"
    verified: bool = False
    # The exact source figure this claim matched, if any — the citation that
    # makes "traces back to your data" literally checkable per number.
    matched_value: float | None = None
    # How it was checked: "semantic" (matched the named metric/entity it was
    # attributed to), "magnitude" (matched some computed value), or
    # "unverified" (matched nothing / violated its named metric).
    basis: str = "unverified"


@dataclass
class GroundingResult:
    """Outcome of checking a report's figures against the source payload."""

    claims: list[NumericClaim] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.claims)

    @property
    def verified_count(self) -> int:
        return sum(1 for c in self.claims if c.verified)

    @property
    def unverified(self) -> list[NumericClaim]:
        return [c for c in self.claims if not c.verified]

    @property
    def traces(self) -> list[dict]:
        """Per-figure citation: each verified claim and the exact source value
        it was matched against. This is the explicit, machine-checkable audit
        trail behind the grounding badge — a reviewer can see *which* computed
        number each figure in the prose traces to (and spot a right-number/
        wrong-place misattribution that a bare pass/fail would hide)."""
        return [
            {
                "figure": c.raw,
                "value": c.value,
                "kind": c.kind,
                "matched_source_value": c.matched_value,
                "basis": c.basis,
            }
            for c in self.claims
            if c.verified
        ]

    @property
    def semantic_count(self) -> int:
        """Figures verified against the exact metric/entity they were attributed
        to (the strongest guarantee), not merely a magnitude match."""
        return sum(1 for c in self.claims if c.verified and c.basis == "semantic")

    def certificate(self) -> dict:
        """The final verification verdict for a report — numerical correctness,
        semantic correctness, and traceability in one machine-checkable object.
        Every agent attaches this to its output so nothing ships unverified and
        silent."""
        return {
            "numerical_ok": self.status in ("none", "clean"),
            "semantic_ok": all(c.basis != "unverified" for c in self.claims),
            "fully_traceable": all(
                c.matched_value is not None for c in self.claims if c.verified
            ),
            "figures_checked": self.total,
            "verified": self.verified_count,
            "semantically_verified": self.semantic_count,
            "coverage": round(self.coverage, 3),
            "status": self.status,
            "unverified_figures": [c.raw for c in self.unverified],
            "traces": self.traces,
        }

    @property
    def coverage(self) -> float:
        """Share of material figures traced back to the data (0.0–1.0)."""
        return 1.0 if self.total == 0 else self.verified_count / self.total

    @property
    def status(self) -> str:
        """One of ``"none"``, ``"clean"``, ``"partial"``, ``"weak"``."""
        if self.total == 0:
            return "none"
        if self.verified_count == self.total:
            return "clean"
        return "partial" if self.coverage >= 0.8 else "weak"

    @property
    def label(self) -> str:
        """Human-readable one-line summary for the UI / export footer."""
        if self.total == 0:
            return "No specific figures to verify in this report."
        if self.status == "clean":
            return (
                f"All {self.total} figures in this report trace back to your data."
            )
        return (
            f"{self.verified_count} of {self.total} figures traced to your data "
            f"({len(self.unverified)} could not be matched)."
        )


# ── Helpers ─────────────────────────────────────────────────────────────────

def _to_float(token: str) -> float | None:
    """Parse a numeric token, rejecting non-finite values.

    ``float()`` happily parses "nan"/"inf"/"-Infinity", so a payload string
    leaf carrying one of those would otherwise enter the known-value pool and
    blow up the significant-figure rounding below.
    """
    try:
        value = float(token.replace(",", ""))
    except (TypeError, ValueError):
        return None
    return value if isfinite(value) else None


def _sig_round(x: float, sig: int) -> float:
    """Round *x* to *sig* significant figures (handles the way models round).

    Non-finite input is returned unchanged rather than raising: ``log10(nan)``
    is nan and ``log10(inf)`` is inf, and ``int()`` on either throws
    (``ValueError``/``OverflowError``). Callers only ever compare the result,
    and every comparison against nan/inf is False, so a non-finite value simply
    fails to match instead of crashing the whole report check.
    """
    if not isfinite(x) or x == 0:
        return 0.0 if x == 0 else x
    return round(x, -int(floor(log10(abs(x)))) + (sig - 1))


def _close(a: float, b: float, rel: float, abs_tol: float) -> bool:
    return abs(a - b) <= max(abs_tol, rel * max(abs(a), abs(b)))


def _matches(claim: float, known: float, rel: float = 0.02, abs_tol: float = 0.5) -> bool:
    """True if *claim* equals *known* within tolerance or a sensible rounding.

    Accepts honest 2–3 significant-figure rounding (e.g. 12,345 quoted as
    "$12,300") but NOT 1-significant-figure rounding: collapsing 12,345 to
    "$10,000" is a 19% distortion, and — because the checker matches against
    *every* numeric leaf in the payload — a 1-sig-fig rung let almost any round
    number find a spurious "match", which quietly inflated the trust badge.
    """
    if _close(claim, known, rel, abs_tol):
        return True
    for sig in (2, 3):
        if abs(claim - _sig_round(known, sig)) <= 1e-9:
            return True
    return False


def collect_known_values(payload: object, _budget: int = 20000) -> list[float]:
    """Recursively pull every numeric leaf out of a payload (dict/list/scalar)."""
    out: list[float] = []

    def walk(node: object) -> None:
        if len(out) >= _budget:
            return
        if isinstance(node, bool):
            return  # bool is an int subclass — never a data figure
        if isinstance(node, (int, float)):
            # NaN/inf are routine in analytics payloads (empty group means,
            # pct-change on the first row, divide-by-zero rates) but are never
            # a figure a report can legitimately cite — and they crash
            # significant-figure rounding. Drop them at the source.
            value = float(node)
            if isfinite(value):
                out.append(value)
        elif isinstance(node, str):
            v = _to_float(node.strip())
            if v is not None:
                out.append(v)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, (list, tuple, set)):
            for v in node:
                walk(v)

    walk(payload)
    return out


def _value_kind(m: re.Match) -> tuple[float, str] | None:
    """(value, kind) for a regex match, or None if the number won't parse."""
    base = _to_float(m.group("num"))
    if base is None:
        return None
    value = base
    suffix = (m.group("suffix") or "").lower()
    if suffix:
        value *= _SUFFIX[suffix]
    if m.group("cur"):
        kind = "currency"
    elif m.group("pct"):
        kind = "percent"
    else:
        kind = "number"
    return value, kind


def _is_structural(value: float, kind: str, before: str, after: str, line_before: str) -> bool:
    """True for report scaffolding — ranking counts, horizons, enumerations,
    ordinals, calendar years — which are not data claims, so they're not scored.

    Decided by CONTEXT, not magnitude: currency/percent are never structural,
    and a plain number is structural only when the surrounding words mark it as
    scaffolding. Everything else is a data claim regardless of size.
    """
    if kind != "number":
        return False
    if value == int(value) and 1900 <= value <= 2100:
        return True  # calendar year
    if _STRUCTURAL_BEFORE.search(before):
        return True  # "top 5", "section 3", "Q2"
    if _TIME_UNIT_AFTER.match(after):
        return True  # "30 days", "7d", "3 months"
    if _ENUM_NOUN_AFTER.match(after):
        return True  # "3 recommendations", "2 key risks"
    # List ordinal at the start of a line: "1. …", "2) …".
    if value == int(value) and 0 <= value <= 99 and line_before.strip() == "" and _ORDINAL_AFTER.match(after):
        return True
    return False


def _iter_material(text: str):
    """Yield ``(value, kind, raw, line_before)`` for every MATERIAL numeric claim
    in *text* — the single materiality gate shared by extraction and label-
    binding, so both always agree on what counts as a data claim.

    ``line_before`` is the text of the current line up to (not across) the
    number, so context is judged within a line and never bleeds across one.
    """
    text = text or ""
    for m in _NUM_RE.finditer(text):
        vk = _value_kind(m)
        if vk is None:
            continue
        value, kind = vk
        # Anchor on the digit itself: _NUM_RE's leading ``\s*`` can absorb a
        # newline, so m.start() may sit on the previous line — m.start("num")
        # is always the first digit.
        start, end = m.start("num"), m.end()
        line_before = text[text.rfind("\n", 0, start) + 1: start]
        before = line_before[-24:]
        after = text[end: end + 24]
        if _is_structural(value, kind, before, after, line_before):
            continue
        yield value, kind, m.group(0).strip(), line_before


# Public name for the materiality gate: other verifiers (e.g. the insights
# citation checker) must decide "is this a data claim?" exactly the same way, or
# two checkers would disagree about what needs verifying.
iter_material_numbers = _iter_material


def extract_claims(text: str) -> list[NumericClaim]:
    """Extract the material numeric figures asserted in *text* (deduplicated)."""
    claims: dict[tuple[str, float], NumericClaim] = {}
    for value, kind, raw, _lb in _iter_material(text):
        key = (kind, round(value, 4))
        if key not in claims:
            claims[key] = NumericClaim(raw=raw, value=value, kind=kind)
    return list(claims.values())


# ── Semantic (label-bound) verification ─────────────────────────────────────

# How many characters before a number to search for a naming metric label —
# enough to catch "average revenue per customer is $X" without spilling into a
# neighbouring clause.
_LABEL_WINDOW = 48


def _percent_aware_match(value: float, expected: float) -> bool:
    """_matches, tolerant of a percent stored as either 23.4 or 0.234."""
    return _matches(value, expected) or _matches(value, expected * 100.0)


def _satisfy(value: float, expected: float | list[float]) -> tuple[bool, float | None]:
    """Does *value* match the binding? For an entity bound to a LIST of its own
    legitimate values (a segment's count/revenue/%, a product's revenue), any
    one of them counts — and the specific matched value is returned for the
    trace. For a scalar headline metric it's an exact-metric match."""
    candidates = expected if isinstance(expected, list) else [expected]
    for e in candidates:
        if _percent_aware_match(value, e):
            return True, e
    return False, None


def _label_verdicts(
    text: str, labeled: dict[str, float | list[float]]
) -> dict[tuple[str, float], tuple[bool, float | None]]:
    """For each figure that sits next to a *named* metric or entity, decide
    whether it matches THAT name's value(s).

    Returns ``{(kind, value): (satisfied, matched_value)}``. A figure the report
    explicitly calls "average order value" must equal the real AOV; a figure
    attributed to the "Champions" segment must be one of Champions' own numbers.
    A right-magnitude/wrong-metric misattribution (e.g. quoting total revenue as
    the AOV, or Espresso's sales as Latte's) is reported as violated, not
    quietly verified against the unrelated leaf.
    """
    # Longest labels first so "revenue per customer" wins over "revenue", and a
    # specific entity name wins over a generic metric word.
    labels = sorted(labeled.items(), key=lambda kv: -len(kv[0]))
    verdicts: dict[tuple[str, float], tuple[bool, float | None]] = {}

    for value, kind, _raw, line_before in _iter_material(text):
        # Search only the current line, immediately before the number.
        window = line_before.lower()[-_LABEL_WINDOW:]
        for label, expected in labels:
            lab = label.lower()
            # Word-bounded so "orders" doesn't fire inside "reorders".
            occ = list(re.finditer(rf"(?<![a-z]){re.escape(lab)}(?![a-z])", window))
            if not occ:
                continue
            # The label must belong to THIS number: no other number may sit
            # between the label and it (otherwise "total revenue is $500k across
            # 6,600 orders" would bind 6,600 to revenue).
            if any(ch.isdigit() for ch in window[occ[-1].end():]):
                continue
            satisfied, matched = _satisfy(value, expected)
            key = (kind, round(value, 4))
            prev = verdicts.get(key)
            # A satisfied binding anywhere wins; otherwise keep it flagged.
            if prev is None or (satisfied and not prev[0]):
                verdicts[key] = (satisfied, matched)
            break  # first (longest) matching label owns this occurrence

    return verdicts


def _match_source(claim: NumericClaim, known: list[float]) -> float | None:
    """Return the exact source figure *claim* matches, or ``None`` if unmatched.

    Returning the matched value (rather than a bare bool) is what powers the
    per-figure trace: every verified number can name the source it came from.
    """
    v = claim.value
    if claim.kind == "percent":
        # Payload may store a percent either as 23.4 or as the fraction 0.234.
        for k in known:
            if _matches(v, k) or _matches(v, k * 100.0):
                return k
        return None
    for k in known:
        if _matches(v, k):
            return k
    return None


def check_grounding(
    report_text: str, *payloads: object,
    labeled: dict[str, float | list[float]] | None = None,
) -> GroundingResult:
    """
    Verify a report's figures against one or more deterministic payloads.

    Parameters
    ----------
    report_text : str
        The generated report (markdown / plain text).
    *payloads : object
        Any number of analytics payloads (dicts, lists, scalars).  Their
        numeric leaves are unioned into the reference set.
    labeled : dict[str, float], optional
        Metric-name → authoritative-value bindings (e.g.
        ``{"average order value": 41.17, "total revenue": 128450.75}``). When a
        figure in the prose is written next to one of these names, it must equal
        THAT metric's value — a figure that instead matches an unrelated payload
        number (right magnitude, wrong metric) is reported as unverified rather
        than passing on the coincidental leaf. Figures not next to a named
        metric fall back to the standard any-leaf check, so this only ever
        *catches* misattribution — it never loosens verification.

    Returns
    -------
    GroundingResult
    """
    known: list[float] = []
    for p in payloads:
        if p is not None:
            known.extend(collect_known_values(p))

    verdicts = _label_verdicts(report_text, labeled) if labeled else {}

    result = GroundingResult()
    for claim in extract_claims(report_text):
        bound = verdicts.get((claim.kind, round(claim.value, 4)))
        if bound is not None:
            satisfied, matched = bound
            claim.verified = satisfied
            claim.matched_value = matched
            claim.basis = "semantic" if satisfied else "unverified"
        else:
            match = _match_source(claim, known) if known else None
            claim.verified = match is not None
            claim.matched_value = match
            claim.basis = "magnitude" if match is not None else "unverified"
        result.claims.append(claim)
    return result
