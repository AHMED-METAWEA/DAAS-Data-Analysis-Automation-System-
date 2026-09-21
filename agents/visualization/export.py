"""
Standalone dashboard export.

Bundles the KPI cards and every chart into a single, self-contained HTML file
(Plotly loaded from CDN) that opens in any browser and prints cleanly to PDF —
a shareable deliverable, the kind of "publish to web" a Power BI report offers.

Pure Plotly / stdlib.
"""

from __future__ import annotations

import html as _html
from datetime import UTC, datetime

_PLOTLY_CDN = "https://cdn.plot.ly/plotly-2.35.2.min.js"

_CSS = """
* { box-sizing: border-box; }
body { margin: 0; background: #eef1f6; color: #1f2a3d;
       font-family: "Segoe UI", system-ui, -apple-system, Roboto, Helvetica, Arial, sans-serif; }
.wrap { max-width: 1180px; margin: 24px auto; padding: 0 18px; }
.header { margin-bottom: 18px; }
.brand { font-size: 12px; letter-spacing: .14em; text-transform: uppercase; color: #2f6df6; font-weight: 700; }
h1 { font-size: 28px; margin: 4px 0 2px; }
.sub { color: #5b6472; margin: 0; }
.meta { color: #5b6472; font-size: 13px; margin-top: 8px; }
.meta b { color: #1f2a3d; }
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 14px; margin: 18px 0; }
.kpi { background: #fff; border: 1px solid #e4e8ee; border-radius: 12px; padding: 14px 16px; }
.kpi .k { font-size: 12px; color: #5b6472; text-transform: uppercase; letter-spacing: .04em; }
.kpi .v { font-size: 26px; font-weight: 700; margin-top: 4px; }
.kpi .d.up { color: #1a7f47; } .kpi .d.down { color: #e5484d; }
.charts { display: grid; grid-template-columns: repeat(2, 1fr); gap: 16px; }
.card { background: #fff; border: 1px solid #e4e8ee; border-radius: 12px; padding: 10px 12px 14px; }
.card.full { grid-column: 1 / -1; }
.card .insight { color: #5b6472; font-size: 13px; margin: 2px 4px 0; }
.footer { color: #5b6472; font-size: 12px; margin: 22px 2px; }
@media (max-width: 720px) { .charts { grid-template-columns: 1fr; } }
@media print { body { background: #fff; } .card, .kpi { break-inside: avoid; } }
"""


def _fig_div(fig) -> str:
    return fig.to_html(
        full_html=False,
        include_plotlyjs=False,
        config={"displaylogo": False, "responsive": True,
                "modeBarButtonsToRemove": ["sendDataToCloud"]},
    )


def build_dashboard_html(
    title: str,
    kpis: list[dict],
    charts: list[dict],
    *,
    subtitle: str = "",
    meta: dict[str, str] | None = None,
    brand: str = "DAAS",
) -> str:
    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    kpi_html = "".join(
        f'<div class="kpi"><div class="k">{_html.escape(str(c["label"]))}</div>'
        f'<div class="v">{_html.escape(str(c["value"]))}</div>'
        + (f'<div class="d {c.get("direction","")}">{_html.escape(c.get("direction","").title())}</div>'
           if c.get("direction") else "")
        + "</div>"
        for c in kpis
    )

    cards = []
    for ch in charts:
        cls = "card full" if ch.get("width") == "full" else "card"
        insight = f'<div class="insight">{_html.escape(ch.get("insight",""))}</div>' if ch.get("insight") else ""
        cards.append(f'<div class="{cls}">{_fig_div(ch["fig"])}{insight}</div>')
    charts_html = "".join(cards)

    meta_html = ""
    if meta:
        meta_html = '<div class="meta">' + " &nbsp;·&nbsp; ".join(
            f"<b>{_html.escape(str(k))}:</b> {_html.escape(str(v))}" for k, v in meta.items()
        ) + "</div>"

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_html.escape(title)}</title>
<script src="{_PLOTLY_CDN}"></script>
<style>{_CSS}</style></head>
<body><div class="wrap">
<div class="header">
  <div class="brand">{_html.escape(brand)}</div>
  <h1>{_html.escape(title)}</h1>
  {f'<p class="sub">{_html.escape(subtitle)}</p>' if subtitle else ''}
  {meta_html}
</div>
<div class="kpis">{kpi_html}</div>
<div class="charts">{charts_html}</div>
<div class="footer">Prepared by {_html.escape(brand)} · generated {generated} ·
figures computed deterministically from your data.</div>
</div></body></html>"""
