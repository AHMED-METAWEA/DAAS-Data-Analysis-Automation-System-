from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ExplainChartRequest(BaseModel):
    figure: dict[str, Any]
    title: str | None = None
    context: str | None = None
    model: str | None = None


class ExplainChartResponse(BaseModel):
    explanation: str
