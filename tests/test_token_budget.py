"""Request sizing against a provider's per-minute token budget.

The bug these pin down produced a user-visible "Something went wrong" on every
insights report:

    AllProvidersFailedError: All LLM providers failed for purpose=insights:
    groq: Error code: 413 ... on tokens per minute (TPM):
    Limit 8000, Requested 9047

Groq bills a completion as ``prompt + max_tokens``, so a 3,000-token
reservation on top of a ~6,000-token prompt is rejected before the model runs —
and 413 is not 429, so no amount of retrying or falling over to another provider
could ever have fixed it. Only a smaller request does.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tools.llm_client import AllProvidersFailedError, complete
from tools.token_budget import (
    MIN_COMPLETION_TOKENS,
    describe_token_limit,
    estimate_tokens,
    fit_from_reported,
    fit_max_tokens,
    is_token_limit_error,
    parse_token_limit,
    prompt_budget,
    provider_tpm_limit,
)

_GROQ_413 = (
    "Error code: 413 - {'error': {'message': 'Request too large for model "
    "`openai/gpt-oss-120b` in organization `org_x` service tier `on_demand` on "
    "tokens per minute (TPM): Limit 8000, Requested 9047', 'type': 'tokens', "
    "'code': 'rate_limit_exceeded'}}"
)

# The daily window, overshot by 427 tokens — a request that a slightly shorter
# answer would have got through.
_GROQ_429_NEAR_MISS = (
    "Error code: 429 - {'error': {'message': 'Rate limit reached for model "
    "`openai/gpt-oss-120b` in organization `org_x` service tier `on_demand` on "
    "tokens per day (TPD): Limit 200000, Used 194314, Requested 6113. Please try "
    "again in 3m4.464s.', 'type': 'tokens', 'code': 'rate_limit_exceeded'}}"
)

# The same window with nothing usable left in it.
_GROQ_429_SPENT = (
    "Error code: 429 - {'error': {'message': 'Rate limit reached for model "
    "`openai/gpt-oss-120b` in organization `org_x` service tier `on_demand` on "
    "tokens per day (TPD): Limit 200000, Used 199298, Requested 4716. Please try "
    "again in 28m54.048s.', 'type': 'tokens', 'code': 'rate_limit_exceeded'}}"
)


class TestEstimation:
    def test_estimate_errs_high_rather_than_low(self) -> None:
        """Under-estimating the prompt is what produces the 413; over-estimating
        costs a slightly shorter answer. Only one of those is recoverable."""
        text = "word " * 1000
        assert estimate_tokens(text) > len(text) / 4

    def test_empty_text_costs_nothing(self) -> None:
        assert estimate_tokens("") == 0


class TestProviderLimits:
    def test_groq_has_a_modelled_ceiling(self) -> None:
        assert provider_tpm_limit("groq") == 8000

    def test_providers_without_a_modelled_ceiling_are_unlimited(self) -> None:
        """Guessing low for a paid tier would truncate answers for no reason."""
        assert provider_tpm_limit("anthropic") == 0

    def test_the_ceiling_is_configurable_for_an_upgraded_plan(self) -> None:
        with patch.dict("os.environ", {"LLM_TPM_LIMIT_GROQ": "300000"}):
            assert provider_tpm_limit("groq") == 300000

    def test_a_malformed_override_falls_back_to_the_default(self) -> None:
        with patch.dict("os.environ", {"LLM_TPM_LIMIT_GROQ": "lots"}):
            assert provider_tpm_limit("groq") == 8000


class TestFittingTheReservation:
    def test_a_request_that_already_fits_is_left_alone(self) -> None:
        messages = [{"role": "user", "content": "hi"}]
        assert fit_max_tokens(messages, 2000, limit=8000) == 2000

    def test_an_unlimited_provider_never_shrinks_a_request(self) -> None:
        messages = [{"role": "user", "content": "x" * 100_000}]
        assert fit_max_tokens(messages, 4096, limit=0) == 4096

    def test_an_oversized_reservation_is_cut_to_fit(self) -> None:
        messages = [{"role": "user", "content": "x" * 21_600}]  # ~6,000 tokens
        fitted = fit_max_tokens(messages, 3000, limit=8000)
        assert fitted < 3000
        assert estimate_tokens(messages[0]["content"]) + fitted < 8000

    def test_shrinking_stops_before_the_answer_becomes_a_stub(self) -> None:
        """Past this floor the prompt is the problem, and a shorter answer only
        turns a clear error into a mangled report."""
        messages = [{"role": "user", "content": "x" * 200_000}]
        assert fit_max_tokens(messages, 3000, limit=8000) == MIN_COMPLETION_TOKENS


class TestReadingTheProvidersOwnNumbers:
    def test_a_413_reports_a_limit_with_nothing_consumed(self) -> None:
        report = parse_token_limit(RuntimeError(_GROQ_413))
        assert (report.limit, report.used, report.requested) == (8000, 0, 9047)
        assert report.scope == "minute"
        assert not report.exhausted
        assert is_token_limit_error(RuntimeError(_GROQ_413))

    def test_a_429_reports_what_the_window_has_already_spent(self) -> None:
        report = parse_token_limit(RuntimeError(_GROQ_429_NEAR_MISS))
        assert (report.limit, report.used, report.requested) == (200_000, 194_314, 6113)
        assert report.scope == "day"
        assert report.exhausted
        assert report.headroom == 5686
        assert report.retry_after == "3m4.464s"

    def test_an_unrelated_failure_is_not_mistaken_for_a_budget_refusal(self) -> None:
        assert parse_token_limit(RuntimeError("Connection reset by peer")) is None
        assert not is_token_limit_error(RuntimeError("GROQ_API_KEY not set"))

    def test_a_retry_is_sized_from_the_tokenizers_verdict_not_an_estimate(self) -> None:
        """`requested - max_tokens` is the prompt as the provider counted it, so
        the retry ceiling needs no estimating at all."""
        report = parse_token_limit(RuntimeError(_GROQ_413))
        assert fit_from_reported(report, 3000) == 8000 - 6047 - 64

    def test_a_partly_spent_window_is_sized_against_what_is_left(self) -> None:
        """Not the limit — the remaining headroom. A refusal that overshot by
        427 tokens goes through at a shorter answer instead of failing."""
        report = parse_token_limit(RuntimeError(_GROQ_429_NEAR_MISS))
        assert fit_from_reported(report, 4096) == 5686 - 2017 - 64

    def test_no_ceiling_rescues_a_prompt_that_is_itself_over_budget(self) -> None:
        huge = _GROQ_413.replace("Requested 9047", "Requested 12000")
        assert fit_from_reported(parse_token_limit(RuntimeError(huge)), 1000) is None

    def test_no_ceiling_rescues_a_window_with_nothing_left(self) -> None:
        report = parse_token_limit(RuntimeError(_GROQ_429_SPENT))
        assert fit_from_reported(report, 2048) is None


class TestTheRefusalIsExplainedInWords:
    """The two refusals need opposite advice, and the provider's raw JSON gives
    neither. Telling someone to wait for an over-large single request would have
    them wait forever."""

    def test_a_spent_window_is_described_as_a_quota_to_wait_out(self) -> None:
        message = describe_token_limit(parse_token_limit(RuntimeError(_GROQ_429_SPENT)), 2048)
        assert "per-day" in message
        assert "199,298 of 200,000" in message
        assert "28m54.048s" in message

    def test_an_over_large_request_is_described_as_a_prompt_to_shrink(self) -> None:
        message = describe_token_limit(parse_token_limit(RuntimeError(_GROQ_413)), 3000)
        assert "Reduce the context sent" in message
        assert "resets" not in message


class TestPromptBudget:
    def test_a_prompt_builder_is_told_what_it_may_spend(self) -> None:
        assert prompt_budget("groq", 2400) == int(8000 * 0.94) - 2400

    def test_an_unlimited_provider_imposes_no_prompt_budget(self) -> None:
        assert prompt_budget("openai", 2400) == 0


class TestClientBehaviourOnAnOversizeRejection:
    """A 413 is a sizing failure, not a provider failure. Falling over to the
    next provider answers the wrong question — and when no other provider is
    configured, it turns a fixable request into a failed report."""

    def _groq_only(self):
        return patch.dict(
            "os.environ", {"GROQ_API_KEY": "k", "LLM_PROVIDER_ORDER": "groq"}, clear=False,
        )

    def test_the_same_provider_is_retried_with_a_reservation_that_fits(self) -> None:
        seen: list[int] = []

        def _handler(model, messages, temperature, max_tokens, json_mode):
            seen.append(max_tokens)
            if len(seen) == 1:
                raise RuntimeError(_GROQ_413)
            from tools.llm_client import Completion
            return Completion("the report", 1, 2, 3)

        with self._groq_only(), patch.dict("tools.llm_client._HANDLERS", {"groq": _handler}):
            assert complete(
                "insights", [{"role": "user", "content": "x"}],
                model="openai/gpt-oss-120b", max_tokens=3000,
            ) == "the report"

        assert len(seen) == 2
        assert seen[1] < seen[0]

    def test_a_spent_daily_quota_is_retried_into_the_headroom_that_remains(self) -> None:
        """The per-minute estimate cannot know what earlier calls already spent
        of the day, so only the provider's refusal reveals it."""
        seen: list[int] = []

        def _handler(model, messages, temperature, max_tokens, json_mode):
            seen.append(max_tokens)
            if len(seen) == 1:
                raise RuntimeError(_GROQ_429_NEAR_MISS)
            from tools.llm_client import Completion
            return Completion("the strategy", 1, 2, 3)

        with self._groq_only(), patch.dict("tools.llm_client._HANDLERS", {"groq": _handler}):
            assert complete(
                "marketing", [{"role": "user", "content": "x"}],
                model="openai/gpt-oss-120b", max_tokens=4096,
            ) == "the strategy"

        assert len(seen) == 2
        assert seen[1] < seen[0]

    def test_an_exhausted_quota_says_so_instead_of_retrying_into_the_same_wall(self) -> None:
        def _handler(model, messages, temperature, max_tokens, json_mode):
            raise RuntimeError(_GROQ_429_SPENT)

        with (
            self._groq_only(),
            patch.dict("tools.llm_client._HANDLERS", {"groq": _handler}),
            pytest.raises(AllProvidersFailedError) as excinfo,
        ):
            complete(
                "marketing", [{"role": "user", "content": "x"}],
                model="openai/gpt-oss-120b", max_tokens=2048,
            )

        message = str(excinfo.value)
        assert "quota is spent" in message
        assert "28m54.048s" in message

    def test_an_unfixable_oversize_reports_the_prompt_not_the_raw_413(self) -> None:
        """When the prompt alone is over budget, telling the user to shorten the
        answer is telling them to do the one thing that cannot work."""
        limit_only = _GROQ_413.replace("Requested 9047", "Requested 40000")

        def _handler(model, messages, temperature, max_tokens, json_mode):
            raise RuntimeError(limit_only)

        with (
            self._groq_only(),
            patch.dict("tools.llm_client._HANDLERS", {"groq": _handler}),
            pytest.raises(AllProvidersFailedError) as excinfo,
        ):
            complete(
                "insights", [{"role": "user", "content": "x"}],
                model="openai/gpt-oss-120b", max_tokens=3000,
            )

        message = str(excinfo.value)
        assert "Reduce the context sent" in message
        assert "LLM_TPM_LIMIT" in message

    def test_an_ordinary_failure_still_falls_through_to_the_next_provider(self) -> None:
        def _boom(*a, **kw):
            raise RuntimeError("Connection reset by peer")

        from tools.llm_client import Completion

        def _ok(model, messages, temperature, max_tokens, json_mode):
            return Completion("fallback answer", 1, 2, 3)

        env = {"GROQ_API_KEY": "k", "OPENAI_API_KEY": "k", "LLM_PROVIDER_ORDER": "groq,openai"}
        with patch.dict("os.environ", env, clear=False), patch.dict(
            "tools.llm_client._HANDLERS", {"groq": _boom, "openai": _ok},
        ):
            assert complete(
                "insights", [{"role": "user", "content": "x"}], model="openai/gpt-oss-120b",
            ) == "fallback answer"
