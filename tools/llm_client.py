"""Multi-provider LLM client with automatic fallback.

Providers are tried in the order given by ``LLM_PROVIDER_ORDER`` (default
``groq,anthropic,openai,openrouter``). If a provider raises (timeout, rate
limit, API error, or an empty response), the next provider in the chain is
tried and the failure is logged.

Only Groq honors the caller's literal ``model`` argument — that's the
sidebar's per-agent model picker, and those model IDs are Groq-specific.
Every other provider resolves its own model for the given ``purpose`` from
``tools/llm_provider_models.py``, since model identifiers aren't portable
across providers. OpenRouter reuses the ``openai`` SDK pointed at a
different base URL, since it's wire-compatible with the OpenAI API.

Every attempt is sized against the provider's per-minute token budget first
(see :mod:`tools.token_budget`). Groq bills ``prompt + max_tokens`` and rejects
the call with 413 when the sum exceeds the account's TPM ceiling, so an
oversized ``max_tokens`` fails a request that the model would have answered in a
third of the space. Falling over to the next provider is the wrong response to
that — the request is too big, not the provider broken — so the client shrinks
the reservation and retries the same provider before moving on.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import NamedTuple

from tools.llm_provider_models import PROVIDER_MODELS, groq_reasoning_effort
from tools.llm_usage import UsageEvent, record
from tools.token_budget import (
    describe_token_limit,
    fit_from_reported,
    fit_max_tokens,
    parse_token_limit,
    provider_tpm_limit,
)

logger = logging.getLogger(__name__)

DEFAULT_PROVIDER_ORDER = ["groq", "anthropic", "openai", "openrouter"]


class Completion(NamedTuple):
    """A provider's answer plus what it cost, as the provider reported it."""

    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass(slots=True)
class _UsageBox:
    """Mutable slot a streaming handler fills in once the stream ends.

    A generator can't return a value to the code driving it, so streamed token
    counts need somewhere to land that the caller already holds a reference to.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


def _usage_openai_style(resp) -> tuple[int, int, int]:
    """Token counts from an OpenAI-shaped response (OpenAI, Groq, OpenRouter).

    Defensive on every access: ``usage`` is absent on some proxied deployments
    and the fields are occasionally ``None`` rather than 0.
    """
    usage = getattr(resp, "usage", None)
    if usage is None:
        return 0, 0, 0
    return (
        int(getattr(usage, "prompt_tokens", 0) or 0),
        int(getattr(usage, "completion_tokens", 0) or 0),
        int(getattr(usage, "total_tokens", 0) or 0),
    )


def _usage_anthropic(resp) -> tuple[int, int, int]:
    """Token counts from an Anthropic response.

    Anthropic names them ``input_tokens``/``output_tokens`` and reports no
    total, so the total is derived rather than read.
    """
    usage = getattr(resp, "usage", None)
    if usage is None:
        return 0, 0, 0
    prompt = int(getattr(usage, "input_tokens", 0) or 0)
    completion = int(getattr(usage, "output_tokens", 0) or 0)
    return prompt, completion, prompt + completion


class AllProvidersFailedError(RuntimeError):
    """Raised when every provider in the fallback chain failed."""


_PROVIDER_API_KEY_ENV = {
    "groq": "GROQ_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


def any_provider_configured() -> bool:
    """True if at least one provider in DEFAULT_PROVIDER_ORDER has an API key set."""
    return any(os.environ.get(env_var) for env_var in _PROVIDER_API_KEY_ENV.values())


def provider_status() -> list[dict[str, str | bool]]:
    """Fallback order with each provider's configured/unconfigured status, for
    the Settings UI. Never exposes the key itself — only whether it's set."""
    return [
        {"id": p, "configured": bool(os.environ.get(_PROVIDER_API_KEY_ENV[p]))}
        for p in _provider_order()
        if p in _PROVIDER_API_KEY_ENV
    ]


def first_available_provider() -> str | None:
    """The provider that will actually serve the next call, or ``None``.

    Prompt builders size themselves against this rather than against the head of
    the default order: the budget that constrains a request belongs to whoever
    answers it, and the first name in the chain is frequently unconfigured.
    """
    for provider in _provider_order():
        env = _PROVIDER_API_KEY_ENV.get(provider)
        if env and os.environ.get(env):
            return provider
    return None


