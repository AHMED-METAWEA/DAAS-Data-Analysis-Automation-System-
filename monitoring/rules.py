"""What counts as worth waking someone up for.

The ranking problem here is the same one the insights report already solved, so
it uses the same engine.  :mod:`agents.insights.evidence` turns an analytics
payload into findings that each carry an explicit **money at stake**, ordered so
that a EGP 40,000 margin leak outranks a weekday curiosity — deterministically,
in code, with the editorial judgement written down as named thresholds rather
than left to a model's sense of drama.  Alerts inherit that ordering directly.

Rebuilding a second, alert-specific notion of importance would have been the
obvious thing to do and the wrong one: the platform would then have two
different answers to "what matters most in this business", and a user who saw
one on the Insights page and the other on WhatsApp would be right to trust
neither.

On top of the evidence engine sit the rules that only make sense over *time*,
and which a single report cannot express:

* **Snapshot deltas** — how the headline metrics moved since the last recorded
  run, measured against what was recorded then, not against a recomputation.
* **Targets** — thresholds the owner set, in their own words.
* **Freshness** — the data itself has stopped arriving. Operationally the most
  urgent alert there is, and the one a purely analytical system never raises:
  stale data produces a perfectly calm report.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from agents.insights.evidence import EvidenceItem
from agents.insights.figures import TOKEN_RE, FigureRegistry
from monitoring.evidence_ar import arabic_for
from tools.localize import format_timestamp

# ── Severity ────────────────────────────────────────────────────────────────

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

# Money at stake as a share of period revenue, and what that share means. These
# are the alerting equivalent of evidence.py's named thresholds: the judgement
# is arguable, so it is visible rather than buried in a comparison.
_CRITICAL_SHARE = 0.20
_HIGH_SHARE = 0.08
_MEDIUM_SHARE = 0.03
_LOW_SHARE = 0.01

# An evidence tag can never produce an alert *less* serious than this.
_TAG_FLOOR = {
    "THREAT": "medium",
    "LEAK": "medium",
    "RISK": "low",
    "OPPORTUNITY": "low",
    "STRENGTH": "info",
    "CAVEAT": "info",
}
# …and never *more* serious than this.
#
# The ceiling on RISK is the important one, and it encodes a distinction the
# money-at-stake number alone cannot make. A THREAT or a LEAK is an event: money
# moved, or is moving. A RISK is a standing structural exposure — "your top 10%
# of customers are half your revenue" — and its money at stake is the size of
# the *exposure*, not of a loss. Ranked by share alone, that exposure is 50% of
# revenue and scores CRITICAL, so the owner would be told their business is in
# crisis every single morning, about a fact that has not changed since they
# started. Structural exposures belong in the briefing body; they are not worth
# interrupting someone for. Opportunities are capped for the same reason.
_TAG_CEILING = {
    "RISK": "medium",
    "OPPORTUNITY": "low",
    "STRENGTH": "info",
    "CAVEAT": "info",
}

# Days without new data before it is worth saying so. One day is normal for a
# nightly export; three means something is broken.
_STALE_WARN_DAYS = 3
_STALE_CRITICAL_DAYS = 7

# Wording for the rules this module owns (the evidence engine's findings are
# worded in monitoring/evidence_ar.py). Kept as format templates rather than
# f-strings so both languages are visibly the same sentence with the same
# substitutions, and a missing Arabic key falls back to English by lookup.
_RULE_TEXT: dict[str, dict[str, str]] = {
    "en": {
        "delta_title": "{label} {direction} {pct:.1f}% since the last check",
        "delta_fell": "fell", "delta_rose": "rose",
        "delta_body": (
            "{label} is {current:,.2f}, against {prior:,.2f} recorded at {since} — a change "
            "of {delta:,.2f} ({pct:+.1f}%). Measured against the stored figure from that run, "
            "not against a recomputation, so a re-clean of the data cannot manufacture this."
        ),
        "since_fallback": "the last check",
        "target_title": "{label} is {word} your target",
        "target_below": "below", "target_above": "above",
        "target_body": (
            "{label} is {value:,.2f}, {word} the target of {limit:,.2f} you set — a gap of "
            "{gap:,.2f}. This is your own threshold, not a platform default."
        ),
        "stale_title": "No new data for {days} days",
        "stale_body": (
            "The most recent transaction in this project is dated {last_date}, {days} days "
            "ago. Every figure in today's briefing describes a period that has already "
            "ended — check the import, the sync or the source system before reading anything "
            "else here as a business signal."
        ),
        "missing_title": "{pct:.1f}% of cells are empty",
        "missing_body": (
            "{pct:.1f}% of all cells in this dataset are empty. Metrics built on the affected "
            "columns are computed over fewer rows than the headline row count implies — treat "
            "the totals as lower bounds."
        ),
        "duplicate_title": "{count:,} fully duplicated rows",
        "duplicate_body": (
            "{count:,} rows ({pct:.1f}%) are identical to another row across every column. "
            "They are included in every total, so revenue and order counts are inflated by "
            "that amount unless they are genuine repeat transactions."
        ),
        "rule_error_title": "A monitoring rule failed: {rule}",
    },
    "ar": {
        "delta_title": "{label} {direction} بنسبة {pct:.1f}% منذ آخر فحص",
        "delta_fell": "انخفض", "delta_rose": "ارتفع",
        "delta_body": (
            "{label} الآن {current:,.2f}، مقابل {prior:,.2f} مُسجَّلة في {since} — أي تغيّر "
            "قدره {delta:,.2f} ({pct:+.1f}%). المقارنة تتم مع الرقم المحفوظ من ذلك الفحص لا "
            "مع إعادة احتساب، فلا يمكن لإعادة تنظيف البيانات أن تصنع هذا الفارق."
        ),
        "since_fallback": "آخر فحص",
        "target_title": "{label} {word} الهدف الذي حدّدته",
        "target_below": "دون", "target_above": "فوق",
        "target_body": (
            "{label} يساوي {value:,.2f}، وهو {word} الهدف البالغ {limit:,.2f} الذي حدّدته — "
            "بفارق {gap:,.2f}. هذا حدٌّ وضعته أنت، وليس إعدادًا افتراضيًا للمنصّة."
        ),
        "stale_title": "لا توجد بيانات جديدة منذ {days} يومًا",
        "stale_body": (
            "أحدث معاملة في هذا المشروع مؤرَّخة في {last_date}، أي منذ {days} يومًا. كل رقم "
            "في ملخّص اليوم يصف فترة انتهت بالفعل — راجع الاستيراد أو المزامنة أو النظام "
            "المصدر قبل قراءة أي شيء هنا كمؤشر على أداء النشاط."
        ),
        "missing_title": "{pct:.1f}% من الخلايا فارغة",
        "missing_body": (
            "{pct:.1f}% من خلايا هذه البيانات فارغة. المؤشرات المبنية على الأعمدة المتأثرة "
            "محسوبة على صفوف أقل مما يوحي به إجمالي عدد الصفوف — تعامل مع الإجماليات "
            "كحدٍّ أدنى."
        ),
        "duplicate_title": "{count:,} صف مكرّر بالكامل",
        "duplicate_body": (
            "{count:,} صف ({pct:.1f}%) مطابق لصف آخر في كل الأعمدة. وهي مُدرَجة في كل "
            "الإجماليات، فالإيراد وعدد الطلبات مُضخَّمان بهذا القدر ما لم تكن معاملات "
            "متكرّرة حقيقية."
        ),
        "rule_error_title": "فشلت إحدى قواعد المراقبة: {rule}",
    },
}


def _rt(language: str, key: str, **kwargs: Any) -> str:
    table = _RULE_TEXT.get(language) or _RULE_TEXT["en"]
    template = table.get(key) or _RULE_TEXT["en"].get(key, "")
    return template.format(**kwargs)


DEFAULT_RULES: dict[str, Any] = {
    "evidence": True,
    "snapshot_delta": True,
    "targets": {},
    "freshness": True,
    "quality": True,
    # Minimum period-over-period move, in percent, before a snapshot delta is
    # an alert rather than ordinary variation.
    "snapshot_delta_pct": 10.0,
}


def _worse(a: str, b: str) -> str:
    return a if SEVERITY_ORDER.get(a, 9) <= SEVERITY_ORDER.get(b, 9) else b


def _better(a: str, b: str) -> str:
    return a if SEVERITY_ORDER.get(a, 9) >= SEVERITY_ORDER.get(b, 9) else b


def severity_from_share(money: float, revenue: float) -> str:
    """Money at stake, expressed as how loudly it should be said.

    Scaled by the size of the business on purpose: 40,000 is an emergency for a
    corner shop and a rounding error for a distributor, and a fixed threshold
    would be wrong for both.
    """
    if not revenue or revenue <= 0:
        return "medium" if money else "info"
    share = abs(money) / revenue
    if share >= _CRITICAL_SHARE:
        return "critical"
    if share >= _HIGH_SHARE:
        return "high"
    if share >= _MEDIUM_SHARE:
        return "medium"
    if share >= _LOW_SHARE:
        return "low"
    return "info"


def fingerprint(*parts: str) -> str:
    """Stable identity for a *finding*, not for an occurrence.

    Built from the rule and the thing it is about — never from a value — so the
    same standing problem produces the same fingerprint tomorrow and the
    cooldown recognises it. Fingerprinting the value instead would make every
    day's version look new, which is precisely the failure that trains people to
    mute a channel.
    """
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:32]


@dataclass
class Finding:
    """A candidate alert, before cooldown and severity filtering."""

    rule: str
    severity: str
    title: str
    body: str
    metric: str = ""
    money_at_stake: float = 0.0
    current_value: float | None = None
    prior_value: float | None = None
    change_pct: float | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    fingerprint_key: str = ""
    # False when ``money_at_stake`` is the size of a standing *exposure* rather
    # than money that moved. The briefing's headline total sums only events:
    # adding "half your revenue comes from ten customers" to "revenue fell
    # 6,656" produces a number that describes nothing.
    is_event: bool = True
    # Findings about whether the data is *real* sort above findings derived from
    # it, whatever the money involved. If the last transaction is 400 days old,
    # "revenue fell 60%" is a true statement about a period that ended long ago,
    # and leading with it sends the owner chasing a collapse that already
    # finished. 0 = operational (read this first), 1 = analytical.
    sort_priority: int = 1

    def as_alert_fields(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "title": self.title[:500],
            "body_md": self.body,
            "metric": self.metric[:64],
            "money_at_stake": float(self.money_at_stake or 0.0),
            "current_value": self.current_value,
            "prior_value": self.prior_value,
            "change_pct": self.change_pct,
            "evidence": self.evidence,
            "fingerprint": self.fingerprint_key,
        }


@dataclass
class RuleContext:
    """Everything the rules need, computed once by the runner."""

    project_id: str
    payload: dict[str, Any]
    decision: dict[str, Any]
    registry: FigureRegistry
    evidence: list[EvidenceItem]
    previous_metrics: dict[str, Any] = field(default_factory=dict)
    previous_captured_at: datetime | None = None
    config: dict[str, Any] = field(default_factory=dict)
    data_last_date: str | None = None
    language: str = "en"

    @property
    def revenue(self) -> float:
        return float((self.payload.get("kpi") or {}).get("revenue") or 0.0)

    @property
    def ar(self) -> bool:
        return self.language == "ar"

    def setting(self, key: str, default: Any = None) -> Any:
        merged = {**DEFAULT_RULES, **(self.config or {})}
        return merged.get(key, default)

    def renderable(self, template: str | None) -> bool:
        """Whether every citation token in *template* exists in this registry.

        An Arabic template written against the calendar-month tokens is useless
        for a dataset that only supports the rolling-30-day comparison — it
        would render as a row of "[figure unavailable]" markers. Checking first
        is what makes falling back to English safe rather than cosmetic.
        """
        if not template:
            return False
        return all(m.group(1) in self.registry for m in TOKEN_RE.finditer(template))


# ── Rule: the evidence engine ───────────────────────────────────────────────

def evidence_findings(ctx: RuleContext) -> list[Finding]:
    """Every ranked finding the insights engine produced, as alerts.

    The evidence text is written with ``{{citation}}`` tokens; rendering it
    through the registry here is what makes an alert's numbers engine-authored
    rather than model-authored, exactly as in the published report. The
    *unrendered* text is what the fingerprint is built from, so a leak of 39,900
    tomorrow is recognised as the same finding as 40,100 today.
    """
    if not ctx.setting("evidence", True):
        return []
    findings: list[Finding] = []
    for item in ctx.evidence:
        if item.tag in ("STRENGTH", "CAVEAT"):
            # Recorded in the briefing by the composer, never pushed as an alert.
            continue
        severity = severity_from_share(item.money_at_stake, ctx.revenue)
        severity = _worse(severity, _TAG_FLOOR.get(item.tag, "info"))
        ceiling = _TAG_CEILING.get(item.tag)
        if ceiling:
            severity = _better(severity, ceiling)

        body_template, title_template = _templates_for(ctx, item)
        rendered, _ = ctx.registry.render(body_template)
        if title_template:
            title, _ = ctx.registry.render(title_template)
        else:
            # The first sentence is the finding; the rest is the reasoning.
            title = rendered.split(". ")[0].strip().rstrip(".")
            title = f"{item.tag.title()}: {title}"

        findings.append(Finding(
            rule=f"evidence.{item.tag.lower()}",
            severity=severity,
            title=title,
            body=rendered,
            metric=item.section,
            money_at_stake=item.money_at_stake,
            is_event=item.is_event,
            evidence={
                "tag": item.tag,
                "key": item.key,
                "section": item.section,
                "template": item.text,
                "money_at_stake": round(item.money_at_stake, 2),
            },
            # Keyed on the finding's identity, never on its wording — so the
            # same leak is recognised tomorrow, in either language, at a
            # different value.
            fingerprint_key=fingerprint(
                "evidence", item.tag, item.section, item.key or item.text[:120],
            ),
        ))
    return findings


def _templates_for(ctx: RuleContext, item: EvidenceItem) -> tuple[str, str | None]:
    """``(body, title)`` templates for one finding, in the schedule's language.

    Arabic is rendered from its own sentence carrying the same citation tokens,
    not from a translation of the English one — see monitoring/evidence_ar.py.
    A key with no Arabic wording, or whose Arabic wording cites figures this
    dataset does not have, falls back to English: a sentence in the wrong
    language is obvious and harmless, a mistranslated financial claim is not.
    """
    # `reader_text` strips the instructions evidence.py writes for the model
    # ("USE THIS COMPARISON WINDOW THROUGHOUT THE REPORT…"), which are prompt
    # engineering, not something to push to someone's phone.
    english = item.reader_text()
    if not ctx.ar or not item.key:
        return english, None

    for key in (item.key, f"{item.key}_p30"):
        body = arabic_for(key)
        if ctx.renderable(body):
            title = arabic_for(key, title=True)
            return body, title if ctx.renderable(title) else None
    return english, None


# ── Rule: movement since the last recorded run ──────────────────────────────

_TRACKED_METRICS = (
    ("revenue", True),
    ("orders", False),
    ("total_customers", False),
    ("aov", True),
)

# Metric names in both languages. Shared by the snapshot-delta and target rules
# so a metric is never called two different things in one briefing.
_METRIC_LABELS: dict[str, dict[str, str]] = {
    "en": {
        "revenue": "Revenue",
        "orders": "Orders",
        "total_customers": "Active customers",
        "aov": "Average order value",
        "gross_margin_pct": "Gross margin %",
        "repeat_rate_pct": "Repeat purchase rate %",
    },
    "ar": {
        "revenue": "الإيراد",
        "orders": "عدد الطلبات",
        "total_customers": "العملاء النشطون",
        "aov": "متوسط قيمة الطلب",
        "gross_margin_pct": "نسبة الهامش الإجمالي",
        "repeat_rate_pct": "نسبة تكرار الشراء",
    },
}


def _metric_label(language: str, metric: str) -> str:
    table = _METRIC_LABELS.get(language) or _METRIC_LABELS["en"]
    return table.get(metric) or _METRIC_LABELS["en"].get(metric, metric)


def metric_label(language: str, metric: str) -> str:
    """Public alias — the briefing prints the same metric names the rules use."""
    return _metric_label(language, metric)


# What each tracked metric is measured *in*. Without this the briefing renders
# every figure through the money formatter and prints "EGP 31.40" for a gross
# margin percentage — a factual error in the one section whose entire purpose is
# to let a reader check the arithmetic.
_METRIC_UNITS: dict[str, str] = {
    "revenue": "money",
    "aov": "money",
    "orders": "count",
    "total_customers": "count",
    "gross_margin_pct": "percent",
    "repeat_rate_pct": "percent",
}


def metric_unit(metric: str) -> str:
    """``"money"`` | ``"percent"`` | ``"count"`` | ``"number"``.

    An unrecognised metric falls back to ``"number"``, never to ``"money"``:
    guessing a currency onto a figure that has none is the expensive direction
    to be wrong in, and a bare number is merely less informative.
    """
    if metric in _METRIC_UNITS:
        return _METRIC_UNITS[metric]
    return "percent" if metric.endswith("_pct") else "number"


def snapshot_delta_findings(ctx: RuleContext) -> list[Finding]:
    """How the headline metrics moved against the previous *recorded* run.

    Against the recording, not against a recomputation. A dataset that gets
    re-cleaned, backfilled or re-joined answers "what was revenue last week?"
    differently each time it is asked, and the difference surfaces as alerts
    nobody can reproduce.
    """
    if not ctx.setting("snapshot_delta", True) or not ctx.previous_metrics:
        return []
    threshold = float(ctx.setting("snapshot_delta_pct", 10.0))
    kpi = ctx.payload.get("kpi") or {}
    findings: list[Finding] = []

    for key, is_money in _TRACKED_METRICS:
        label = _metric_label(ctx.language, key)
        current = kpi.get(key)
        prior = ctx.previous_metrics.get(key)
        if current is None or prior in (None, 0):
            continue
        try:
            current_value, prior_value = float(current), float(prior)
        except (TypeError, ValueError):
            continue
        change_pct = (current_value - prior_value) / abs(prior_value) * 100
        if abs(change_pct) < threshold:
            continue

        # Only a *drop* in a money metric puts money at stake; a rise is good
        # news and must not be ranked as though it were a loss.
        money = abs(current_value - prior_value) if (is_money and change_pct < 0) else 0.0
        severity = severity_from_share(money, ctx.revenue) if money else "low"
        if change_pct > 0:
            severity = "info"
        since = (
            format_timestamp(ctx.previous_captured_at, ctx.language)
            if ctx.previous_captured_at else _rt(ctx.language, "since_fallback")
        )
        direction = _rt(ctx.language, "delta_fell" if change_pct < 0 else "delta_rose")
        findings.append(Finding(
            rule="snapshot_delta",
            severity=severity,
            title=_rt(
                ctx.language, "delta_title",
                label=label, direction=direction, pct=abs(change_pct),
            ),
            body=_rt(
                ctx.language, "delta_body",
                label=label, current=current_value, prior=prior_value, since=since,
                delta=current_value - prior_value, pct=change_pct,
            ),
            metric=key,
            money_at_stake=money,
            current_value=current_value,
            prior_value=prior_value,
            change_pct=change_pct,
            evidence={"basis": "metric snapshot comparison", "captured_at": since},
            fingerprint_key=fingerprint("snapshot_delta", key, "down" if change_pct < 0 else "up"),
        ))
    return findings


# ── Rule: the owner's own targets ───────────────────────────────────────────

def _target_value(ctx: RuleContext, metric: str) -> float | None:
    kpi = ctx.payload.get("kpi") or {}
    if metric in kpi and kpi[metric] is not None:
        try:
            return float(kpi[metric])
        except (TypeError, ValueError):
            return None
    sections = (ctx.decision or {}).get("sections") or {}
    if metric == "gross_margin_pct":
        margin = sections.get("margin") or {}
        return float(margin["gross_margin_pct"]) if margin.get("gross_margin_pct") is not None else None
    if metric == "repeat_rate_pct":
        repeat = sections.get("repeat") or {}
        return float(repeat["repeat_rate_pct"]) if repeat.get("repeat_rate_pct") is not None else None
    return None


def target_findings(ctx: RuleContext) -> list[Finding]:
    """Thresholds the owner set, checked against the current figures.

    A target is the one piece of business context the platform cannot infer.
    "Revenue below 50,000 this month is a problem" is a fact about the owner's
    rent and payroll, not about the data.
    """
    targets = ctx.setting("targets", {}) or {}
    findings: list[Finding] = []
    for metric, spec in targets.items():
        if not isinstance(spec, dict):
            continue
        value = _target_value(ctx, metric)
        if value is None:
            continue
        label = _metric_label(ctx.language, metric)
        for bound in ("min", "max"):
            limit = spec.get(bound)
            if limit is None:
                continue
            try:
                limit_value = float(limit)
            except (TypeError, ValueError):
                continue
            breached = value < limit_value if bound == "min" else value > limit_value
            if not breached:
                continue
            word = _rt(ctx.language, "target_below" if bound == "min" else "target_above")
            gap = abs(value - limit_value)
            is_money = metric in ("revenue", "aov")
            findings.append(Finding(
                rule="target_breach",
                severity=(
                    spec.get("severity")
                    or (severity_from_share(gap, ctx.revenue) if is_money else "high")
                ),
                title=_rt(ctx.language, "target_title", label=label, word=word),
                body=_rt(
                    ctx.language, "target_body",
                    label=label, value=value, word=word, limit=limit_value, gap=gap,
                ),
                metric=metric,
                money_at_stake=gap if is_money else 0.0,
                current_value=value,
                prior_value=limit_value,
                change_pct=(
                    (value - limit_value) / abs(limit_value) * 100 if limit_value else None
                ),
                evidence={"target": limit_value, "bound": bound, "source": "user-defined target"},
                fingerprint_key=fingerprint("target", metric, bound),
            ))
    return findings


# ── Rule: is the data even arriving? ────────────────────────────────────────

def freshness_findings(ctx: RuleContext) -> list[Finding]:
    """The data has stopped updating.

    Operationally the most urgent thing a monitor can say, and the one a purely
    analytical system never says: stale data produces a report that is calm,
    detailed and about last month.
    """
    if not ctx.setting("freshness", True) or not ctx.data_last_date:
        return []
    try:
        last = datetime.fromisoformat(str(ctx.data_last_date)).replace(tzinfo=UTC)
    except ValueError:
        return []
    days = (datetime.now(UTC) - last).days
    if days < _STALE_WARN_DAYS:
        return []
    severity = "critical" if days >= _STALE_CRITICAL_DAYS else "high"
    return [Finding(
        rule="data_freshness",
        severity=severity,
        title=_rt(ctx.language, "stale_title", days=days),
        body=_rt(ctx.language, "stale_body", days=days, last_date=ctx.data_last_date),
        metric="data_freshness",
        money_at_stake=0.0,
        sort_priority=0,
        current_value=float(days),
        evidence={"last_date": ctx.data_last_date, "days_stale": days},
        fingerprint_key=fingerprint("freshness", ctx.project_id),
    )]


# ── Rule: the data got worse ────────────────────────────────────────────────

def quality_findings(ctx: RuleContext) -> list[Finding]:
    """Data-quality regressions that change how every other figure reads."""
    if not ctx.setting("quality", True):
        return []
    findings: list[Finding] = []
    for check in ctx.payload.get("quality") or []:
        if check.get("severity") in (None, "low"):
            continue
        name = check.get("check", "quality")
        if name == "missing_values" and check.get("overall_pct"):
            pct = float(check["overall_pct"])
            findings.append(Finding(
                rule="data_quality",
                severity="medium",
                title=_rt(ctx.language, "missing_title", pct=pct),
                body=_rt(ctx.language, "missing_body", pct=pct),
                metric="missing_values",
                current_value=pct,
                is_event=False,
                evidence=check,
                fingerprint_key=fingerprint("quality", "missing_values"),
            ))
        if name == "duplicate_rows" and check.get("count"):
            count = int(check["count"])
            findings.append(Finding(
                rule="data_quality",
                severity="medium",
                title=_rt(ctx.language, "duplicate_title", count=count),
                body=_rt(
                    ctx.language, "duplicate_body",
                    count=count, pct=float(check.get("pct") or 0.0),
                ),
                metric="duplicate_rows",
                current_value=float(count),
                is_event=False,
                evidence=check,
                fingerprint_key=fingerprint("quality", "duplicate_rows"),
            ))
    return findings


# ── Orchestration ───────────────────────────────────────────────────────────

RULES = (
    freshness_findings,      # first: it changes how everything else should be read
    evidence_findings,
    snapshot_delta_findings,
    target_findings,
    quality_findings,
)


def evaluate(ctx: RuleContext) -> list[Finding]:
    """Run every rule and rank what they found.

    Three keys, in this order:

    1. **Operational before analytical.** "The data stopped arriving" outranks
       every finding derived from that data, whatever the money involved —
       because if it is true, the rest of the briefing describes a period that
       has already ended.
    2. **Severity**, which already accounts for the size of the business.
    3. **Money at stake**, breaking ties within a severity band.
    """
    findings: list[Finding] = []
    for rule in RULES:
        try:
            findings.extend(rule(ctx))
        except Exception as exc:  # pragma: no cover - one rule must not kill a run
            findings.append(Finding(
                rule="rule_error",
                severity="info",
                title=_rt(ctx.language, "rule_error_title", rule=rule.__name__),
                body=f"{type(exc).__name__}: {exc}",
                is_event=False,
                fingerprint_key=fingerprint("rule_error", rule.__name__),
            ))
    findings.sort(
        key=lambda f: (
            f.sort_priority,
            SEVERITY_ORDER.get(f.severity, 9),
            -abs(f.money_at_stake),
        )
    )
    return findings


def filter_findings(
    findings: list[Finding],
    *,
    min_severity: str = "medium",
    suppressed: set[str] | None = None,
    limit: int = 5,
) -> tuple[list[Finding], list[Finding]]:
    """Apply the severity floor, the cooldown and the per-run cap.

    Returns ``(delivered, suppressed_findings)``. The suppressed ones are
    returned rather than dropped so the run record can say *why* a quiet
    briefing was quiet — "nothing found" and "four findings, all still inside
    their cooldown" are different states and only one needs attention.
    """
    suppressed = suppressed or set()
    floor = SEVERITY_ORDER.get(min_severity, 2)
    kept: list[Finding] = []
    held: list[Finding] = []
    for finding in findings:
        if SEVERITY_ORDER.get(finding.severity, 9) > floor:
            held.append(finding)
            continue
        if finding.fingerprint_key in suppressed:
            held.append(finding)
            continue
        if len(kept) >= limit:
            held.append(finding)
            continue
        kept.append(finding)
    return kept, held
