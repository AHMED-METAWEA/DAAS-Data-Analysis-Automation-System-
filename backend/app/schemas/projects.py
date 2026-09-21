from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    data_source_mode: str = Field(default="files", pattern="^(files|database|google_sheet)$")


class ProjectOut(BaseModel):
    id: str
    name: str
    slug: str
    data_source_mode: str
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