def _provider_order() -> list[str]:
    raw = os.environ.get("LLM_PROVIDER_ORDER", "")
    if not raw.strip():
        return list(DEFAULT_PROVIDER_ORDER)
    return [p.strip().lower() for p in raw.split(",") if p.strip()]


def _split_system(messages: list[dict]) -> tuple[str, list[dict]]:
    system_parts = [m["content"] for m in messages if m["role"] == "system"]
    turns = [m for m in messages if m["role"] != "system"]
    return "\n\n".join(system_parts), turns


def _reasoning_tokens(resp) -> int:
    """Tokens the model spent thinking rather than answering, if reported."""
    usage = getattr(resp, "usage", None)
    details = getattr(usage, "completion_tokens_details", None) if usage else None
    return int(getattr(details, "reasoning_tokens", 0) or 0)


def _groq_kwargs(model, json_mode) -> dict:
    kwargs = {"response_format": {"type": "json_object"}} if json_mode else {}
    effort = groq_reasoning_effort(model)
    if effort:
        kwargs["reasoning_effort"] = effort
    return kwargs


def _check_groq_completion(resp, max_tokens: int) -> str:
    """The answer text, or a failure that names what actually went wrong.

    "Groq returned an empty response" was true and useless. A reasoning model
    bills its thinking against ``max_tokens`` without any of it reaching
    ``content``, so a ceiling that reasoning exhausts produces an empty answer
    from a request that succeeded — and the fix is the reasoning budget, not
    the API key, the prompt, or a retry, which is everything the old message
    sent a reader to check.
    """
    choice = resp.choices[0]
    text = choice.message.content
    truncated = getattr(choice, "finish_reason", "") == "length"
    reasoning = _reasoning_tokens(resp)

    if not text or not text.strip():
        if truncated:
            raise RuntimeError(
                f"Groq spent the whole {max_tokens}-token completion budget on reasoning "
                f"({reasoning} tokens) and returned no answer. Lower reasoning_effort for "
                "this model in tools/llm_provider_models.py, or raise the budget"
            )
        raise RuntimeError("Groq returned an empty response")

    if truncated:
        # Non-fatal: a cut-off brief still beats no brief, and the caller has
        # nowhere better to fall back to. But it is a real defect in the output
        # and must not pass silently just because the string is non-empty.
        logger.warning(
            "Groq answer was cut off at the %s-token ceiling (%s spent on reasoning); "
            "the text is incomplete", max_tokens, reasoning,
        )
    return text.strip()


def _call_groq(model, messages, temperature, max_tokens, json_mode):
    from groq import Groq

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY not set")
    client = Groq(api_key=api_key)
    resp = client.chat.completions.create(
        model=model, messages=messages, temperature=temperature, max_tokens=max_tokens,
        **_groq_kwargs(model, json_mode),
    )
    return Completion(_check_groq_completion(resp, max_tokens), *_usage_openai_style(resp))


def _call_anthropic(model, messages, temperature, max_tokens, json_mode):
    from anthropic import Anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    client = Anthropic(api_key=api_key)
    system, turns = _split_system(messages)
    resp = client.messages.create(
        model=model, max_tokens=max_tokens, temperature=temperature,
        system=system, messages=turns,
    )
    text = "".join(
        block.text for block in resp.content if getattr(block, "type", "") == "text"
    )
    if not text.strip():
        raise RuntimeError("Anthropic returned an empty response")
    return Completion(text.strip(), *_usage_anthropic(resp))


def _call_openai_compatible(model, messages, temperature, max_tokens, json_mode, *, api_key_env, base_url=None):
    from openai import OpenAI

    api_key = os.environ.get(api_key_env)
    if not api_key:
        raise RuntimeError(f"{api_key_env} not set")
    client = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)
    kwargs = {"response_format": {"type": "json_object"}} if json_mode else {}
    resp = client.chat.completions.create(
        model=model, messages=messages, temperature=temperature, max_tokens=max_tokens, **kwargs,
    )
    text = resp.choices[0].message.content
    if not text or not text.strip():
        raise RuntimeError(f"{api_key_env} provider returned an empty response")
    return Completion(text.strip(), *_usage_openai_style(resp))


def _call_openai(model, messages, temperature, max_tokens, json_mode):
    return _call_openai_compatible(
        model, messages, temperature, max_tokens, json_mode, api_key_env="OPENAI_API_KEY",
    )


def _call_openrouter(model, messages, temperature, max_tokens, json_mode):
    return _call_openai_compatible(
        model, messages, temperature, max_tokens, json_mode,
        api_key_env="OPENROUTER_API_KEY", base_url="https://openrouter.ai/api/v1",
    )


