from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CopilotMessage(BaseModel):
    role: str
    content: str


class CopilotAskRequest(BaseModel):
    message: str
    history: list[CopilotMessage] = Field(default_factory=list)
    model: str | None = None
    conversation_id: str | None = Field(
        default=None,
        description="Echoed back from a previous response to enable follow-up "
        "questions about that answer. Omit for a fresh conversation.",
    )


class CopilotAskResponse(BaseModel):
    route: str
    route_label: str
    answer: str
    figure: dict[str, Any] | None = None
    table: list[dict[str, Any]] | None = None
    report_md: str | None = None
    tool_error: str | None = None
    grounded: bool | None = None
    conversation_id: str = Field(
        description="Pass this back on the next request to let the copilot "
        "answer follow-up questions about this turn's result.",
    )
