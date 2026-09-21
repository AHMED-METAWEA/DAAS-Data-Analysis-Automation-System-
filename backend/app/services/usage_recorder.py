"""Binds LLM usage events to the user and project that caused them.

``tools/llm_usage.py`` knows how to emit an event but deliberately knows nothing
about users, projects or requests. This module is the seam where a raw event
acquires an owner and gets persisted.

Two things here are load-bearing and easy to get wrong:

**Where the ContextVar is set.** FastAPI runs ``def`` endpoints and ``def``
dependencies in a threadpool. A ContextVar set inside one of those is set on a
*copy* of the context and is invisible to everything else in the request. So the
binding has to happen in an ``async`` dependency, which runs directly in the
request's own Task. From there it propagates *into* the threadpool — anyio
copies the caller's context when it hands work to a worker — so an agent five
frames deep inside a sync endpoint still sees the sink.

**Why the context is a mutable object.** The user id is known at authentication
time, but the project id is only known once ``get_owned_project`` has run — and
that dependency is sync, so anything it *sets* is lost. It can still *mutate*,
because the copied context holds the same object reference. Hence a mutable
:class:`UsageContext` rather than separate ContextVars per field.

The sink writes on its own short-lived session rather than the request's: the
request session may be mid-transaction or already rolled back by the error that
the failed LLM call caused, and metering must not be the thing that decides
whether that transaction commits.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from collections.abc import Iterator

from db.session import get_session
from db.usage_models import LlmUsageEvent
from tools.llm_usage import UsageEvent, set_sink

logger = logging.getLogger(__name__)


@dataclass
class UsageContext:
    """Who a run of LLM calls belongs to. Mutated in place — see module docs."""

    user_id: str
    project_id: str | None = None
    # "user" for an interactive request, "schedule" for the monitoring worker.
    source: str = "user"


_context: ContextVar[UsageContext | None] = ContextVar("daas_usage_context", default=None)


def current_context() -> UsageContext | None:
    return _context.get()


def attach_project(project_id: str) -> None:
    """Record which project the in-flight LLM calls belong to.

    A no-op when nothing is bound (an unauthenticated route, a script), so
    callers never have to guard.
    """
    ctx = _context.get()
    if ctx is not None:
        ctx.project_id = project_id


def persist(ctx: UsageContext, event: UsageEvent) -> None:
    """Write one event. Raising here would be caught and swallowed by
    ``tools.llm_usage.record``, but failing quietly *and* loudly-in-the-log is
    better than relying on that."""
    session = get_session()
    try:
        session.add(
            LlmUsageEvent(
                user_id=ctx.user_id,
                project_id=ctx.project_id,
                purpose=event.purpose,
                provider=event.provider,
                model=event.model,
                prompt_tokens=event.prompt_tokens,
                completion_tokens=event.completion_tokens,
                total_tokens=event.total_tokens,
                latency_ms=event.latency_ms,
                streamed=event.streamed,
                status=event.status,
                # Provider errors can be enormous HTML pages; the column is Text
                # but a dashboard only ever shows the first line.
                error=(event.error or None) and event.error[:2000],
                fallback_depth=event.fallback_depth,
                source=ctx.source,
                created_at=event.created_at,
            )
        )
        session.commit()
    except Exception:
        session.rollback()
        logger.warning("Could not persist LLM usage event for user=%s", ctx.user_id, exc_info=True)
    finally:
        session.close()


def bind(ctx: UsageContext) -> None:
    """Make ``ctx`` the owner of every LLM call in the current context."""
    _context.set(ctx)
    set_sink(lambda event: persist(ctx, event))


@contextmanager
def usage_context(user_id: str, project_id: str | None = None, source: str = "user") -> Iterator[UsageContext]:
    """Bind an owner for the duration of a block.

    This is what work running *outside* a request needs — the monitoring
    scheduler in particular. A scheduled briefing spends the schedule owner's
    tokens with nobody watching, and without an explicit binding those calls
    have no ambient sink and land on nobody's bill.
    """
    ctx = UsageContext(user_id=user_id, project_id=project_id, source=source)
    token = _context.set(ctx)
    set_sink(lambda event: persist(ctx, event))
    try:
        yield ctx
    finally:
        _context.reset(token)
        set_sink(None)
