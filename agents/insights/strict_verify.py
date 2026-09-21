"""
Strict verification for citation-based reports.

The registry (:mod:`agents.insights.figures`) prevents fabrication for every
number the model *cites*.  This module closes the other half: it catches every
number the model **typed as digits** instead of citing, and checks it against
the registry — a closed allow-list of ~100 curated figures — rather than
against every numeric leaf in a nested analytics payload.

That difference is the whole point.  The general grounding checker matches a
figure against thousands of payload leaves, so a wrong number has thousands of
chances to coincidentally "verify".  Here a typed number must equal a figure
that a human decided was meaningful, in one of its legitimate rounded forms,
or it is reported as unverified — with the sentence it appeared in, so the
correction pass can fix that exact claim instead of rewriting blind.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from math import floor, isfinite, log10

from agents.reporting.grounding import iter_material_numbers

from .figures import LEFTOVER_MARKUP_RE, TOKEN_RE, FigureRegistry, normalise_tokens

# A currency symbol is a claim too: the dataset records amounts, never which
# currency they are in, so "$640,675.46" asserts something the data cannot
# support. Flagged whenever no currency column was found.
_CURRENCY_RE = re.compile(r"[$€£¥]\s*(?=[\d{])")

# Dates are not data claims, but "2025-05-31" contains three number-shaped
# tokens ("2025", "-05", "-31") that would otherwise be scored as figures. They
# are masked before extraction rather than special-cased afterwards.
_DATE_PATTERNS = [
    re.compile(r"\b\d{4}-\d{1,2}-\d{1,2}\b"),                    # 2025-05-31
    re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"),                  # 31/05/2025
    re.compile(r"\b\d{1,2}\s*[-–]\s*\d{1,2}\s+\w+\s+\d{4}\b"),   # 1-15 May 2025
    re.compile(
        r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{4}\b",
        re.IGNORECASE,
    ),                                                            # May 2025
    re.compile(r"\bQ[1-4]\s*'?\d{2,4}\b", re.IGNORECASE),        # Q2 2025
]

# How far back to quote for the correction prompt so the model can locate the
# offending claim without being handed the whole paragraph.
_CONTEXT_CHARS = 110

# ── "What is this worth?" attribution ───────────────────────────────────────
# Citation tokens guarantee a number is real; they do not guarantee it is the
# RIGHT number for the sentence. The one place that distinction has teeth is a
# recommendation's value: quoting a period's total revenue as the prize for
# fixing a decline is a real number making a false claim.
#
# So the value line is checked structurally: it must cite a figure that measures
# a change, an effect, a leak or a sized scenario — never a level.
_WORTH_LINE_RE = re.compile(
    r"^[ \t]*[-*+][ \t]*\*\*\s*what\s+it'?s?\s+worth\s*:?\s*\*\*(?P<body>.*)$",
    re.IGNORECASE | re.MULTILINE,
)
_VALUE_OF_ACTION_EXACT = {
    "discount_given", "margin_drag_revenue", "top_product_revenue",
    "gross_profit", "repeat_revenue", "one_time_revenue",
}


# ── Bare-referent citations ─────────────────────────────────────────────────
# A number is only unambiguous when the sentence says what it IS. Models reach
# for filler that drops the noun — "as measured by {{mon_revenue}}", "a positive
# change in {{gross_profit}}" — and the published sentence then contains a
# correct figure attached to nothing. These openings are always that mistake.
_BARE_REFERENT_RE = re.compile(
    r"(?P<phrase>"
    r"as\s+(?:measured|indicated|shown|evidenced|reflected|represented)\s+by|"
    r"(?:positive|negative)\s+change\s+in|"
    r"based\s+on\s+(?:the\s+)?(?:analysis|figure|value)\s+of|"
    r"as\s+(?:per|of)\s+the\s+value"
    r")\s*\{\{\s*(?P<key>[a-zA-Z0-9_]+)\s*\}\}",
    re.IGNORECASE,
)


# ── Sign agreement ──────────────────────────────────────────────────────────
# Change figures carry their own sign, so "revenue fell by {{mon_revenue_change}}"
# renders as "fell by -6,656.10" — a double negative that literally states a
# rise. The registry publishes a sign-free companion for exactly this sentence;
# this check makes using it non-optional.
_CHANGE_VERB_RE = re.compile(
    r"(?P<verb>"
    r"(?:fell|dropped|declined|decreased|shrank|contracted|slipped|lost|"
    r"rose|grew|increased|gained|climbed|improved|expanded)\s+(?:by\s+)?|"
    r"(?:a\s+|an\s+)?(?:decline|drop|decrease|fall|reduction|loss|increase|gain|rise|growth)"
    r"\s+of\s+"
    r")\{\{\s*(?P<key>[a-zA-Z0-9_]+)\s*\}\}",
    re.IGNORECASE,
)


def is_value_of_action_key(key: str) -> bool:
    """Does this figure measure something an action could move?

    Deltas (``*_change``), decomposition components (``*_effect``), sized
    scenarios (``scn_*``) and named leaks qualify. A stock level — revenue in a
    period, the order count — does not: nothing is "worth" a level.
    """
    return (
        key.startswith("scn_")
        or key.endswith("_change")
        or "_effect" in key
        or key.endswith("_loss")
        or key in _VALUE_OF_ACTION_EXACT
    )


@dataclass
class TypedNumber:
    """A number the model wrote as digits rather than as a citation token."""

    raw: str
    value: float
    kind: str  # currency | percent | number
    verified: bool
    figure_key: str | None = None
    basis: str = "unverified"  # registry_exact | registry_rounded | unverified
    context: str = ""


@dataclass
class StrictResult:
    """Verdict on one generated report."""

    cited_keys: list[str] = field(default_factory=list)
    typed: list[TypedNumber] = field(default_factory=list)
    unknown_tokens: list[str] = field(default_factory=list)
    # Currency symbols written against figures whose currency is unknown.
    currency_claims: list[str] = field(default_factory=list)
    # Recommendation value lines that cite a level instead of a change/scenario.
    misvalued_actions: list[str] = field(default_factory=list)
    # Figures written without saying what they are.
    ambiguous_citations: list[str] = field(default_factory=list)

    @property
    def typed_total(self) -> int:
        return len(self.typed)

    @property
    def unverified(self) -> list[TypedNumber]:
        return [t for t in self.typed if not t.verified]

    @property
    def cited_count(self) -> int:
        return len(self.cited_keys)

    @property
    def is_clean(self) -> bool:
        """Nothing in the report is unaccounted for."""
        return not (
            self.unverified or self.unknown_tokens or self.currency_claims
            or self.misvalued_actions or self.ambiguous_citations
        )

    @property
    def citation_rate(self) -> float:
        """Share of the report's numbers that came from the registry verbatim
        (as opposed to being typed by the model and checked afterwards)."""
        total = self.cited_count + self.typed_total
        return 1.0 if total == 0 else self.cited_count / total

    @property
    def status(self) -> str:
        return "verified" if self.is_clean else "unverified"

    @property
    def label(self) -> str:
        total = self.cited_count + self.typed_total
        if total == 0:
            return "This report states no figures."
        if self.is_clean:
            return (
                f"All {total} figures in this report are computed values from your data "
                f"({self.cited_count} inserted directly from the calculation engine)."
            )
        problems = len(self.unverified) + len(self.unknown_tokens)
        attribution = (
            len(self.currency_claims) + len(self.misvalued_actions) + len(self.ambiguous_citations)
        )
        if not problems and attribution:
            return (
                f"All {total} figures trace to your data, but {attribution} statement(s) "
                "attach a figure to a claim it does not support."
            )
        return (
            f"{total - problems} of {total} figures verified against the computed values; "
            f"{problems} could not be traced."
        )

    def certificate(self) -> dict:
        """Machine-checkable verdict attached to the report output."""
        return {
            "status": self.status,
            "figures_cited_from_engine": self.cited_count,
            "figures_typed_by_model": self.typed_total,
            "typed_and_verified": sum(1 for t in self.typed if t.verified),
            "citation_rate": round(self.citation_rate, 3),
            "unverified_figures": [
                {"figure": t.raw, "value": t.value, "context": t.context} for t in self.unverified
            ],
            "unknown_citations": self.unknown_tokens,
            "unsupported_currency_claims": self.currency_claims,
            "misvalued_actions": self.misvalued_actions,
            "ambiguous_citations": self.ambiguous_citations,
            "cited_keys": self.cited_keys,
        }


def mask_non_claims(text: str) -> str:
    """Blank out dates and citation tokens so only real typed figures remain.

    Replacement preserves length (spaces), which keeps the surrounding-context
    windows that decide materiality aligned with the original text.
    """
    out = text or ""
    for pattern in _DATE_PATTERNS:
        out = pattern.sub(lambda m: " " * len(m.group(0)), out)
    out = TOKEN_RE.sub(lambda m: " " * len(m.group(0)), out)
    return out


def _sig_round(x: float, sig: int) -> float:
    if not isfinite(x) or x == 0:
        return 0.0
    return round(x, -int(floor(log10(abs(x)))) + (sig - 1))


def _acceptable_forms(value: float) -> set[float]:
    """Every form a report may legitimately print a registered figure in.

    Deliberately a *set of exact roundings* rather than a tolerance band: an
    honest report writes 640,675.46 as "640,675" or "641,000", never as
    "639,000".  A tolerance band would accept the last one too.

    The absolute value is included because prose carries the sign in words —
    "revenue fell by 7,540.26" is a correct statement of −7,540.26.
    """
    forms: set[float] = set()
    for v in {value, abs(value)}:
        forms.add(round(v, 4))
        for nd in (0, 1, 2):
            forms.add(round(v, nd))
        for sig in (2, 3, 4):
            forms.add(round(_sig_round(v, sig), 4))
    return forms


def _build_index(registry: FigureRegistry) -> dict[float, str]:
    """value-form → figure key. Earlier figures win, so the headline metric
    owns a value it shares with a derived one."""
    index: dict[float, str] = {}
    for fig in registry.numeric():
        for form in _acceptable_forms(fig.value):
            index.setdefault(form, fig.key)
    return index


def check_action_values(raw_report: str, registry: FigureRegistry) -> list[str]:
    """Find recommendation value lines that cite a level instead of a change.

    Returns one short description per offending line, quoting it, so the
    correction pass can fix that bullet without touching the rest.
    """
    problems: list[str] = []
    for match in _WORTH_LINE_RE.finditer(raw_report or ""):
        body = match.group("body")
        keys = [m.group(1) for m in TOKEN_RE.finditer(body) if m.group(1) in registry]
        snippet = " ".join(body.split())[:110]
        if not keys:
            problems.append(f'no figure cited — "…{snippet}"')
            continue
        if not any(is_value_of_action_key(k) for k in keys):
            cited = ", ".join(
                f"{{{{{k}}}}} ({registry.get(k).label})" for k in keys if registry.get(k)
            )
            problems.append(
                f"cites {cited}, which is a level rather than what the action is worth — "
                f'in "…{snippet}"'
            )
    return problems


def check_bare_referents(raw_report: str, registry: FigureRegistry) -> list[str]:
    """Find figures cited without saying what they are.

    "revenue recovers past {{mon_prior_revenue}}" is a claim; "as measured by
    {{mon_prior_revenue}}" is the same correct number saying nothing. The second
    is what leaves a reader unsure what they are looking at, so it is treated as
    a defect rather than a style preference.
    """
    problems: list[str] = []
    for match in _BARE_REFERENT_RE.finditer(raw_report or ""):
        fig = registry.get(match.group("key"))
        name = fig.label if fig else match.group("key")
        problems.append(f'"{match.group("phrase").strip()} {{{{{match.group("key")}}}}}" — name the metric ({name})')
    return problems


def check_sign_agreement(raw_report: str, registry: FigureRegistry) -> list[str]:
    """Find change verbs applied to a figure that already carries a minus sign.

    "revenue fell by −6,656.10" is a correct number inside a sentence that says
    the opposite of what is true — the worst kind of error a verified report can
    ship, because every audit trail behind it checks out.
    """
    problems: list[str] = []
    for match in _CHANGE_VERB_RE.finditer(raw_report or ""):
        key = match.group("key")
        fig = registry.get(key)
        if fig is None or fig.unit == "text" or fig.value >= 0:
            continue
        verb = " ".join(match.group("verb").split())
        alternative = next(
            (
                cand for cand in (
                    key.replace("_change", "_decline"),
                    key.replace("_change", "_loss"),
                    key.replace("_change", "_drop"),
                )
                if cand in registry
            ),
            None,
        )
        hint = f" — use {{{{{alternative}}}}} (same size, no sign)" if alternative else ""
        problems.append(
            f'"{verb} {{{{{key}}}}}" states a rise: that figure is negative, so the sentence '
            f"double-negates{hint}"
        )
    return problems


def verify_typed_numbers(
    raw_report: str,
    registry: FigureRegistry,
    *,
    currency_known: bool = True,
    check_action_worth: bool = False,
) -> StrictResult:
    """Check a report *before* rendering: which numbers did the model type?

    Must run on the pre-render text — after substitution every cited figure is
    a literal number too, and the distinction between "the engine wrote this"
    and "the model wrote this" would be lost.
    """
    raw_report = normalise_tokens(raw_report)
    result = StrictResult(cited_keys=registry.used_keys(raw_report))
    result.unknown_tokens = [
        m.group(1) for m in TOKEN_RE.finditer(raw_report) if m.group(1) not in registry
    ]
    if not currency_known:
        result.currency_claims = sorted({m.group(0).strip() for m in _CURRENCY_RE.finditer(raw_report or "")})
    if check_action_worth:
        result.misvalued_actions = check_action_values(raw_report, registry)
    result.ambiguous_citations = (
        check_bare_referents(raw_report, registry)
        + check_sign_agreement(raw_report, registry)
    )

    index = _build_index(registry)
    masked = mask_non_claims(raw_report)
    seen: set[tuple[str, float]] = set()
    for value, kind, raw, _line_before in iter_material_numbers(masked):
        key = (kind, round(value, 4))
        if key in seen:
            continue
        seen.add(key)

        figure_key = None
        basis = "unverified"
        for form in _acceptable_forms(value):
            if form in index:
                figure_key = index[form]
                fig = registry.get(figure_key)
                basis = (
                    "registry_exact"
                    if fig is not None and round(abs(fig.value), 2) == round(abs(value), 2)
                    else "registry_rounded"
                )
                break

        context = ""
        if figure_key is None:
            pos = (raw_report or "").find(raw)
            if pos >= 0:
                start = max(0, pos - _CONTEXT_CHARS // 2)
                context = " ".join((raw_report[start: pos + _CONTEXT_CHARS // 2]).split())
        result.typed.append(
            TypedNumber(
                raw=raw, value=value, kind=kind, verified=figure_key is not None,
                figure_key=figure_key, basis=basis, context=context,
            )
        )
    return result


def render_and_verify(
    raw_report: str,
    registry: FigureRegistry,
    *,
    currency_known: bool = True,
    check_action_worth: bool = False,
) -> tuple[str, StrictResult]:
    """Verify the model's draft, then substitute the exact figures into it.

    Verification happens first (on the draft) and substitution second, so the
    returned text contains only engine-authored numbers plus whatever typed
    numbers passed the registry check.
    """
    result = verify_typed_numbers(
        raw_report, registry,
        currency_known=currency_known, check_action_worth=check_action_worth,
    )
    rendered, unknown = registry.render(raw_report)
    # ``render`` re-derives the unknown list; keep the two in agreement.
    result.unknown_tokens = list(dict.fromkeys(unknown))
    # Last line of defence: no citation markup may reach a published report.
    # A malformed token that neither substitutes nor registers as unknown would
    # otherwise print literal braces into a business document.
    leftovers = {m.group(0) for m in LEFTOVER_MARKUP_RE.finditer(rendered)}
    if leftovers:
        result.unknown_tokens.extend(sorted(leftovers))
    return rendered, result


def correction_brief(result: StrictResult) -> str:
    """The precise, itemised instruction for a correction pass.

    Naming each offending figure and the sentence it sits in makes the retry a
    targeted fix rather than a full re-roll of the report.
    """
    lines: list[str] = []
    if result.unverified:
        lines.append(
            "These numbers were typed directly and do NOT match any computed figure. "
            "Replace each one with the correct {{citation_token}} from the allowed list, "
            "or delete the claim if no figure supports it:"
        )
        for t in result.unverified:
            ctx = f' — in: "…{t.context}…"' if t.context else ""
            lines.append(f'  - "{t.raw}"{ctx}')
    if result.unknown_tokens:
        lines.append(
            "These citation tokens do not exist in the allowed list. Use only tokens from "
            "the list, or remove the sentence:"
        )
        for k in result.unknown_tokens:
            lines.append(f"  - {{{{{k}}}}}")
    if result.currency_claims:
        lines.append(
            "Remove every currency symbol (" + ", ".join(result.currency_claims) + "). "
            "The dataset does not record a currency, so naming one is a claim you cannot "
            "support. Write the bare figure."
        )
    if result.misvalued_actions:
        lines.append(
            "These 'What it's worth' lines attach the wrong kind of figure. What an action "
            "is worth is a CHANGE, a LEAK or a sized scenario — a token starting `scn_`, "
            "ending `_change`, or containing `_effect` — never a period total or a level. "
            "Fix each one:"
        )
        for problem in result.misvalued_actions:
            lines.append(f"  - {problem}")
    if result.ambiguous_citations:
        lines.append(
            "These sentences attach a figure in a way the reader cannot parse — either the "
            "metric is not named in the clause, or a change verb is applied to a figure that "
            "already carries a minus sign. Rewrite each one:"
        )
        for problem in result.ambiguous_citations:
            lines.append(f"  - {problem}")
    return "\n".join(lines)
