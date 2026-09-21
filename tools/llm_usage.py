"""Per-call LLM token accounting.

Every provider SDK returns exact token counts on its response and, before this
module, all eight handlers in ``tools/llm_client.py`` threw them away — there
was no ``usage`` reference anywhere in the codebase. So "how much did this cost"
had no answer, and the only way to find out was the provider's own console.

The awkward part is that ``complete()`` returns a bare ``str`` and has 23 call
sites across agents, services and the streaming path. Changing the return type
to carry usage would mean touching every one of them. Instead the client
*pushes* an event to a sink installed in the surrounding context, so metering is
invisible to callers and no agent needs to know it exists.

A ContextVar is the right carrier because the sink is per-request, not global:
Starlette propagates context into the threadpool where the sync endpoints run,
so a sink installed in a FastAPI dependency reaches an agent five frames deep
without a single signature change. Work that runs *outside* a request — the
monitoring scheduler — has no ambient sink and must install one explicitly,
naming the user the run belongs to; otherwise its tokens land on nobody's bill.

Nothing here is allowed to break an LLM call. Metering is bookkeeping; a failed
insert must never turn a working answer into an error, so every sink invocation
is wrapped and logged rather than raised.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

# ok       — provider returned an answer
# error    — provider raised; tokens may still have been spent upstream
# cancelled— consumer abandoned a stream mid-answer (the copilot's stop button)
STATUSES = ("ok", "error", "cancelled")


@dataclass(slots=True)
class UsageEvent:
    """One LLM call, as billed.

    ``prompt_tokens``/``completion_tokens`` are whatever the provider reported —
    never an estimate. When a provider reports nothing (some streams, most
    errors) the counts stay 0 and :attr:`counted` is False, which is the signal
    a dashboard needs to say "not counted" instead of quietly showing zero and
    letting a reader interpret it as free.
    """

    purpose: str
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: int = 0
    streamed: bool = False
    status: str = "ok"
    error: str | None = None
    # 0 = the first provider in the fallback chain served it. Anything higher
    # means the preferred provider failed, which is worth surfacing: a chain
    # quietly running on its third choice is a bill nobody predicted.
    fallback_depth: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def counted(self) -> bool:
        """True when the provider actually reported token counts."""
        return self.total_tokens > 0 or self.prompt_tokens > 0 or self.completion_tokens > 0

    def normalized(self) -> UsageEvent:
        """Fill in ``total_tokens`` when a provider reports only the two parts.

        Anthropic returns ``input_tokens``/``output_tokens`` and no total, so
        without this a dashboard summing ``total_tokens`` would read every
        Anthropic call as zero.
        """
        if not self.total_tokens:
            self.total_tokens = self.prompt_tokens + self.completion_tokens
        return self


Sink = Callable[[UsageEvent], None]

_sink: ContextVar[Sink | None] = ContextVar("llm_usage_sink", default=None)


@contextmanager
def usage_sink(sink: Sink) -> Iterator[None]:
    """Install ``sink`` for the duration of the block.

    Resets to the previous sink on exit rather than to ``None``, so nesting a
    scheduler run inside a request doesn't silently disarm the outer sink.
    """
    token = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(token)


def set_sink(sink: Sink | None) -> None:
    """Install ``sink`` for the rest of the current context, with no matching
    reset.

    This is the form a FastAPI dependency needs. A ``with`` block cannot span
    the gap between a dependency returning and the endpoint running, and each
    request is handled in its own asyncio Task — which copies the context at
    creation — so a bare ``set`` here is scoped to that one request and cannot
    leak into another. Use :func:`usage_sink` anywhere the scope is a block.
    """
    _sink.set(sink)


def current_sink() -> Sink | None:
    return _sink.get()


def record(event: UsageEvent) -> None:
    """Hand one event to the installed sink, if any.

    Never raises. A metering failure that propagated would take down the answer
    it was measuring, which trades a real feature for a bookkeeping detail.
    """
    sink = _sink.get()
    if sink is None:
        return
    try:
        sink(event.normalized())
    except Exception:
        logger.warning(
            "LLM usage sink failed for purpose=%s provider=%s (call itself was unaffected)",
            event.purpose,
            event.provider,
            exc_info=True,
        )
