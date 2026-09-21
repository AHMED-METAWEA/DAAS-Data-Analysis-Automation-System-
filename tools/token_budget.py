"""Request sizing against a provider's per-minute token budget.

The failure this exists to prevent is not a rate-limit *retry* problem, it is a
sizing problem, and the difference matters. Groq bills a chat completion as
``prompt_tokens + max_tokens`` — the reservation counts whether or not the model
uses it — and rejects the call with **HTTP 413** when that sum exceeds the
account's tokens-per-minute limit:

    Request too large for model `openai/gpt-oss-120b` ... on tokens per minute
    (TPM): Limit 8000, Requested 9047

413 is not 429. Waiting does not help and neither does retrying the identical
request: the same bytes will be rejected every minute forever. The only fix is
to make the request smaller, and the cheapest thing to make smaller is the
reservation — a 3000-token ceiling on a brief that ends up 1100 tokens long is
1900 tokens of budget spent on nothing.

So the client asks this module for a ceiling that fits *before* it calls, and
falls back to the limit the provider itself reported if the estimate was wrong.
Estimates here are deliberately pessimistic: under-estimating the prompt is what
produces the 413, while over-estimating costs only a slightly shorter answer.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

# ~3.6 chars/token rather than the usual 4: English prose averages closer to 4,
# but these prompts are dense with numbers, ``{{tokens}}`` and punctuation, all
# of which fragment badly. Erring low on chars-per-token errs high on tokens,
# which is the safe direction.
_CHARS_PER_TOKEN = 3.6

# Per-message envelope (role, delimiters) the provider adds on top of content.
_MESSAGE_OVERHEAD = 4

# Providers' published on-demand TPM ceilings. 0 means "no ceiling worth
# modelling here" — the paid tiers of the other three are large enough that a
# single report never approaches them, and guessing low would truncate answers
# for no reason.
_DEFAULT_TPM = {"groq": 8000, "anthropic": 0, "openai": 0, "openrouter": 0}

# Headroom against the estimate being wrong. A 413 costs the user the whole
# report; a slightly shorter completion costs a sentence.
_SAFETY_MARGIN = 0.06

# Below this a completion is not a short answer, it is a truncated one. When
# even this does not fit, the prompt itself is the problem and shrinking the
# reservation further only turns a clear error into a mangled report.
MIN_COMPLETION_TOKENS = 700

# Both refusals carry the provider's own tokenizer numbers, and both name the
# window they apply to. ``Used`` appears only on a 429 — a 413 is one request
# being bigger than the entire limit, so nothing has been consumed yet.
_LIMIT_RE = re.compile(
    r"Limit\s+(\d+)\s*,\s*(?:Used\s+(\d+)\s*,\s*)?Requested\s+(\d+)", re.IGNORECASE
)
_SCOPE_RE = re.compile(r"tokens per (minute|day|hour)", re.IGNORECASE)
_RETRY_RE = re.compile(r"try again in ([0-9hms.]+)", re.IGNORECASE)

# Margin over the provider's numbers. Both sides of the arithmetic below come
# from the provider's own tokenizer, so this covers rounding rather than a bad
# estimate — a percentage of a 200,000-token daily limit would throw away
# 12,000 usable tokens to guard against an error of a few.
_REPORTED_MARGIN = 64


@dataclass(frozen=True)
class TokenLimit:
    """A provider's refusal, in its own numbers.

    ``used`` is 0 on a 413, where the refusal is "this one request is larger
    than your entire limit" rather than "your window is spent".
    """

    limit: int
    used: int
    requested: int
    scope: str = ""       # "minute" | "day" | "hour" | ""
    retry_after: str = ""  # as the provider phrased it, e.g. "3m4.464s"

    @property
    def headroom(self) -> int:
        """Tokens still available in the window."""
        return max(0, self.limit - self.used)

    def prompt_tokens(self, max_tokens: int) -> int:
        """The prompt as the provider counted it — exact, not estimated."""
        return max(0, self.requested - max_tokens)

    @property
    def exhausted(self) -> bool:
        """True when the window is spent, as opposed to one request being big."""
        return self.used > 0

    def window(self) -> str:
        return f"per-{self.scope}" if self.scope else "token"


def estimate_tokens(text: str) -> int:
    """Pessimistic token count for a string."""
    if not text:
        return 0
    return int(len(text) / _CHARS_PER_TOKEN) + 1


def estimate_messages_tokens(messages: list[dict]) -> int:
    """Pessimistic token count for a chat message list, envelopes included."""
    return sum(
        estimate_tokens(str(m.get("content") or "")) + _MESSAGE_OVERHEAD
        for m in messages
    )


def provider_tpm_limit(provider: str) -> int:
    """The per-minute token ceiling to size requests against, 0 for unlimited.

    ``LLM_TPM_LIMIT_GROQ`` (etc.) overrides one provider; ``LLM_TPM_LIMIT``
    overrides all of them. Raising this after upgrading a plan is the single
    change needed to get longer reports back.
    """
    specific = os.environ.get(f"LLM_TPM_LIMIT_{provider.upper()}")
    shared = os.environ.get("LLM_TPM_LIMIT")
    for raw in (specific, shared):
        if raw and raw.strip():
            try:
                return max(0, int(raw.strip()))
            except ValueError:
                continue
    return _DEFAULT_TPM.get(provider, 0)


def fit_max_tokens(
    messages: list[dict], max_tokens: int, *, limit: int, prompt_tokens: int | None = None
) -> int:
    """Largest completion ceiling that keeps ``prompt + ceiling`` under ``limit``.

    Returns ``max_tokens`` unchanged when it already fits or when ``limit`` is 0
    (unlimited). Never returns less than :data:`MIN_COMPLETION_TOKENS`: at that
    point the request cannot be saved by shrinking the reservation, and the
    caller should surface the real problem rather than ship a truncated answer.
    """
    if limit <= 0:
        return max_tokens
    prompt = estimate_messages_tokens(messages) if prompt_tokens is None else prompt_tokens
    available = int(limit * (1 - _SAFETY_MARGIN)) - prompt
    if available >= max_tokens:
        return max_tokens
    return max(MIN_COMPLETION_TOKENS, available)


def parse_token_limit(exc: BaseException) -> TokenLimit | None:
    """The provider's token refusal in structured form, else ``None``.

    Covers both shapes, because the useful response to them is the same
    arithmetic on different inputs:

    * **413** — one request larger than the whole limit. Nothing is consumed;
      the request has to shrink.
    * **429 with ``Used``** — the window is spent. Waiting helps, but so does
      shrinking, whenever what remains still fits the prompt and a usable
      answer. That case is common and easy to miss: a refusal reading
      ``Limit 200000, Used 194314, Requested 6113`` overshot by 427 tokens and
      would have gone through at a slightly shorter answer.

    The provider's numbers beat any local estimate — they are its own
    tokenizer's verdict on this exact request.
    """
    message = str(exc)
    lowered = message.lower()
    markers = ("413", "429", "too large", "rate_limit", "tokens per")
    if not any(marker in lowered for marker in markers):
        return None
    match = _LIMIT_RE.search(message)
    if not match:
        return None
    scope = _SCOPE_RE.search(message)
    retry = _RETRY_RE.search(message)
    return TokenLimit(
        limit=int(match.group(1)),
        used=int(match.group(2) or 0),
        requested=int(match.group(3)),
        scope=scope.group(1).lower() if scope else "",
        retry_after=retry.group(1).rstrip(".") if retry else "",
    )


def is_token_limit_error(exc: BaseException) -> bool:
    """True for token-budget refusals, as opposed to timeouts or auth failures."""
    return parse_token_limit(exc) is not None


def fit_from_reported(report: TokenLimit, max_tokens: int) -> int | None:
    """Completion ceiling to retry with, from the provider's own accounting.

    ``None`` means no ceiling rescues this request: what remains in the window
    will not hold the prompt plus an answer worth reading, so the caller should
    report the window rather than retry into the same wall.
    """
    available = report.headroom - report.prompt_tokens(max_tokens) - _REPORTED_MARGIN
    if available < MIN_COMPLETION_TOKENS:
        return None
    return min(max_tokens, available)


def describe_token_limit(report: TokenLimit, max_tokens: int) -> str:
    """Plain-language account of a refusal, in place of the provider's raw JSON.

    The two cases need opposite advice, and the raw blob gives neither. A spent
    window is about waiting or upgrading; an over-large single request is about
    sending less context, and telling someone to wait for that one would have
    them wait forever.
    """
    if report.exhausted:
        wait = f"; resets in {report.retry_after}" if report.retry_after else ""
        return (
            f"{report.window()} token quota is spent: {report.used:,} of {report.limit:,} used, "
            f"{report.headroom:,} left, this request needs {report.requested:,}{wait}. "
            "Wait for the window to reset, or raise the quota on the provider plan"
        )
    return (
        f"prompt is ~{report.prompt_tokens(max_tokens):,} tokens against a {report.window()} "
        f"budget of {report.limit:,}; shortening the answer cannot make this request fit. "
        "Reduce the context sent, or raise the ceiling with LLM_TPM_LIMIT once the provider "
        "plan allows it"
    )


def prompt_budget(provider: str, reserved_completion: int) -> int:
    """How many prompt tokens a caller may spend, given what it wants to write.

    Prompt builders use this to decide how much optional context to include,
    instead of assembling everything and discovering the ceiling at the API.
    Returns 0 when the provider has no modelled ceiling.
    """
    limit = provider_tpm_limit(provider)
    if limit <= 0:
        return 0
    return max(0, int(limit * (1 - _SAFETY_MARGIN)) - reserved_completion)
