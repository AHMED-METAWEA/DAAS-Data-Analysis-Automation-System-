from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class KpiCardOut(BaseModel):
    label: str
    value: str
    direction: str | None = None


class ChartOut(BaseModel):
    title: str
    insight: str = ""
    width: str = "half"
    figure: dict[str, Any]


class DashboardResponse(BaseModel):
    kpis: list[KpiCardOut]
    charts: list[ChartOut]
    revenue_label: str
    row_count: int


class ChartAskRequest(BaseModel):
    query: str
    model: str | None = None


class ChartAskResponse(BaseModel):
    figures: list[dict[str, Any]]
    output: str
    error: str | None = None


class AutoChartsRequest(BaseModel):
    focus_context: str = ""
    model: str | None = None


class AutoChartsResponse(BaseModel):
    charts: list[ChartOut]


class SnapshotResponse(BaseModel):
    kpis: list[KpiCardOut]
    row_count: int
    column_count: int
