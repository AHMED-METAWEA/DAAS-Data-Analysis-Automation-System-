"""
Professional report export.

Turns a generated markdown report plus its metadata / KPIs / grounding result
into a single self-contained, print-ready HTML document (open it and press
Ctrl/Cmd-P → "Save as PDF" for a board-ready deliverable).

Deliberately dependency-free: the project's venv does not ship the ``markdown``
package, so a small, well-scoped markdown → HTML converter lives here.  It
covers exactly what the report templates use — headings, lists, GitHub tables,
bold/italic/code, links, horizontal rules and paragraphs.

Pure standard-library; safe to unit-test without Streamlit.
"""

from __future__ import annotations

import html as _html
import re
from datetime import UTC, datetime

from .grounding import GroundingResult

# ── Inline markdown ─────────────────────────────────────────────────────────

_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
_CODE_RE = re.compile(r"`([^`]+)`")


def _inline(text: str) -> str:
    text = _html.escape(text, quote=False)
    text = _CODE_RE.sub(r"<code>\1</code>", text)
    text = _BOLD_RE.sub(r"<strong>\1</strong>", text)
    text = _ITALIC_RE.sub(r"<em>\1</em>", text)
    text = _LINK_RE.sub(r'<a href="\2">\1</a>', text)
    return text


# ── Block markdown ──────────────────────────────────────────────────────────

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_HR_RE = re.compile(r"^\s*([-*_])\1{2,}\s*$")
_UL_RE = re.compile(r"^\s*[-*]\s+(.*)$")
_OL_RE = re.compile(r"^\s*\d+\.\s+(.*)$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")


def _split_row(line: str) -> list[str]:
    cells = line.strip().strip("|").split("|")
    return [c.strip() for c in cells]


def _render_table(rows: list[str]) -> str:
    if not rows:
        return ""
    header = _split_row(rows[0])
    body = rows[2:] if len(rows) > 1 and _TABLE_SEP_RE.match(rows[1]) else rows[1:]
    out = ["<table>", "<thead><tr>"]
    out += [f"<th>{_inline(c)}</th>" for c in header]
    out.append("</tr></thead><tbody>")
    for r in body:
        cells = _split_row(r)
        out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells) + "</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def markdown_to_html(md: str) -> str:
    """Convert the subset of markdown used by DAAS reports to HTML."""
    lines = (md or "").replace("\r\n", "\n").split("\n")
    html: list[str] = []
    para: list[str] = []
    list_type: str | None = None  # "ul" | "ol"
    table_buf: list[str] = []

    def flush_para() -> None:
        if para:
            html.append(f"<p>{'<br>'.join(_inline(x) for x in para)}</p>")
            para.clear()

    def close_list() -> None:
        nonlocal list_type
        if list_type:
            html.append(f"</{list_type}>")
            list_type = None

    def flush_table() -> None:
        if table_buf:
            html.append(_render_table(table_buf))
            table_buf.clear()

    def flush_all() -> None:
        flush_para()
        close_list()
        flush_table()

    for line in lines:
        # Inside a table block?
        if table_buf:
            if line.lstrip().startswith("|"):
                table_buf.append(line)
                continue
            flush_table()

        stripped = line.strip()

        if not stripped:
            flush_all()
            continue

        if line.lstrip().startswith("|") and "|" in stripped[1:]:
            flush_para()
            close_list()
            table_buf.append(line)
            continue

        m = _HEADING_RE.match(line)
        if m:
            flush_all()
            level = len(m.group(1))
            html.append(f"<h{level}>{_inline(m.group(2).strip())}</h{level}>")
            continue

        if _HR_RE.match(line):
            flush_all()
            html.append("<hr>")
            continue

        m = _UL_RE.match(line)
        if m:
            flush_para()
            flush_table()
            if list_type != "ul":
                close_list()
                html.append("<ul>")
                list_type = "ul"
            html.append(f"<li>{_inline(m.group(1).strip())}</li>")
            continue

        m = _OL_RE.match(line)
        if m:
            flush_para()
            flush_table()
            if list_type != "ol":
                close_list()
                html.append("<ol>")
                list_type = "ol"
            html.append(f"<li>{_inline(m.group(1).strip())}</li>")
            continue

        # Plain paragraph text.
        close_list()
        para.append(stripped)

    flush_all()
    return "\n".join(html)


# ── Document assembly ───────────────────────────────────────────────────────

