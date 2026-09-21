from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from agents.constants import DEFAULT_COPILOT_MODEL
from agents.copilot.graph import build_copilot_graph
from backend.app.api.deps import get_current_user, get_owned_project
from backend.app.schemas.copilot import CopilotAskRequest, CopilotAskResponse
from backend.app.services.copilot_sessions import get_or_create_session, update_session
from backend.app.services.copilot_stream import (
    CACHEABLE_ROUTES,
    ROUTE_LABELS,
    stream_copilot_events,
)
from backend.app.services.json_safe import json_safe
from db.auth_models import User
from db.platform_models import Project

router = APIRouter(prefix="/projects/{project_id}/copilot", tags=["copilot"])


def _resolve_model(payload: CopilotAskRequest, current_user: User) -> str:
    return payload.model or current_user.model_preferences.get("copilot") or DEFAULT_COPILOT_MODEL


@router.post("/ask", response_model=CopilotAskResponse)
def ask_copilot(
    payload: CopilotAskRequest,
    project: Project = Depends(get_owned_project),
    current_user: User = Depends(get_current_user),
) -> CopilotAskResponse:
    session = get_or_create_session(payload.conversation_id, project.id)
    has_previous_result = session.last_tool_result is not None

    graph = build_copilot_graph()
    result = graph.invoke(
        {
            "project_id": project.id,
            "message": payload.message,
            "history": [m.model_dump() for m in payload.history],
            "model": _resolve_model(payload, current_user),
            "model_prefs": current_user.model_preferences or {},
            "has_previous_result": has_previous_result,
            "last_route": session.last_route,
            "last_tool_result": session.last_tool_result,
            "route": "",
            "forecast_horizon_days": 30,
            "forecast_metric_hint": "",
            "clarify_question": "",
            "route_error": "",
            "tool_result": None,
            "narration": None,
        }
    )
    route = result.get("route", "general")

    if route == "follow_up":
        # Text-only continuation — the chart/table/report this is explaining
        # is already visible in the earlier message it's replying to, so it
        # isn't re-attached here. The cache is deliberately left untouched:
        # nothing new was computed, so a *second* follow-up should still
        # explain the same underlying result.
        route_label = f"{ROUTE_LABELS.get(session.last_route or '', 'Copilot')} · follow-up"
        return CopilotAskResponse(
            route=route,
            route_label=route_label,
            answer=result.get("narration") or "I couldn't process that question.",
            conversation_id=session.id,
        )

    tool_result = result.get("tool_result") or {}
    if route in CACHEABLE_ROUTES:
        update_session(session.id, route=route, tool_result=tool_result)
    table = tool_result.get("table")
    return CopilotAskResponse(
        route=route,
        route_label=ROUTE_LABELS.get(route, "General Q&A"),
        answer=result.get("narration") or "I couldn't process that question.",
        figure=tool_result.get("figure"),
        table=json_safe(table) if table else None,
        report_md=tool_result.get("report_md"),
        tool_error=(tool_result.get("raw_payload") or {}).get("error"),
        grounded=(tool_result.get("raw_payload") or {}).get("grounded"),
        conversation_id=session.id,
    )


@router.post("/ask/stream")
def ask_copilot_stream(
    payload: CopilotAskRequest,
    project: Project = Depends(get_owned_project),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    """Token-streaming twin of ``/ask``, delivered as Server-Sent Events.

    Everything request-scoped (the ORM ``project``/``current_user``, whose DB
    session may close before the generator is drained) is read into plain values
    HERE, before the generator starts — the generator itself only touches those
    primitives. See ``copilot_stream.stream_copilot_events`` for the protocol.
    """
    events = stream_copilot_events(
        project_id=project.id,
        message=payload.message,
        history=[m.model_dump() for m in payload.history],
        model=_resolve_model(payload, current_user),
        model_prefs=current_user.model_preferences or {},
        conversation_id=payload.conversation_id,
    )
    return StreamingResponse(
        events,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Disable proxy buffering (nginx) so tokens aren't held back.
            "X-Accel-Buffering": "no",
        },
    )
