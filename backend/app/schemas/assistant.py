from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AskMessage(BaseModel):
    role: str
    content: str


class AskRequest(BaseModel):
    message: str
    history: list[AskMessage] = Field(default_factory=list)
    model: str | None = None


class AskResponse(BaseModel):
    answer: str
    output: str = ""
    error: str | None = None
    figure: dict[str, Any] | None = None
    preview: list[dict[str, Any]] | None = None
