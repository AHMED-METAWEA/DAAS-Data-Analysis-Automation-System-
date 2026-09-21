"""Composing the briefing that actually gets sent.

Three constraints shape everything here:

1. **It has to arrive.**  This text is pushed to WhatsApp at 07:00 whether or
   not an LLM provider is reachable, in credit, or having a good day.  So the
   briefing is assembled from templates over already-computed figures, and no
   model sits in the delivery path.  The root-cause narrative, when one exists,
   is included — but it was generated earlier, verified against the figure
   registry, and stored; if it is missing the briefing is shorter, never absent.

2. **It has to be read on a phone.**  A busy owner gives this three lines before
   deciding whether to open the link.  The lead is what changed and what it is
   worth; the detail sits below it, for whoever keeps reading.

3. **It has to work in Arabic.**  Not translated after the fact — composed in
   Arabic, with the sentences ordered the way Arabic business writing orders
   them.  A "localised" product that is really an English product with a
   translation layer is obvious to the people it is meant for.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from tools.localize import format_timestamp
from monitoring.rules import Finding, metric_label, metric_unit
from notifications.base import Message, markdown_to_text

_SEVERITY_LABEL = {
    "en": {
        "critical": "CRITICAL", "high": "HIGH", "medium": "MEDIUM",
        "low": "LOW", "info": "FYI",
    },
    "ar": {
        "critical": "حرِج", "high": "مرتفع", "medium": "متوسط",
        "low": "منخفض", "info": "للعلم",
    },
}

_SEVERITY_ICON = {
    "critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵", "info": "⚪",
}

_TEXT = {
    "en": {
        "title": "{project} — business briefing",
        "title_quiet": "{project} — nothing needs you today",
        "generated": "Generated {timestamp} · {rows:,} rows · data through {last_date}",
        "generated_nodate": "Generated {timestamp} · {rows:,} rows",
        "headline_one": "1 thing needs your attention.",
        "headline_many": "{count} things need your attention.",
        "at_stake": "Money at stake: {amount}.",
        "quiet": (
            "Everything checked out. No metric crossed a threshold, no target was missed, "
            "and the data is arriving on schedule."
        ),
        "quiet_suppressed": (
            "Nothing new. {count} finding(s) from earlier are still open but were reported "
            "recently, so they are not repeated here."
        ),
        "root_cause_heading": "Where it came from",
        "detail_heading": "Detail",
        "open": "Open the full analysis: {link}",
        "footer": "You are receiving this because you set up a scheduled check in DAAS.",
        "no_currency": (
            "Amounts are in the source data's own units — this dataset records no currency."
        ),
        "explains": (
            "{share} of the change comes from {slice}, which is only {rows} of all "
            "transaction lines."
        ),
        "rest_flat": "Everything else moved {rest}.",
        "not_robust": (
            "Treat this as a lead rather than a proven cause — it does not clear the "
            "significance bar once every slice tested is accounted for."
        ),
        # ── Report chrome ──────────────────────────────────────────────────
        "section_basis": "Basis of this report",
        "section_figures": "The figures",
        "section_detail": "Everything else flagged",
        "section_method": "How this report was produced",
        "basis_period": "Compared against the reading taken {when}.",
        "basis_records": "{rows:,} records analysed.",
        "basis_through": "Data covers everything up to {last_date}.",
        "basis_currency": "Amounts shown in {currency}.",
        "figure_line": "{metric}: {prior} → {current} ({change})",
        "figure_windows": "{current_label} ({current_rows:,} rows) vs {prior_label} ({prior_rows:,} rows).",
        # The assurance block. Every sentence here is a claim the system can
        # actually keep — see the module docstring.
        "assure_figures": (
            "Every number above is calculated directly from your own data. No figure in "
            "this report is written by a language model."
        ),
        "assure_verified": (
            "The written interpretation was checked against those same figures before "
            "this was sent. A draft citing a number your data does not support is "
            "rejected automatically."
        ),
        "assure_fallback": (
            "The model's draft did not pass that check, so it was discarded and the "
            "interpretation below was generated directly from the figures instead."
        ),
        "assure_robust": (
            "{robust} of {total} candidate explanations remain significant after "
            "correcting for the {tested} slices tested."
        ),
        "assure_inference": (
            "Figures are measured. Causes are inferred — they show where a change "
            "concentrated, which is evidence, not proof."
        ),
        "exposure_note": "{amount} exposed — a standing risk, not part of the total above",
        "summary_more": "Full report with figures and method: {link}",
    },
    "ar": {
        "title": "{project} — الملخص اليومي للأعمال",
        "title_quiet": "{project} — لا شيء يحتاج انتباهك اليوم",
        "generated": "أُنشئ في {timestamp} · {rows:,} صف · بيانات حتى {last_date}",
        "generated_nodate": "أُنشئ في {timestamp} · {rows:,} صف",
        "headline_one": "أمر واحد يحتاج انتباهك.",
        "headline_many": "{count} أمور تحتاج انتباهك.",
        "at_stake": "المبلغ المعرّض للخطر: {amount}.",
        "quiet": (
            "كل شيء على ما يُرام. لم يتجاوز أي مؤشر حدوده، ولم يُفَوَّت أي هدف، والبيانات "
            "تصل في موعدها."
        ),
        "quiet_suppressed": (
            "لا جديد. ما زالت {count} ملاحظة سابقة قائمة، لكنها أُرسلت مؤخرًا فلم تتكرر هنا."
        ),
        "root_cause_heading": "من أين جاء هذا التغيّر",
        "detail_heading": "التفاصيل",
        "open": "افتح التحليل الكامل: {link}",
        "footer": "تصلك هذه الرسالة لأنك أنشأت فحصًا مجدولًا في DAAS.",
        "no_currency": "المبالغ بوحدات البيانات الأصلية — لا تسجّل هذه البيانات أي عملة.",
        "explains": "{share} من التغيّر يأتي من {slice}، وهي {rows} فقط من إجمالي سطور المعاملات.",
        "rest_flat": "أما بقية النشاط فقد تغيّرت بنسبة {rest}.",
        "not_robust": (
            "تعامل مع هذا كمؤشر أولي لا كسبب مؤكد — فهو لا يتجاوز حد الدلالة الإحصائية بعد "
            "حساب كل الشرائح التي جرى اختبارها."
        ),
        # ── عناصر التقرير ──────────────────────────────────────────────────
        "section_basis": "أساس هذا التقرير",
        "section_figures": "الأرقام",
        "section_detail": "بقية ما جرى رصده",
        "section_method": "كيف أُعدّ هذا التقرير",
        "basis_period": "بالمقارنة مع القراءة المأخوذة في {when}.",
        "basis_records": "جرى تحليل {rows:,} سجل.",
        "basis_through": "تغطي البيانات كل شيء حتى {last_date}.",
        "basis_currency": "المبالغ معروضة بـ {currency}.",
        "figure_line": "{metric}: {prior} ← {current} ({change})",
        "figure_windows": "{current_label} ({current_rows:,} صف) مقابل {prior_label} ({prior_rows:,} صف).",
        "assure_figures": (
            "كل رقم أعلاه محسوب مباشرة من بياناتك أنت. لا يوجد في هذا التقرير رقم واحد "
            "كتبه نموذج لغوي."
        ),
        "assure_verified": (
            "جرى التحقق من النص التفسيري مقابل هذه الأرقام نفسها قبل الإرسال. وأي صياغة "
            "تذكر رقمًا لا تدعمه بياناتك تُرفض تلقائيًا."
        ),
        "assure_fallback": (
            "لم تجتز مسودة النموذج هذا التحقق، فجرى استبعادها وإنتاج التفسير أدناه من "
            "الأرقام مباشرة."
        ),
        "assure_robust": (
            "{robust} من أصل {total} تفسيرات مرشّحة تظل ذات دلالة بعد التصحيح لعدد "
            "الشرائح المختبَرة ({tested})."
        ),
        "assure_inference": (
            "الأرقام مقيسة، أما الأسباب فمستنتجة — فهي تبيّن أين تركّز التغيّر، وهذا دليل "
            "لا برهان."
        ),
        "exposure_note": "{amount} معرّضة للخطر — مخاطرة قائمة وليست جزءًا من الإجمالي أعلاه",
        "summary_more": "التقرير الكامل بالأرقام والمنهجية: {link}",
    },
}


def _t(language: str, key: str, **kwargs: Any) -> str:
    table = _TEXT.get(language) or _TEXT["en"]
    template = table.get(key) or _TEXT["en"].get(key, "")
    return template.format(**kwargs)


def format_amount(value: float, currency: str | None = None) -> str:
    """A money figure, rendered the same way in every channel and both languages.

    Western digits deliberately, including in the Arabic briefing: Arabic
    business software, invoices and banking apps across Egypt and the Gulf use
    them, and Arabic-Indic numerals in a WhatsApp briefing read as a translation
    artefact rather than as localisation.
    """
    amount = f"{abs(value):,.0f}" if abs(value) >= 1000 else f"{abs(value):,.2f}"
    sign = "-" if value < 0 else ""
    return f"{sign}{currency} {amount}".strip() if currency else f"{sign}{amount}"


def format_metric_value(value: float, metric: str, currency: str | None) -> str:
    """One metric value, rendered in the unit it is actually measured in.

    A currency symbol on a percentage, or two decimal places on an order count,
    is a small error with an outsized cost here: the figures block exists so a
    reader can check the report's arithmetic, and a figure printed in the wrong
    unit fails at precisely that job.
    """
    unit = metric_unit(metric)
    if unit == "money":
        return format_amount(value, currency)
    if unit == "percent":
        return f"{value:,.1f}%"
    if unit == "count":
        return f"{value:,.0f}"
    return format_amount(value, None)


@dataclass
class BriefingContext:
    """Everything the composer needs, all of it already computed."""

    project_name: str
    language: str = "en"
    currency: str | None = None
    row_count: int = 0
    data_last_date: str | None = None
    app_link: str = ""
    root_cause: dict[str, Any] | None = None
    suppressed_count: int = 0
    generated_at: datetime | None = None
    # When the reading this run is compared against was taken. Naming the
    # baseline is what turns "revenue fell 5.6%" from an assertion into a
    # checkable statement — without it the reader cannot tell whether they are
    # looking at a day, a week, or a drift since whenever the last run happened.
    previous_captured_at: datetime | None = None


def _root_cause_lines(ctx: BriefingContext) -> list[str]:
    """The drill-down, in two sentences a person can act on.

    Rendered from the stored result rather than the narrative when the narrative
    is missing, so this section degrades to shorter prose instead of vanishing.
    """
    result = ctx.root_cause or {}
    if not result.get("available") or not result.get("explanations"):
        return []
    top = result["explanations"][0]
    lines = [f"*{_t(ctx.language, 'root_cause_heading')}*"]
    lines.append(_t(
        ctx.language, "explains",
        share=f"{abs(top['explanatory_power_pct']):.0f}%",
        slice=top["slice_label"],
        rows=f"{top['rows_share_pct']:.1f}%",
    ))
    if top.get("rest_change_pct") is not None:
        lines.append(_t(ctx.language, "rest_flat", rest=f"{top['rest_change_pct']:+.1f}%"))
    if top.get("robust") is False:
        lines.append(_t(ctx.language, "not_robust"))
    narrative = (result.get("narrative") or "").strip()
    if narrative:
        lines.append("")
        lines.append(narrative)
    return lines


def _basis_lines(ctx: BriefingContext, findings: list[Finding]) -> list[str]:
    """What this report was computed from — stated before any conclusion.

    A real analyst report opens by naming its inputs: the period, the baseline,
    the record count, the currency. Skipping that is what makes an automated
    alert feel like an assertion from nowhere, and it is also what makes it
    unfalsifiable — a reader who cannot see the baseline cannot check the
    change against it.
    """
    lines = [f"*{_t(ctx.language, 'section_basis')}*"]
    if ctx.previous_captured_at:
        lines.append(_t(
            ctx.language, "basis_period",
            when=format_timestamp(ctx.previous_captured_at, ctx.language),
        ))
    if ctx.data_last_date:
        lines.append(_t(ctx.language, "basis_through", last_date=ctx.data_last_date))
    if ctx.row_count:
        lines.append(_t(ctx.language, "basis_records", rows=ctx.row_count))
    if ctx.currency:
        lines.append(_t(ctx.language, "basis_currency", currency=ctx.currency))
    # Only the section heading — nothing worth printing under it.
    return lines if len(lines) > 1 else []


def _figures_lines(findings: list[Finding], ctx: BriefingContext) -> list[str]:
    """Prior → current → change, for every finding that measured something.

    This is the block that makes the report checkable. The narrative says
    revenue fell; this says what it fell *from* and *to*, so a reader with the
    same data can reproduce the percentage rather than take it on trust.
    """
    rows: list[str] = []
    for finding in findings:
        if finding.current_value is None or finding.prior_value is None:
            continue
        change = (
            f"{finding.change_pct:+.1f}%" if finding.change_pct is not None else "—"
        )
        rows.append("- " + _t(
            ctx.language, "figure_line",
            metric=metric_label(ctx.language, finding.metric) if finding.metric else finding.title,
            prior=format_metric_value(finding.prior_value, finding.metric, ctx.currency),
            current=format_metric_value(finding.current_value, finding.metric, ctx.currency),
            change=change,
        ))
    if not rows:
        return []

    lines = [f"*{_t(ctx.language, 'section_figures')}*", *rows]

    # The drill-down compares two explicit date windows. Naming them (and their
    # row counts) closes the last gap a sceptical reader has: a change measured
    # over two very differently-sized windows is not the same claim as one
    # measured over two comparable ones.
    result = ctx.root_cause or {}
    current, prior = result.get("current"), result.get("prior")
    if current and prior:
        lines.append(_t(
            ctx.language, "figure_windows",
            current_label=current.get("label", "—"),
            current_rows=int(current.get("rows") or 0),
            prior_label=prior.get("label", "—"),
            prior_rows=int(prior.get("rows") or 0),
        ))
    return lines


def _assurance_lines(ctx: BriefingContext) -> list[str]:
    """The claims this system can actually keep about its own output.

    Deliberately not "this report is 100% accurate". Figures are *measured* and
    that guarantee is real and worth stating plainly. Causes are *inferred*, and
    a report that blurs the two is asking to be believed about something it
    cannot prove — which is the fastest way to lose the trust the accurate half
    had earned. So both are stated, separately.

    The verification sentence is likewise reported, not assumed: when the
    model's draft failed the figure check and was replaced, the report says so
    rather than quietly presenting the fallback as the model's work.
    """
    lines = [f"*{_t(ctx.language, 'section_method')}*", _t(ctx.language, "assure_figures")]

    result = ctx.root_cause or {}
    verification = result.get("verification") or {}
    narrative_present = bool((result.get("narrative") or "").strip())

    if narrative_present:
        lines.append(_t(ctx.language, "assure_verified"))
        if verification.get("fell_back_to_deterministic"):
            lines.append(_t(ctx.language, "assure_fallback"))

    explanations = result.get("explanations") or []
    if explanations:
        stats = result.get("stats") or {}
        robust = sum(1 for e in explanations if e.get("robust"))
        tested = int(stats.get("slices_tested") or 0)
        if tested:
            lines.append(_t(
                ctx.language, "assure_robust",
                robust=robust, total=len(explanations), tested=f"{tested:,}",
            ))
        lines.append(_t(ctx.language, "assure_inference"))

    return lines


def _compose_summary(
    findings: list[Finding],
    ctx: BriefingContext,
    total_at_stake: float,
    quiet: bool,
) -> str:
    """The executive summary that goes to WhatsApp and Telegram.

    Both cap a message at 4096 characters, and the full report — basis, figures,
    method — will exceed that on any run with several findings. Truncating it
    would drop the evidence sections first, since they sit lowest, leaving the
    conclusions without the working that justifies them. So the short channels
    get a deliberately-scoped summary and a link, rather than a report with its
    proof cut off.

    Same figures, same arithmetic, fewer of them — never a different number.
    """
    language = ctx.language if ctx.language in _TEXT else "en"
    lines: list[str] = [
        f"*{_t(language, 'title_quiet' if quiet else 'title', project=ctx.project_name)}*",
        "",
    ]

    if quiet:
        lines.append(_t(language, "quiet"))
        if ctx.suppressed_count:
            lines.append(_t(language, "quiet_suppressed", count=ctx.suppressed_count))
    else:
        lines.append(
            _t(language, "headline_one") if len(findings) == 1
            else _t(language, "headline_many", count=len(findings))
        )
        if total_at_stake > 0:
            lines.append(_t(
                language, "at_stake", amount=format_amount(total_at_stake, ctx.currency),
            ))
        lines.append("")
        top = findings[0]
        lines.append(f"{_SEVERITY_ICON.get(top.severity, '•')} *{top.title}*")

        # The one figure line for the lead finding: a number without its
        # baseline is not checkable, and this is the only place a phone reader
        # will see one.
        if top.current_value is not None and top.prior_value is not None:
            change = f"{top.change_pct:+.1f}%" if top.change_pct is not None else "—"
            lines.append(_t(
                language, "figure_line",
                metric=metric_label(language, top.metric) if top.metric else top.title,
                prior=format_metric_value(top.prior_value, top.metric, ctx.currency),
                current=format_metric_value(top.current_value, top.metric, ctx.currency),
                change=change,
            ))

        # One sentence of cause, if there is one worth a sentence.
        result = ctx.root_cause or {}
        if result.get("available") and result.get("explanations"):
            explanation = result["explanations"][0]
            lines.append("")
            lines.append(_t(
                language, "explains",
                share=f"{abs(explanation['explanatory_power_pct']):.0f}%",
                slice=explanation["slice_label"],
                rows=f"{explanation['rows_share_pct']:.1f}%",
            ))
            if explanation.get("robust") is False:
                lines.append(_t(language, "not_robust"))

        if len(findings) > 1:
            lines.append("")
            for finding in findings[1:]:
                icon = _SEVERITY_ICON.get(finding.severity, "•")
                lines.append(f"- {icon} {finding.title}")

    if ctx.app_link:
        lines.append("")
        lines.append(_t(language, "summary_more", link=ctx.app_link))
    return "\n".join(lines).strip()


def compose(
    findings: list[Finding],
    ctx: BriefingContext,
) -> tuple[str, Message]:
    """Build the briefing. Returns ``(markdown, message)``.

    The markdown is stored on the run and shown in the app; the
    :class:`~notifications.base.Message` carries every rendering a channel might
    need, so no transport has to re-derive one.
    """
    language = ctx.language if ctx.language in _TEXT else "en"
    generated = ctx.generated_at or datetime.now(UTC)
    timestamp = format_timestamp(generated, language)
    # Only *events* are summed. Adding a concentration exposure ("half your
    # revenue comes from ten customers", 322,000) to an actual loss ("revenue
    # fell 6,656") gives a headline number that describes nothing real — and it
    # is the number the owner reads first.
    total_at_stake = sum(
        abs(f.money_at_stake or 0.0) for f in findings if f.is_event
    )

    quiet = not findings
    subject = _t(
        language, "title_quiet" if quiet else "title", project=ctx.project_name,
    )

    body: list[str] = [f"## {subject}", ""]
    if ctx.data_last_date:
        body.append(f"_{_t(language, 'generated', timestamp=timestamp, rows=ctx.row_count, last_date=ctx.data_last_date)}_")
    else:
        body.append(f"_{_t(language, 'generated_nodate', timestamp=timestamp, rows=ctx.row_count)}_")
    body.append("")

    # ── The lead: three lines, phone-first ─────────────────────────────────
    if quiet:
        body.append(_t(language, "quiet"))
        if ctx.suppressed_count:
            body.append("")
            body.append(_t(language, "quiet_suppressed", count=ctx.suppressed_count))
    else:
        headline = (
            _t(language, "headline_one") if len(findings) == 1
            else _t(language, "headline_many", count=len(findings))
        )
        body.append(f"**{headline}**")
        if total_at_stake > 0:
            body.append(_t(
                language, "at_stake", amount=format_amount(total_at_stake, ctx.currency),
            ))
        body.append("")
        # The single most important finding, stated in full before any list —
        # a reader who stops here still leaves with the thing that mattered.
        top = findings[0]
        icon = _SEVERITY_ICON.get(top.severity, "•")
        body.append(f"{icon} **{top.title}**")
        body.append("")
        body.append(top.body)

    # ── Basis and figures ──────────────────────────────────────────────────
    # Placed after the lead and before the interpretation, which is the order a
    # written report uses: what needs attention, what it was measured against,
    # then what it means. A phone reader stops after the lead; anyone still
    # reading is the person who wants to check the working.
    if not quiet:
        for block in (_basis_lines(ctx, findings), _figures_lines(findings, ctx)):
            if block:
                body.append("")
                body.extend(block)

    # ── Where it came from ─────────────────────────────────────────────────
    root_cause = _root_cause_lines(ctx)
    if root_cause and not quiet:
        body.append("")
        body.extend(root_cause)

    # ── The rest ───────────────────────────────────────────────────────────
    if len(findings) > 1:
        body.append("")
        body.append(f"*{_t(language, 'section_detail')}*")
        for finding in findings[1:]:
            icon = _SEVERITY_ICON.get(finding.severity, "•")
            label = _SEVERITY_LABEL.get(language, _SEVERITY_LABEL["en"]).get(
                finding.severity, finding.severity
            )
            # An exposure is labelled, never printed as a bare amount beside
            # events. The headline total deliberately excludes exposures, so an
            # unlabelled "EGP 322,000" sitting under "Money at stake: EGP 8,756"
            # reads as an arithmetic error in the report rather than as the two
            # different quantities it actually is.
            if not finding.money_at_stake:
                amount = ""
            elif finding.is_event:
                amount = f" — {format_amount(finding.money_at_stake, ctx.currency)}"
            else:
                amount = " — " + _t(
                    language, "exposure_note",
                    amount=format_amount(finding.money_at_stake, ctx.currency),
                )
            body.append(f"- {icon} [{label}] {finding.title}{amount}")

    # ── How this was produced ──────────────────────────────────────────────
    if not quiet:
        body.append("")
        body.extend(_assurance_lines(ctx))

    if total_at_stake > 0 and not ctx.currency:
        body.append("")
        body.append(f"_{_t(language, 'no_currency')}_")

    if ctx.app_link:
        body.append("")
        body.append(_t(language, "open", link=ctx.app_link))
    body.append("")
    body.append(f"_{_t(language, 'footer')}_")

    markdown = "\n".join(body).strip()
    message = Message(
        subject=subject,
        # Short channels get the executive summary, not a truncated report.
        # `Message.for_length` would otherwise cut the full report at 4096
        # characters — and the sections most likely to fall off that cliff are
        # the figures and the method, which are exactly the parts that make the
        # claims checkable. A briefing amputated mid-evidence is worse than one
        # that never promised evidence.
        text=markdown_to_text(_compose_summary(findings, ctx, total_at_stake, quiet)),
        markdown=markdown,
        html=_to_html(markdown, language),
        language=language,
        link=ctx.app_link,
        metadata={
            "findings": len(findings),
            "money_at_stake": round(total_at_stake, 2),
            "top_severity": findings[0].severity if findings else "info",
        },
    )
    return markdown, message


def _to_html(markdown: str, language: str) -> str:
    """A minimal HTML rendering for email.

    Hand-rolled rather than pulled from a markdown library: the composer emits a
    known, closed set of constructs (headings, bold, bullets, italics), so the
    conversion is a dozen lines and the dependency would be bought to solve a
    problem that does not exist here.
    """
    direction = "rtl" if language == "ar" else "ltr"
    lines: list[str] = []
    in_list = False
    for raw in markdown.split("\n"):
        line = raw.rstrip()
        if line.startswith("- "):
            if not in_list:
                lines.append("<ul>")
                in_list = True
            lines.append(f"<li>{_inline_html(line[2:])}</li>")
            continue
        if in_list:
            lines.append("</ul>")
            in_list = False
        if not line:
            continue
        if line.startswith("## "):
            lines.append(f"<h2>{_inline_html(line[3:])}</h2>")
        elif line.startswith("# "):
            lines.append(f"<h1>{_inline_html(line[2:])}</h1>")
        else:
            lines.append(f"<p>{_inline_html(line)}</p>")
    if in_list:
        lines.append("</ul>")
    inner = "\n".join(lines)
    return (
        f'<div dir="{direction}" style="font-family:system-ui,-apple-system,Segoe UI,'
        f'Roboto,sans-serif;line-height:1.55;color:#1a1a1a;max-width:640px">'
        f"{inner}</div>"
    )


def _inline_html(text: str) -> str:
    import html
    import re

    escaped = html.escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", escaped)
    escaped = re.sub(r"_(.+?)_", r"<em>\1</em>", escaped)
    escaped = re.sub(
        r"(https?://[^\s<]+)", r'<a href="\1" style="color:#2563eb">\1</a>', escaped,
    )
    return escaped