_HANDLERS = {
    "groq": _call_groq,
    "anthropic": _call_anthropic,
    "openai": _call_openai,
    "openrouter": _call_openrouter,
}


# ── Streaming variants ───────────────────────────────────────────────────────
# Token-by-token variants of the handlers above, used by ``stream_complete``.
# They intentionally never take ``json_mode`` (a streamed JSON object is useless
# to a UI rendering it as it arrives) and yield only non-empty text deltas.
#
# Each takes a ``_UsageBox`` it fills in when the stream ends, because a
# generator has no way to hand a return value back to the code driving it.


def _open_usage_stream(create, **kwargs):
    """Start a stream that also reports token usage on its final chunk.

    OpenAI-compatible APIs send a usage chunk only when asked via
    ``stream_options``. Without it a streamed call reports nothing at all —
    which is how a token dashboard silently under-counts its single most
    expensive surface, the copilot, while looking perfectly healthy.

    Not every OpenAI-compatible deployment accepts the parameter, and a
    rejection here would push the whole request onto the next provider in the
    fallback chain — a real answer swapped for a different model because of a
    bookkeeping flag. So a rejection degrades to an unmetered stream instead.
    """
    try:
        return create(stream=True, stream_options={"include_usage": True}, **kwargs)
    except TypeError:
        # SDK too old to know the parameter.
        return create(stream=True, **kwargs)
    except Exception as exc:
        if "stream_options" not in str(exc):
            raise
        logger.info("Provider rejected stream_options; streaming without usage reporting")
        return create(stream=True, **kwargs)


def _absorb_stream_usage(chunk, usage: _UsageBox) -> None:
    """Copy token counts off a stream chunk, if it carries any.

    The usage chunk arrives last and has an **empty** ``choices`` list, so it
    has to be read before the ``if not chunk.choices: continue`` guard skips
    it. That skip is precisely why streamed calls used to report nothing.

    Groq mirrors the same numbers under ``x_groq.usage``, so both shapes are
    checked rather than assuming the OpenAI layout everywhere.
    """
    candidate = getattr(chunk, "usage", None)
    if candidate is None:
        x_groq = getattr(chunk, "x_groq", None)
        candidate = getattr(x_groq, "usage", None) if x_groq is not None else None
    if candidate is None:
        return
    prompt = int(getattr(candidate, "prompt_tokens", 0) or 0)
    completion = int(getattr(candidate, "completion_tokens", 0) or 0)
    total = int(getattr(candidate, "total_tokens", 0) or 0)
    # Never overwrite a real count with a zero: some providers send several
    # partially-populated usage frames rather than one complete one.
    usage.prompt_tokens = prompt or usage.prompt_tokens
    usage.completion_tokens = completion or usage.completion_tokens
    usage.total_tokens = total or usage.total_tokens


def _stream_groq(model, messages, temperature, max_tokens, usage: _UsageBox) -> Iterator[str]:
    from groq import Groq

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY not set")
    client = Groq(api_key=api_key)
    stream = _open_usage_stream(
        client.chat.completions.create,
        model=model, messages=messages, temperature=temperature,
        max_tokens=max_tokens, **_groq_kwargs(model, json_mode=False),
    )
    for chunk in stream:
        _absorb_stream_usage(chunk, usage)
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta


def _stream_anthropic(model, messages, temperature, max_tokens, usage: _UsageBox) -> Iterator[str]:
    from anthropic import Anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    client = Anthropic(api_key=api_key)
    system, turns = _split_system(messages)
    with client.messages.stream(
        model=model, max_tokens=max_tokens, temperature=temperature,
        system=system, messages=turns,
    ) as stream:
        for text in stream.text_stream:
            if text:
                yield text
        # Anthropic reports usage on the assembled final message rather than on
        # a trailing chunk. This is only reachable when the stream ran to
        # completion; an abandoned generator never gets here, which is why
        # ``stream_complete`` still records a (zero-token) cancelled event.
        try:
            usage.prompt_tokens, usage.completion_tokens, usage.total_tokens = _usage_anthropic(
                stream.get_final_message()
            )
        except Exception:
            logger.debug("Anthropic final message unavailable; stream left unmetered", exc_info=True)


