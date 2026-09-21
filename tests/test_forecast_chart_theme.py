from __future__ import annotations

import numpy as np
import pandas as pd

from agents.forecasting.nodes import _build_chart
from agents.visualization import theme


def _chart():
    dates = pd.date_range("2024-01-01", periods=60).values
    y = np.linspace(50, 150, 60)
    forecast_dates = pd.date_range("2024-03-01", periods=10)
    forecast = np.linspace(150, 170, 10)
    lower = forecast - 5
    upper = forecast + 5
    return _build_chart(dates, y, forecast_dates, forecast, lower, upper, "revenue", "Prophet", "daily")


def test_build_chart_uses_smart_analyst_template() -> None:
    fig = _chart()
    # Plotly resolves a string `template=` assignment against `pio.templates`
    # at set-time, so `fig.layout.template` is already the resolved Template
    # object — this is a robust check, not a string comparison.
    assert list(fig.layout.template.layout.colorway) == theme.SEQUENCE


def test_build_chart_uses_palette_colors_not_hardcoded_css_names() -> None:
    fig = _chart()
    colors = []
    for trace in fig.data:
        line = getattr(trace, "line", None)
        if line is not None and line.color:
            colors.append(str(line.color))
        fill = getattr(trace, "fillcolor", None)
        if fill:
            colors.append(str(fill))
    joined = " ".join(colors).lower()
    for banned in ("royalblue", "crimson", "lightblue"):
        assert banned not in joined


def test_build_chart_title_has_bold_markup() -> None:
    fig = _chart()
    assert "<b>" in fig.layout.title.text
