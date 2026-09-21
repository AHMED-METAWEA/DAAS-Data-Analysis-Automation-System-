from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ReportSummaryOut(BaseModel):
    id: str
    project_id: str
    project_name: str
    type: str
    title: str
    created_at: datetime


class ReportOut(ReportSummaryOut):
    markdown: str
    grounding: dict[str, Any] = Field(default_factory=dict)


class CreateReportRequest(BaseModel):
    type: str
    title: str
    markdown: str
    grounding: dict[str, Any] = Field(default_factory=dict)