def _stream_openai_compatible(model, messages, temperature, max_tokens, usage: _UsageBox, *, api_key_env, base_url=None) -> Iterator[str]:
    from openai import OpenAI

    api_key = os.environ.get(api_key_env)
    if not api_key:
        raise RuntimeError(f"{api_key_env} not set")
    client = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)
    stream = _open_usage_stream(
        client.chat.completions.create,
        model=model, messages=messages, temperature=temperature,
        max_tokens=max_tokens,
    )
    for chunk in stream:
        _absorb_stream_usage(chunk, usage)
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta


def _stream_openai(model, messages, temperature, max_tokens, usage: _UsageBox) -> Iterator[str]:
    yield from _stream_openai_compatible(
        model, messages, temperature, max_tokens, usage, api_key_env="OPENAI_API_KEY",
    )


def _stream_openrouter(model, messages, temperature, max_tokens, usage: _UsageBox) -> Iterator[str]:
    yield from _stream_openai_compatible(
        model, messages, temperature, max_tokens, usage,
        api_key_env="OPENROUTER_API_KEY", base_url="https://openrouter.ai/api/v1",
    )


_STREAM_HANDLERS = {
    "groq": _stream_groq,
    "anthropic": _stream_anthropic,
    "openai": _stream_openai,
    "openrouter": _stream_openrouter,
}


# ── Request sizing ───────────────────────────────────────────────────────────


def _retry_ceiling_for(exc: Exception, provider: str, ceiling: int) -> int | None:
    """Completion ceiling to retry a refused request with, or ``None``.

    ``None`` and a raise mean different things here, so this returns ``None``
    only when a retry is genuinely pointless; a refusal that shrinking cannot
    fix is re-raised as a readable sentence instead of the provider's JSON.
    """
    report = parse_token_limit(exc)
    if report is None:
        raise exc
    retry_ceiling = fit_from_reported(report, ceiling)
    if retry_ceiling is None:
        raise RuntimeError(describe_token_limit(report, ceiling)) from exc
    if retry_ceiling >= ceiling:
        raise exc
    logger.info(
        "%s refused the request (%s budget: %s of %s used, %s requested); "
        "retrying with %s completion tokens",
        provider, report.window(), report.used or "n/a", report.limit,
        report.requested, retry_ceiling,
    )
    return retry_ceiling


def _call_within_budget(handler, provider, model, messages, temperature, max_tokens, json_mode):
    """One provider attempt, sized to that provider's token budget.

    Two sizing passes at most. The first uses a local estimate of the prompt.
    If that estimate was optimistic — or the window has been partly spent by
    earlier calls, which no local estimate can know — the provider refuses with
    its own tokenizer's numbers, and the second pass is sized from those. A
    third would be pointless: the second already uses the largest ceiling the
    provider itself says fits.
    """
    ceiling = fit_max_tokens(messages, max_tokens, limit=provider_tpm_limit(provider))
    if ceiling < max_tokens:
        logger.info(
            "Sizing %s request to %s completion tokens (asked %s) to fit its token budget",
            provider, ceiling, max_tokens,
        )
    try:
        return handler(model, messages, temperature, ceiling, json_mode)
    except Exception as exc:
        retry_ceiling = _retry_ceiling_for(exc, provider, ceiling)
        return handler(model, messages, temperature, retry_ceiling, json_mode)


def _stream_within_budget(handler, provider, model, messages, temperature, max_tokens, usage):
    """Streaming counterpart of :func:`_call_within_budget`.

    The resize is only attempted before the first delta. Once tokens have
    reached the caller, restarting the stream would splice two answers together
    — the same reason a mid-stream failure never falls back to another provider.
    """
    ceiling = fit_max_tokens(messages, max_tokens, limit=provider_tpm_limit(provider))
    stream = handler(model, messages, temperature, ceiling, usage)
    try:
        first = next(stream)
    except StopIteration:
        return
    except Exception as exc:
        retry_ceiling = _retry_ceiling_for(exc, provider, ceiling)
        yield from handler(model, messages, temperature, retry_ceiling, usage)
        return
    yield first
    yield from stream


