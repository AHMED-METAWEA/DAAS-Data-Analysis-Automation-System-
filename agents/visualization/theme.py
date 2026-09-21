"""
DAAS house visual theme.

A single, registered Plotly template ("smart_analyst") plus helpers so every
chart — whether built deterministically or written by the LLM — shares one
cohesive, professional look: consistent palette, typography, spacing and
number formatting. This is what lifts the dashboard from "a pile of default
Plotly charts" to a designed BI report.

Pure Plotly / stdlib — no Streamlit, so it is importable anywhere and testable.
"""

from __future__ import annotations

import plotly.graph_objects as go
import plotly.io as pio

# ── Brand palette ───────────────────────────────────────────────────────────

INK = "#1F2A3D"          # primary text
MUTED = "#5B6472"        # secondary text
GRID = "#ECEFF4"         # gridlines
PAPER = "#FFFFFF"
PANEL = "#FFFFFF"
PRIMARY = "#2F6DF6"

# Categorical sequence — distinct, print-safe, colour-blind friendly-ish.
SEQUENCE = [
    "#2F6DF6", "#12B5A5", "#F5A524", "#EF5DA8",
    "#7C5CFC", "#2CA24C", "#E5484D", "#0FB5C4",
    "#8B8FA3", "#C026D3",
]

# Continuous scale for heatmaps / sequential encodings.
SEQUENTIAL = [
    [0.0, "#EAF1FE"], [0.25, "#B9D0FB"], [0.5, "#7AA5F8"],
    [0.75, "#3D79F2"], [1.0, "#1B4FD1"],
]

FONT_FAMILY = '"Segoe UI", system-ui, -apple-system, Roboto, Helvetica, Arial, sans-serif'

TEMPLATE_NAME = "smart_analyst"


def _build_template() -> go.layout.Template:
    axis = dict(
        showgrid=False,
        zeroline=False,
        showline=True,
        linecolor=GRID,
        ticks="outside",
        tickcolor=GRID,
        title=dict(font=dict(size=13, color=MUTED)),
        tickfont=dict(size=12, color=MUTED),
    )
    return go.layout.Template(
        layout=dict(
            colorway=SEQUENCE,
            colorscale=dict(sequential=SEQUENTIAL, diverging="RdBu"),
            font=dict(family=FONT_FAMILY, color=INK, size=13),
            title=dict(
                font=dict(family=FONT_FAMILY, size=18, color=INK),
                x=0.01, xanchor="left", y=0.96, yanchor="top",
            ),
            paper_bgcolor=PAPER,
            plot_bgcolor=PANEL,
            margin=dict(l=56, r=28, t=64, b=48),
            hoverlabel=dict(
                bgcolor="white", bordercolor=GRID,
                font=dict(family=FONT_FAMILY, size=12, color=INK),
            ),
            hovermode="x unified",
            legend=dict(
                orientation="h", yanchor="bottom", y=1.02,
                xanchor="right", x=1, title="",
                font=dict(size=12, color=MUTED),
            ),
            xaxis={**axis},
            yaxis={**axis, "showgrid": True, "gridcolor": GRID},
        )
    )


def register(make_default: bool = True) -> None:
    """Register the house template (idempotent). Optionally make it the default
    so LLM-written ``px`` charts inherit the brand look automatically."""
    pio.templates[TEMPLATE_NAME] = _build_template()
    if make_default:
        pio.templates.default = TEMPLATE_NAME


# Register on import so a bare ``import ...theme`` is enough.
register()


# ── Figure post-processing ──────────────────────────────────────────────────

def style_figure(
    fig: go.Figure,
    *,
    title: str | None = None,
    subtitle: str | None = None,
    money_axis: str | None = None,   # "y" | "x" | None
    height: int | None = None,
    show_legend: bool | None = None,
) -> go.Figure:
    """Enforce the house style on any figure (including LLM-generated ones)."""
    if fig is None:
        return fig
    fig.update_layout(template=TEMPLATE_NAME)

    if title is not None:
        text = f"<b>{title}</b>"
        if subtitle:
            text += f'<br><span style="font-size:12px;color:{MUTED}">{subtitle}</span>'
        fig.update_layout(title_text=text)
        if subtitle:
            fig.update_layout(margin=dict(t=78))
    elif subtitle:
        fig.update_layout(title_text=f'<span style="font-size:12px;color:{MUTED}">{subtitle}</span>')

    if money_axis in ("x", "y"):
        fig.update_layout(**{f"{money_axis}axis": dict(tickprefix="$", tickformat=",.0f")})
    if height is not None:
        fig.update_layout(height=height)
    if show_legend is not None:
        fig.update_layout(showlegend=show_legend)
    return fig


# ── Number formatting ───────────────────────────────────────────────────────

def human_money(value: float | int | None) -> str:
    """Compact currency: 1234567 -> "$1.23M"."""
    if value is None:
        return "—"
    v = float(value)
    sign = "-" if v < 0 else ""
    v = abs(v)
    if v >= 1_000_000_000:
        return f"{sign}${v / 1_000_000_000:.2f}B"
    if v >= 1_000_000:
        return f"{sign}${v / 1_000_000:.2f}M"
    if v >= 1_000:
        return f"{sign}${v / 1_000:.1f}K"
    return f"{sign}${v:,.0f}"


def to_rgba(hex_color: str, alpha: float) -> str:
    """Convert a "#RRGGBB" hex color (e.g. a ``SEQUENCE`` entry) to an
    ``rgba(r,g,b,a)`` string, so fills/bands can be derived from the house
    palette instead of hand-maintained as separate hardcoded constants."""
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def human_int(value: float | int | None) -> str:
    if value is None:
        return "—"
    v = float(value)
    if abs(v) >= 1_000_000:
        return f"{v / 1_000_000:.2f}M"
    if abs(v) >= 1_000:
        return f"{v / 1_000:.1f}K"
    return f"{int(round(v)):,}"