_CSS = """
:root { --ink:#1a2330; --muted:#5b6472; --line:#e4e8ee; --brand:#2f6df6;
        --ok:#1a7f47; --okbg:#e7f6ee; --warn:#8a5a00; --warnbg:#fdf3e0; }
* { box-sizing: border-box; }
body { font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
       color: var(--ink); line-height: 1.55; margin: 0; background: #f4f6fa; }
.page { max-width: 900px; margin: 24px auto; background: #fff; padding: 48px 56px;
        box-shadow: 0 1px 3px rgba(20,30,50,.08); }
.brand { font-size: 12px; letter-spacing: .14em; text-transform: uppercase;
         color: var(--brand); font-weight: 700; }
h1.title { font-size: 30px; margin: 4px 0 2px; line-height: 1.15; }
.subtitle { color: var(--muted); font-size: 15px; margin: 0 0 18px; }
.meta { display: flex; flex-wrap: wrap; gap: 8px 22px; padding: 12px 0 16px;
        border-top: 1px solid var(--line); border-bottom: 1px solid var(--line);
        font-size: 13px; color: var(--muted); }
.meta b { color: var(--ink); font-weight: 600; }
.kpis { display: flex; flex-wrap: wrap; gap: 14px; margin: 22px 0; }
.kpi { flex: 1 1 130px; border: 1px solid var(--line); border-radius: 10px;
       padding: 12px 14px; }
.kpi .k { font-size: 12px; color: var(--muted); text-transform: uppercase;
          letter-spacing: .04em; }
.kpi .v { font-size: 22px; font-weight: 700; margin-top: 2px; }
.badge { display: inline-block; padding: 8px 14px; border-radius: 8px;
         font-size: 13px; font-weight: 600; margin: 6px 0 20px; }
.badge.ok { background: var(--okbg); color: var(--ok); }
.badge.warn { background: var(--warnbg); color: var(--warn); }
.badge .items { display: block; font-weight: 400; margin-top: 4px; color: var(--muted); }
.body h1 { font-size: 24px; margin: 28px 0 8px; }
.body h2 { font-size: 19px; margin: 24px 0 8px; padding-bottom: 4px;
           border-bottom: 1px solid var(--line); }
.body h3 { font-size: 16px; margin: 18px 0 6px; }
.body p { margin: 8px 0; }
.body ul, .body ol { margin: 8px 0 8px 22px; }
.body li { margin: 3px 0; }
.body hr { border: none; border-top: 1px solid var(--line); margin: 20px 0; }
.body code { background: #f0f2f6; padding: 1px 5px; border-radius: 4px; font-size: 90%; }
.body table { border-collapse: collapse; width: 100%; margin: 14px 0; font-size: 14px; }
.body th, .body td { border: 1px solid var(--line); padding: 7px 10px; text-align: left; }
.body th { background: #f6f8fb; }
.footer { margin-top: 34px; padding-top: 14px; border-top: 1px solid var(--line);
          font-size: 12px; color: var(--muted); }
@media print {
  body { background: #fff; }
  .page { box-shadow: none; margin: 0; max-width: none; padding: 0 8mm; }
}
"""


def _kpi_html(kpis: list[tuple[str, str]] | None) -> str:
    if not kpis:
        return ""
    cards = "".join(
        f'<div class="kpi"><div class="k">{_html.escape(str(k))}</div>'
        f'<div class="v">{_html.escape(str(v))}</div></div>'
        for k, v in kpis
    )
    return f'<div class="kpis">{cards}</div>'


def _meta_html(meta: dict[str, str] | None) -> str:
    if not meta:
        return ""
    parts = "".join(
        f"<span><b>{_html.escape(str(k))}:</b> {_html.escape(str(v))}</span>"
        for k, v in meta.items()
    )
    return f'<div class="meta">{parts}</div>'


def _grounding_html(grounding: GroundingResult | None) -> str:
    if grounding is None or grounding.total == 0:
        return ""
    cls = "ok" if grounding.status == "clean" else "warn"
    icon = "✓" if grounding.status == "clean" else "⚠"
    items = ""
    if grounding.unverified:
        listed = ", ".join(c.raw for c in grounding.unverified[:12])
        items = f'<span class="items">Not matched to source data: {_html.escape(listed)}</span>'
    return (
        f'<div class="badge {cls}">{icon} {_html.escape(grounding.label)}{items}</div>'
    )


def build_html_report(
    title: str,
    body_md: str,
    *,
    subtitle: str = "",
    meta: dict[str, str] | None = None,
    kpis: list[tuple[str, str]] | None = None,
    grounding: GroundingResult | None = None,
    brand: str = "DAAS",
) -> str:
    """Assemble a self-contained, print-ready HTML report document."""
    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    body_html = markdown_to_html(body_md)
    footer_bits = [f"Prepared by {_html.escape(brand)} · generated {generated}"]
    if grounding is not None and grounding.total:
        footer_bits.append(_html.escape(grounding.label))
    footer_bits.append(
        "Figures are computed deterministically from your data; the language model "
        "narrates them. This document is auto-generated — review before circulating."
    )
    footer = " · ".join(footer_bits[:2]) + "<br>" + footer_bits[2]

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_html.escape(title)}</title>
<style>{_CSS}</style></head>
<body><div class="page">
<div class="brand">{_html.escape(brand)}</div>
<h1 class="title">{_html.escape(title)}</h1>
{f'<p class="subtitle">{_html.escape(subtitle)}</p>' if subtitle else ''}
{_meta_html(meta)}
{_kpi_html(kpis)}
{_grounding_html(grounding)}
<div class="body">
{body_html}
</div>
<div class="footer">{footer}</div>
</div></body></html>"""