def stream_complete(
    purpose: str,
    messages: list[dict],
    *,
    model: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 2048,
) -> Iterator[str]:
    """Stream a chat completion as text deltas, with the same provider-fallback
    chain as ``complete``.

    Fallback semantics differ from ``complete`` in one important way: once a
    provider has emitted its first token, a later failure in that provider can
    NOT silently fall back to the next one — doing so would splice two different
    answers together mid-sentence. So a mid-stream error is re-raised; only a
    failure *before* any token is produced (auth, rate-limit, empty stream)
    advances to the next provider. Raises ``AllProvidersFailedError`` if every
    configured provider fails before producing a token.
    """
    errors: list[str] = []
    for depth, provider in enumerate(_provider_order()):
        handler = _STREAM_HANDLERS.get(provider)
        if handler is None:
            continue
        if provider == "groq":
            provider_model = model or None
        else:
            provider_model = PROVIDER_MODELS.get(provider, {}).get(purpose)
        if not provider_model:
            continue
        usage = _UsageBox()
        started = time.monotonic()
        produced = False
        status: str | None = None
        failure: str | None = None
        try:
            for delta in _stream_within_budget(
                handler, provider, provider_model, messages, temperature, max_tokens, usage,
            ):
                if not delta:
                    continue
                produced = True
                yield delta
            if produced:
                status = "ok"
                logger.info(
                    "LLM stream purpose=%s served by provider=%s model=%s tokens=%s",
                    purpose, provider, provider_model, usage.total_tokens or "unreported",
                )
                return
            status, failure = "error", "empty stream"
            errors.append(f"{provider}: empty stream")
        except Exception as exc:
            status, failure = "error", str(exc)
            if produced:
                logger.warning(
                    "LLM stream provider=%s failed mid-answer for purpose=%s: %s",
                    provider, purpose, exc,
                )
                raise
            errors.append(f"{provider}: {exc}")
            logger.warning(
                "LLM stream provider=%s failed for purpose=%s: %s", provider, purpose, exc
            )
            continue
        finally:
            # Runs on every exit from this attempt — success, failure, and the
            # ``return`` above. ``status`` is still None only when the consumer
            # closed the generator mid-answer (the copilot's stop button), which
            # raises GeneratorExit at the ``yield`` and never reaches any branch
            # that sets it. Those tokens were spent and are worth recording even
            # though the provider never got to report a count.
            record(UsageEvent(
                purpose=purpose, provider=provider, model=provider_model,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                total_tokens=usage.total_tokens,
                latency_ms=int((time.monotonic() - started) * 1000),
                streamed=True,
                status=status or "cancelled",
                error=failure,
                fallback_depth=depth,
            ))
    raise AllProvidersFailedError(
        f"All LLM providers failed for purpose={purpose}: {'; '.join(errors) or 'no provider configured'}"
    )


def complete(
    purpose: str,
    messages: list[dict],
    *,
    model: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 2048,
    json_mode: bool = False,
) -> str:
    """Complete a chat prompt, falling back across providers on failure.

    ``model`` is honored ONLY for the Groq leg. Every other provider
    resolves its own model for ``purpose`` from ``PROVIDER_MODELS``.
    Raises ``AllProvidersFailedError`` if every configured provider fails.
    """
    errors: list[str] = []
    for depth, provider in enumerate(_provider_order()):
        handler = _HANDLERS.get(provider)
        if handler is None:
            continue
        if provider == "groq":
            provider_model = model or None
        else:
            provider_model = PROVIDER_MODELS.get(provider, {}).get(purpose)
        if not provider_model:
            continue
        started = time.monotonic()
        try:
            result = _call_within_budget(
                handler, provider, provider_model, messages, temperature, max_tokens, json_mode,
            )
            record(UsageEvent(
                purpose=purpose, provider=provider, model=provider_model,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                total_tokens=result.total_tokens,
                latency_ms=int((time.monotonic() - started) * 1000),
                fallback_depth=depth,
            ))
            logger.info(
                "LLM call purpose=%s served by provider=%s model=%s tokens=%s",
                purpose, provider, provider_model, result.total_tokens or "unreported",
            )
            return result.text
        except Exception as exc:
            # A failed attempt still costs wall-clock time and often real tokens
            # upstream, and a chain that fails over on every call is something
            # the usage dashboard should make visible rather than hide.
            record(UsageEvent(
                purpose=purpose, provider=provider, model=provider_model,
                latency_ms=int((time.monotonic() - started) * 1000),
                status="error", error=str(exc), fallback_depth=depth,
            ))
            errors.append(f"{provider}: {exc}")
            logger.warning(
                "LLM provider=%s failed for purpose=%s: %s", provider, purpose, exc
            )
            continue
    raise AllProvidersFailedError(
        f"All LLM providers failed for purpose={purpose}: {'; '.join(errors) or 'no provider configured'}"
    )
