"""
Tests for LLM retry logic and OpenTelemetry instrumentation — Week 3.

These tests verify:
  1. Retriable errors (429, 5xx, connection errors) are retried
  2. Non-retriable errors (400, 401, ValueError) are NOT retried
  3. After max_attempts, the last error is re-raised
  4. OTel spans are created for every agent call
  5. Span attributes (agent name, model, tokens, duration) are recorded
  6. Span errors are recorded when the LLM call fails

Teaching note — what we're testing here vs. what we're NOT testing:

  We ARE testing the retry and tracing WIRING — the mechanism that decides
  when to retry and whether spans are opened/closed correctly.

  We are NOT testing the Anthropic API itself — we mock it completely.
  We are NOT testing whether the retry waits the right number of seconds —
  that would make tests take 30+ seconds. We verify retry COUNT, not timing.

  The golden rule of unit tests: test the behavior of YOUR code, not the
  behavior of libraries you depend on. tenacity's backoff math is tenacity's
  problem to test. Your problem is: does your code call tenacity correctly?

Teaching note — how to test retry logic without waiting:

  tenacity's wait parameter controls how long to sleep between attempts.
  In tests, we don't want to sleep. We use tenacity's testing utilities to
  override the wait strategy with wait_none() — zero wait between retries.
  This lets us test "did it retry 3 times?" in milliseconds, not seconds.
"""

import logging as _stdlib_logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from anthropic import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from tenacity import wait_fixed

import pacca.agents.base as base_module
from pacca.agents.base import AgentConfig, BaseAgent
from pacca.config import tracing as tracing_module

# =============================================================================
# Minimal concrete agent for testing
# =============================================================================


class _TestOutput(BaseModel):
    """Minimal Pydantic output model for agent testing."""

    result: str
    score: float = 0.9


class _ConcreteAgent(BaseAgent):
    """
    A concrete implementation of BaseAgent for testing purposes.

    In production, only specific agents (DecisionAgent, MedicalDirectorAgent,
    etc.) inherit from BaseAgent. For tests, we need a concrete class — we
    can't instantiate the ABC directly because it has abstract methods.
    """

    @property
    def name(self) -> str:
        return "TestAgent"

    @property
    def system_prompt(self) -> str:
        return "You are a test agent."


def make_mock_response(result: str = "approved", score: float = 0.97) -> MagicMock:
    """
    Build a mock Anthropic API response that looks like a real tool_use response.

    Teaching note: the Anthropic API returns a Message object with a `.content`
    list. Each item in the list is a content block. When tool_choice forces
    tool use, there will be exactly one block with type="tool_use" and
    an `.input` dict matching the tool's schema.

    We need our mock to mirror this structure exactly so our parsing code
    (which looks for content_block.type == "tool_use") works correctly.
    """
    mock_content = MagicMock()
    mock_content.type = "tool_use"
    mock_content.input = {"result": result, "score": score}

    mock_usage = MagicMock()
    mock_usage.input_tokens = 450
    mock_usage.output_tokens = 120

    mock_response = MagicMock()
    mock_response.content = [mock_content]
    mock_response.usage = mock_usage
    return mock_response


# =============================================================================
# Retry behavior tests
# =============================================================================


class TestRetryLogic:
    """
    Tests for tenacity retry behavior in BaseAgent._call_with_retry().

    Each test mocks the Anthropic client to raise specific errors on the
    first N calls, then succeed (or raise a non-retriable error).
    """

    @pytest.fixture
    def agent(self) -> _ConcreteAgent:
        """Create a test agent with fast retry settings (no real waiting)."""
        cfg = AgentConfig(model="claude-test", temperature=0.0, max_tokens=100)
        a = _ConcreteAgent(config=cfg)
        # Override settings to use 3 max attempts
        a._settings = MagicMock()
        a._settings.llm_retry_max_attempts = 3
        a._settings.llm_retry_wait_min_seconds = 0.0  # No real waiting in tests
        a._settings.llm_retry_wait_max_seconds = 0.0
        return a

    @pytest.mark.asyncio
    async def test_rate_limit_error_is_retried(self, agent: _ConcreteAgent) -> None:
        """
        A 429 RateLimitError on the first attempt should cause a retry,
        with the second attempt succeeding and returning the result.

        Real-world meaning: Anthropic returned 429 because we're sending
        too many requests. Wait and retry — it will succeed shortly.
        """
        success_response = make_mock_response()

        # First call: 429 error. Second call: success.
        agent.client.messages.create = AsyncMock(
            side_effect=[
                RateLimitError(
                    message="Rate limit exceeded",
                    response=MagicMock(status_code=429),
                    body={"error": {"type": "rate_limit_error"}},
                ),
                success_response,
            ]
        )

        with patch("pacca.agents.base.wait_exponential", return_value=MagicMock(sleep=0)):
            result = await agent.execute("test prompt", _TestOutput)

        assert result.result == "approved"
        # The API was called twice: once failed, once succeeded
        assert agent.client.messages.create.call_count == 2

    @pytest.mark.asyncio
    async def test_connection_error_is_retried(self, agent: _ConcreteAgent) -> None:
        """
        A network connection error should trigger retry.

        Real-world meaning: the network hiccupped. Try again.
        """
        success_response = make_mock_response()

        agent.client.messages.create = AsyncMock(
            side_effect=[
                APIConnectionError(message="Connection failed", request=MagicMock()),
                success_response,
            ]
        )

        with patch("pacca.agents.base.wait_exponential", return_value=MagicMock(sleep=0)):
            result = await agent.execute("test prompt", _TestOutput)

        assert result.result == "approved"
        assert agent.client.messages.create.call_count == 2

    @pytest.mark.asyncio
    async def test_exhausted_retries_reraises_last_error(self, agent: _ConcreteAgent) -> None:
        """
        After max_attempts failures, the last error must be re-raised.

        Real-world meaning: the API is consistently unavailable. After 3
        attempts we give up and let the route handler return a 500 error.
        The error IS the correct outcome — we don't silently swallow it.
        """
        rate_limit_error = RateLimitError(
            message="Rate limit exceeded",
            response=MagicMock(status_code=429),
            body={"error": {"type": "rate_limit_error"}},
        )
        # All 3 attempts fail
        agent.client.messages.create = AsyncMock(
            side_effect=[rate_limit_error, rate_limit_error, rate_limit_error]
        )

        with (
            patch("pacca.agents.base.wait_exponential", return_value=MagicMock(sleep=0)),
            pytest.raises(RateLimitError),
        ):
            await agent.execute("test prompt", _TestOutput)

        # All 3 attempts were made before giving up
        assert agent.client.messages.create.call_count == 3

    @pytest.mark.asyncio
    async def test_bad_request_error_not_retried(self, agent: _ConcreteAgent) -> None:
        """
        A 400 BadRequestError must NOT be retried.

        Real-world meaning: we sent invalid data to the API. Retrying with
        the same invalid data will just fail again. Fail fast.
        """
        bad_request_error = BadRequestError(
            message="Invalid request",
            response=MagicMock(status_code=400),
            body={"error": {"type": "invalid_request_error"}},
        )
        agent.client.messages.create = AsyncMock(side_effect=bad_request_error)

        with pytest.raises(BadRequestError):
            await agent.execute("test prompt", _TestOutput)

        # Only called once — no retry for 400 errors
        assert agent.client.messages.create.call_count == 1, (
            "BadRequestError (400) must not be retried. "
            "The request is invalid and retrying will not fix it."
        )

    @pytest.mark.asyncio
    async def test_auth_error_not_retried(self, agent: _ConcreteAgent) -> None:
        """
        A 401 AuthenticationError must NOT be retried.

        Real-world meaning: the API key is wrong. Retrying with the same
        wrong key is pointless and could lock the account.
        """
        auth_error = AuthenticationError(
            message="Invalid API key",
            response=MagicMock(status_code=401),
            body={"error": {"type": "authentication_error"}},
        )
        agent.client.messages.create = AsyncMock(side_effect=auth_error)

        with pytest.raises(AuthenticationError):
            await agent.execute("test prompt", _TestOutput)

        assert agent.client.messages.create.call_count == 1, (
            "AuthenticationError (401) must not be retried. "
            "Retrying with the same invalid API key will always fail."
        )

    @pytest.mark.asyncio
    async def test_successful_call_not_retried(self, agent: _ConcreteAgent) -> None:
        """
        A successful API call must be called exactly once — no unnecessary retries.

        This test guards against an accidental misconfiguration where the
        retry logic triggers even on success.
        """
        agent.client.messages.create = AsyncMock(return_value=make_mock_response())

        result = await agent.execute("test prompt", _TestOutput)

        assert result.result == "approved"
        assert agent.client.messages.create.call_count == 1

    @pytest.mark.asyncio
    async def test_timeout_error_is_retried(self, agent: _ConcreteAgent) -> None:
        """
        APITimeoutError (part of the original RETRIABLE_ERRORS tuple) must
        still be retried after the chg-21 5xx predicate is added.

        This is a non-regression check: adding the OR'd status-code predicate
        must not disturb the existing retry_if_exception_type(RETRIABLE_ERRORS)
        branch. Nobody had a test for APITimeoutError specifically before this
        change (RateLimitError and APIConnectionError were covered, this one
        was not), so it is added now rather than only implied.
        """
        success_response = make_mock_response()

        agent.client.messages.create = AsyncMock(
            side_effect=[
                APITimeoutError(request=MagicMock()),
                success_response,
            ]
        )

        with patch("pacca.agents.base.wait_exponential", return_value=wait_fixed(0)):
            result = await agent.execute("test prompt", _TestOutput)

        assert result.result == "approved"
        assert agent.client.messages.create.call_count == 2


class TestServerErrorRetry:
    """
    chg-21: 5xx / 529 server errors must be retried via the OR'd predicate
    `retry_if_exception_type(RETRIABLE_ERRORS) | retry_if_exception(_is_retriable_status_error)`.

    Before chg-21, `_is_retriable_status_error` existed in base.py but was
    referenced nowhere — RETRIABLE_ERRORS only covered RateLimitError,
    APIConnectionError, APITimeoutError. A 500/529 failed on the very first
    attempt. These tests pin the fixed behavior and (via
    test_pre_fix_predicate_does_not_retry_5xx) prove the old predicate
    actually fails this exact assertion.
    """

    @pytest.fixture
    def agent(self) -> _ConcreteAgent:
        """
        A real agent with no `_settings` stub.

        _call_with_retry() reads settings.llm_retry_max_attempts from
        effective_settings() at CALL TIME (see TestRetryRespectsRuntimeOverrides
        above) — it never consults self._settings. Stubbing
        `a._settings.llm_retry_max_attempts = 3` here would be silently
        ignored, and the "3" in these tests' assertions would actually come
        from the global default (settings.llm_retry_max_attempts default=3),
        making a CI env override of that default change these tests' expected
        counts without the fixture reflecting why. Assert the real default is
        in effect instead of stubbing a value nothing reads.
        """
        from pacca.config.settings import active_overrides, get_settings

        assert active_overrides() == {}, "no overrides should leak from another test"
        assert get_settings().llm_retry_max_attempts == 3, (
            "These tests assume the real settings.llm_retry_max_attempts "
            "default (3); if that default changes, update the expected "
            "attempt counts below rather than silently drifting."
        )
        cfg = AgentConfig(model="claude-test", temperature=0.0, max_tokens=100)
        return _ConcreteAgent(config=cfg)

    @pytest.mark.asyncio
    async def test_internal_server_error_5xx_is_retried_to_exhaustion(
        self, agent: _ConcreteAgent
    ) -> None:
        """
        A 500 InternalServerError must be retried up to llm_retry_max_attempts,
        then the original error re-raised (reraise=True preserved).
        """
        server_error = InternalServerError(
            message="Internal server error",
            response=MagicMock(status_code=500),
            body={"error": {"type": "api_error"}},
        )
        agent.client.messages.create = AsyncMock(
            side_effect=[server_error, server_error, server_error]
        )

        with (
            patch("pacca.agents.base.wait_exponential", return_value=wait_fixed(0)),
            pytest.raises(InternalServerError),
        ):
            await agent.execute("test prompt", _TestOutput)

        assert agent.client.messages.create.call_count == 3, (
            "InternalServerError (500) must be retried up to "
            "llm_retry_max_attempts (3), not fail on the first attempt."
        )

    @pytest.mark.asyncio
    async def test_529_overloaded_is_retried(self, agent: _ConcreteAgent) -> None:
        """
        A 529 ("overloaded") response is a raw APIStatusError subclass with
        status_code=529 in anthropic 0.98.0 (see session verification: the
        SDK's concrete _make_status_error maps 529 to an internal
        `_exceptions.OverloadedError`, a class that exists in the package but
        is NOT re-exported from the top-level `anthropic` namespace/__all__ —
        so callers, and this test, only ever see it as an APIStatusError with
        status_code == 529). _is_retriable_status_error must treat it as
        retriable because 529 >= 500.
        """
        overloaded_error = APIStatusError(
            message="Overloaded",
            response=MagicMock(status_code=529),
            body={"error": {"type": "overloaded_error"}},
        )
        success_response = make_mock_response()
        agent.client.messages.create = AsyncMock(side_effect=[overloaded_error, success_response])

        with patch("pacca.agents.base.wait_exponential", return_value=wait_fixed(0)):
            result = await agent.execute("test prompt", _TestOutput)

        assert result.result == "approved"
        assert agent.client.messages.create.call_count == 2, (
            "A 529 (status_code=529) APIStatusError must be retried — "
            "529 >= 500, so _is_retriable_status_error must return True."
        )

    @pytest.mark.asyncio
    async def test_pre_fix_predicate_does_not_retry_5xx(self, agent: _ConcreteAgent) -> None:
        """
        Regression proof: with ONLY the pre-chg-21 predicate
        (retry_if_exception_type(RETRIABLE_ERRORS), no OR'd status-code
        check), a 500 is NOT retried — it fails on the first attempt.

        This test inlines the exact pre-fix predicate (rather than relying on
        git history/stash, which is forbidden in this worktree) so the
        regression this change fixes stays runnable and visible in CI
        forever, not just as a one-time manual proof.
        """
        from tenacity import retry_if_exception_type

        server_error = InternalServerError(
            message="Internal server error",
            response=MagicMock(status_code=500),
            body={"error": {"type": "api_error"}},
        )
        agent.client.messages.create = AsyncMock(
            side_effect=[server_error, server_error, server_error]
        )

        pre_fix_predicate = retry_if_exception_type(base_module.RETRIABLE_ERRORS)

        with (
            patch("pacca.agents.base.wait_exponential", return_value=wait_fixed(0)),
            patch(
                "pacca.agents.base.retry_if_exception_type",
                return_value=pre_fix_predicate,
            ),
            patch(
                "pacca.agents.base.retry_if_exception", side_effect=lambda _pred: pre_fix_predicate
            ),
            pytest.raises(InternalServerError),
        ):
            await agent.execute("test prompt", _TestOutput)

        assert agent.client.messages.create.call_count == 1, (
            "With the pre-fix predicate reinstated, a 500 must fail on the "
            "first attempt (this is the bug chg-21 fixes)."
        )

    @pytest.mark.asyncio
    async def test_is_retriable_status_error_reachable_from_retry_path(
        self, agent: _ConcreteAgent
    ) -> None:
        """
        `_is_retriable_status_error` must be actually invoked by the retry
        path, not merely defined and unreferenced. Spy on it and confirm it
        is called at least once during a 5xx retry cycle.
        """
        server_error = InternalServerError(
            message="Internal server error",
            response=MagicMock(status_code=500),
            body={"error": {"type": "api_error"}},
        )
        success_response = make_mock_response()
        agent.client.messages.create = AsyncMock(side_effect=[server_error, success_response])

        spy = MagicMock(wraps=base_module._is_retriable_status_error)

        with (
            patch("pacca.agents.base.wait_exponential", return_value=wait_fixed(0)),
            patch("pacca.agents.base._is_retriable_status_error", spy),
        ):
            result = await agent.execute("test prompt", _TestOutput)

        assert result.result == "approved"
        assert spy.call_count >= 1, (
            "_is_retriable_status_error must be reachable from the retry "
            "predicate — it was dead code before chg-21."
        )


class TestSdkInternalRetryDisabled:
    """
    chg-21: tenacity must be the SOLE retry authority for LLM calls.

    The Anthropic SDK's AsyncAnthropic client retries 408/409/429/5xx
    internally by default (max_retries=2, i.e. up to 3 HTTP attempts per
    call) BELOW tenacity — invisible to effective_settings()-driven retry
    tuning, to _log_retry_attempt's logging, and to the span's
    attempt_number. Once tenacity also retries 5xx (this change), leaving
    the SDK default would silently multiply attempts under two
    uncoordinated backoff schedules: up to llm_retry_max_attempts (tenacity)
    x 3 (SDK) HTTP requests for one persistent 5xx. BaseAgent must construct
    the client with max_retries=0 so nobody reintroduces that multiplication.
    """

    def test_client_constructed_with_max_retries_zero(self) -> None:
        agent = _ConcreteAgent(config=AgentConfig(model="claude-test"))
        assert agent.client.max_retries == 0, (
            "AsyncAnthropic must be constructed with max_retries=0. The SDK "
            "default (max_retries=2) retries 5xx/429 internally, which would "
            "multiply attempts on top of tenacity's own 5xx retry (chg-21)."
        )


class _FakeOutcome:
    """Minimal stand-in for a tenacity Future outcome: only .exception() is used."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def exception(self) -> BaseException:
        return self._exc


class _FakeRetryState:
    """
    Minimal duck-typed stand-in for tenacity.RetryCallState.

    _build_retry_wait's returned callable only ever reads `.outcome.exception()`
    (to inspect the failed attempt's exception for a retry-after header) and,
    on the fallback path, `.attempt_number` (consumed by the real
    wait_exponential it delegates to). No sleeping, no asyncio, no real
    tenacity retry loop involved -- these tests call the wait callable
    directly and assert on its return value.
    """

    def __init__(self, exc: BaseException, attempt_number: int = 1) -> None:
        self.outcome = _FakeOutcome(exc)
        self.attempt_number = attempt_number


class TestRetryAfterHeader:
    """
    chg-21 follow-up (Validator round 3): disabling the SDK's own retry loop
    (max_retries=0) also discarded its retry-after/retry-after-ms handling.
    _build_retry_wait restores server-directed backoff at the tenacity layer
    without reintroducing the SDK's second retry loop.

    All assertions are on the wait callable's COMPUTED return value, never
    on wall-clock time -- these tests call base_module._build_retry_wait(...)
    directly with a fake retry_state, so nothing here sleeps.
    """

    @staticmethod
    def _rate_limit_error_with_headers(headers: dict[str, str]) -> RateLimitError:
        response = MagicMock(status_code=429)
        response.headers = headers
        return RateLimitError(
            message="Rate limited",
            response=response,
            body={"error": {"type": "rate_limit_error"}},
        )

    def test_retry_after_seconds_is_honored(self) -> None:
        """A `retry-after: 7` header must produce a computed wait of 7.0s."""
        exc = self._rate_limit_error_with_headers({"retry-after": "7"})
        wait_fn = base_module._build_retry_wait(min_seconds=1.0, max_seconds=30.0)

        assert wait_fn(_FakeRetryState(exc)) == 7.0

    def test_retry_after_ms_is_honored(self) -> None:
        """`retry-after-ms` is more precise than `retry-after` and must be preferred."""
        exc = self._rate_limit_error_with_headers({"retry-after-ms": "2500"})
        wait_fn = base_module._build_retry_wait(min_seconds=1.0, max_seconds=30.0)

        assert wait_fn(_FakeRetryState(exc)) == 2.5

    def test_retry_after_exceeding_max_is_clamped(self) -> None:
        """
        A hostile/buggy `retry-after: 9999` must be clamped to
        llm_retry_wait_max_seconds, not honored verbatim -- otherwise a
        malicious or misconfigured server could hang an agent call for
        hours.
        """
        exc = self._rate_limit_error_with_headers({"retry-after": "9999"})
        wait_fn = base_module._build_retry_wait(min_seconds=1.0, max_seconds=30.0)

        assert wait_fn(_FakeRetryState(exc)) == 30.0

    def test_clamp_respects_runtime_override_of_max_wait(self) -> None:
        """
        The clamp is only meaningful if it tracks settings.llm_retry_wait_max_seconds
        through effective_settings() AT CALL TIME -- exactly the same guarantee
        TestRetryRespectsRuntimeOverrides proves for llm_retry_max_attempts.
        _call_with_retry passes effective_settings().llm_retry_wait_max_seconds
        straight into _build_retry_wait on every call; this drives that exact
        override path and confirms the clamp reflects the override, not the
        construction-time default (30.0).
        """
        from pacca.config.settings import apply_overrides, clear_all_overrides, effective_settings

        exc = self._rate_limit_error_with_headers({"retry-after": "9999"})
        try:
            apply_overrides({"llm_retry_wait_max_seconds": 2.0, "llm_retry_wait_min_seconds": 0.1})
            settings = effective_settings()
            wait_fn = base_module._build_retry_wait(
                settings.llm_retry_wait_min_seconds, settings.llm_retry_wait_max_seconds
            )
            assert wait_fn(_FakeRetryState(exc)) == 2.0, (
                "Clamp must use the OVERRIDDEN llm_retry_wait_max_seconds (2.0), "
                "not the default (30.0)."
            )
        finally:
            clear_all_overrides()

    def test_malformed_or_absent_header_falls_back_to_exponential_without_raising(
        self,
    ) -> None:
        """
        Neither a malformed header value nor a missing header should raise --
        both must fall back to the same wait_exponential(min, max) the
        fallback path delegates to.
        """
        from tenacity import wait_exponential

        expected = wait_exponential(min=1.0, max=30.0)
        wait_fn = base_module._build_retry_wait(min_seconds=1.0, max_seconds=30.0)

        malformed_exc = self._rate_limit_error_with_headers({"retry-after": "not-a-number"})
        state = _FakeRetryState(malformed_exc, attempt_number=2)
        assert wait_fn(state) == expected(state)

        absent_exc = self._rate_limit_error_with_headers({})
        state2 = _FakeRetryState(absent_exc, attempt_number=3)
        assert wait_fn(state2) == expected(state2)

    def test_5xx_without_retry_after_still_uses_exponential(self) -> None:
        """
        Non-regression: a 5xx with no retry-after header must behave exactly
        as before this change -- wait_exponential(min, max), unchanged.
        """
        from tenacity import wait_exponential

        response = MagicMock(status_code=500)
        response.headers = {}
        server_error = InternalServerError(
            message="Internal server error",
            response=response,
            body={"error": {"type": "api_error"}},
        )
        expected = wait_exponential(min=1.0, max=30.0)
        wait_fn = base_module._build_retry_wait(min_seconds=1.0, max_seconds=30.0)
        state = _FakeRetryState(server_error, attempt_number=2)

        assert wait_fn(state) == expected(state)


class TestRetryRespectsRuntimeOverrides:
    """
    The three llm_retry_* fields are advertised by PATCH /config as
    runtime-tunable ("increase retries during API instability"). For that to
    be true, BaseAgent._call_with_retry() must read them from
    effective_settings() AT CALL TIME — not from the construction-time
    self._settings snapshot.

    These tests prove the override actually changes the attempt count, and
    that behavior is neutral at the default config (no override → 3 attempts,
    matching settings.llm_retry_max_attempts default).

    Note: unlike TestRetryLogic, these tests deliberately do NOT stub
    agent._settings — the whole point is that _call_with_retry ignores the
    snapshot and reads the live effective settings. We drive the runtime
    override store via the production apply_overrides/clear_all_overrides API.
    """

    @pytest.fixture
    def agent(self) -> _ConcreteAgent:
        """A real agent with no _settings stub — call-time settings only."""
        cfg = AgentConfig(model="claude-test", temperature=0.0, max_tokens=100)
        return _ConcreteAgent(config=cfg)

    @staticmethod
    def _always_rate_limited() -> RateLimitError:
        return RateLimitError(
            message="Rate limit exceeded",
            response=MagicMock(status_code=429),
            body={"error": {"type": "rate_limit_error"}},
        )

    @pytest.mark.asyncio
    async def test_override_increases_attempt_count(self, agent: _ConcreteAgent) -> None:
        """
        apply_overrides({"llm_retry_max_attempts": 5}) must make the retry try
        5 times — proving the override reaches the tenacity stop condition at
        call time, not the snapshot's default of 3.

        We override the wait bounds to ~0 so the test does not actually sleep;
        the PRIMARY assertion is the attempt COUNT.
        """
        from pacca.config.settings import apply_overrides, clear_all_overrides

        # Always fail with a retriable error so retries run to exhaustion.
        agent.client.messages.create = AsyncMock(side_effect=self._always_rate_limited())

        try:
            apply_overrides(
                {
                    "llm_retry_max_attempts": 5,
                    "llm_retry_wait_min_seconds": 0.1,  # ge=0.1 floor; kept tiny
                    "llm_retry_wait_max_seconds": 1.0,  # ge=1.0 floor; kept tiny
                }
            )
            with (
                patch("pacca.agents.base.wait_exponential", return_value=MagicMock(sleep=0)),
                pytest.raises(RateLimitError),
            ):
                await agent.execute("test prompt", _TestOutput)
        finally:
            clear_all_overrides()

        assert agent.client.messages.create.call_count == 5, (
            "Override llm_retry_max_attempts=5 must drive 5 attempts. "
            "If this is 3, _call_with_retry is still reading the "
            "construction-time self._settings snapshot instead of "
            "effective_settings() at call time."
        )

    @pytest.mark.asyncio
    async def test_override_decreases_attempt_count(self, agent: _ConcreteAgent) -> None:
        """
        Task-specified case: apply_overrides({"llm_retry_max_attempts": 2})
        must cap the retry at exactly 2 attempts (vs the default of 3).
        """
        from pacca.config.settings import apply_overrides, clear_all_overrides

        agent.client.messages.create = AsyncMock(side_effect=self._always_rate_limited())

        try:
            apply_overrides(
                {
                    "llm_retry_max_attempts": 2,
                    "llm_retry_wait_min_seconds": 0.1,
                    "llm_retry_wait_max_seconds": 1.0,
                }
            )
            with (
                patch("pacca.agents.base.wait_exponential", return_value=MagicMock(sleep=0)),
                pytest.raises(RateLimitError),
            ):
                await agent.execute("test prompt", _TestOutput)
        finally:
            clear_all_overrides()

        assert agent.client.messages.create.call_count == 2, (
            "Override llm_retry_max_attempts=2 must cap attempts at 2."
        )

    @pytest.mark.asyncio
    async def test_default_attempt_count_is_neutral(self, agent: _ConcreteAgent) -> None:
        """
        Behavior-neutral at default: with NO override active, the retry must
        still make exactly 3 attempts (settings.llm_retry_max_attempts default).
        Guards against the dynamic read changing default behavior.
        """
        from pacca.config.settings import active_overrides

        # Sanity: no overrides leaked from another test.
        assert active_overrides() == {}

        agent.client.messages.create = AsyncMock(side_effect=self._always_rate_limited())

        with (
            patch("pacca.agents.base.wait_exponential", return_value=MagicMock(sleep=0)),
            pytest.raises(RateLimitError),
        ):
            await agent.execute("test prompt", _TestOutput)

        assert agent.client.messages.create.call_count == 3, (
            "With no override, attempt count must equal the default of 3. "
            "The dynamic read must be behavior-neutral at default config."
        )


# =============================================================================
# OpenTelemetry span tests
# =============================================================================


class TestOtelSpans:
    """
    Tests that OTel spans are created, attributed, and closed correctly.

    Teaching note on mocking OTel:
      We don't need a real OTel backend to test span creation. We mock the
      tracer's start_as_current_span() method and verify it was called with
      the right span name, and that the right attributes were set.

      This is valid because we're testing OUR code (does it call the OTel
      API correctly?), not OTel's code (does start_as_current_span work?).
    """

    @pytest.fixture
    def agent_with_mock_tracer(self) -> _ConcreteAgent:
        """Create a test agent with a mocked OTel tracer."""
        cfg = AgentConfig(model="claude-test", temperature=0.0, max_tokens=100)
        a = _ConcreteAgent(config=cfg)
        a._settings = MagicMock()
        a._settings.llm_retry_max_attempts = 1
        a._settings.llm_retry_wait_min_seconds = 0.0
        a._settings.llm_retry_wait_max_seconds = 0.0
        return a

    @pytest.mark.asyncio
    async def test_span_created_for_successful_call(
        self, agent_with_mock_tracer: _ConcreteAgent
    ) -> None:
        """
        A span named 'agent.TestAgent' must be opened for every successful call.

        This tests the naming convention: 'agent.<AgentName>' so all agent
        spans are filterable in the trace backend.
        """
        agent = agent_with_mock_tracer
        agent.client.messages.create = AsyncMock(return_value=make_mock_response())

        # Track span names opened
        opened_spans = []

        original_start = agent._tracer.start_as_current_span

        def capturing_start(name: str, **kwargs: Any) -> Any:
            opened_spans.append(name)
            return original_start(name, **kwargs)

        with patch.object(agent._tracer, "start_as_current_span", side_effect=capturing_start):
            await agent.execute("test prompt", _TestOutput)

        assert "agent.TestAgent" in opened_spans, (
            f"Expected span 'agent.TestAgent' to be opened. Got: {opened_spans}"
        )

    @pytest.mark.asyncio
    async def test_span_attributes_include_agent_name(
        self, agent_with_mock_tracer: _ConcreteAgent
    ) -> None:
        """
        The span must have 'agent.name' attribute set to the agent's name.

        This is how you filter traces by agent in Langfuse:
        'show me all spans from DecisionAgent'.
        """
        agent = agent_with_mock_tracer
        agent.client.messages.create = AsyncMock(return_value=make_mock_response())

        set_attributes = {}

        # Create a mock span that records attribute calls
        mock_span = MagicMock()
        mock_span.__enter__ = MagicMock(return_value=mock_span)
        mock_span.__exit__ = MagicMock(return_value=False)

        def capture_attribute(key: str, value: Any) -> None:
            set_attributes[key] = value

        mock_span.set_attribute = capture_attribute

        with patch.object(agent._tracer, "start_as_current_span", return_value=mock_span):
            await agent.execute("test prompt", _TestOutput)

        assert set_attributes.get("agent.name") == "TestAgent", (
            f"Expected span attribute 'agent.name' = 'TestAgent'. Got attributes: {set_attributes}"
        )
        assert "llm.model" in set_attributes, (
            "Span must include 'llm.model' attribute for filtering by model."
        )

    @pytest.mark.asyncio
    async def test_token_usage_recorded_on_span(
        self, agent_with_mock_tracer: _ConcreteAgent
    ) -> None:
        """
        Token usage (input_tokens, output_tokens) must be recorded on the span.

        This is critical for cost analysis: in Langfuse, you can sum
        llm.total_tokens across all spans to compute total API cost.
        """
        agent = agent_with_mock_tracer
        agent.client.messages.create = AsyncMock(return_value=make_mock_response())

        set_attributes = {}
        mock_span = MagicMock()
        mock_span.__enter__ = MagicMock(return_value=mock_span)
        mock_span.__exit__ = MagicMock(return_value=False)
        mock_span.set_attribute = lambda k, v: set_attributes.update({k: v})

        with patch.object(agent._tracer, "start_as_current_span", return_value=mock_span):
            await agent.execute("test prompt", _TestOutput)

        # make_mock_response() sets input_tokens=450, output_tokens=120
        assert set_attributes.get("llm.input_tokens") == 450
        assert set_attributes.get("llm.output_tokens") == 120
        assert set_attributes.get("llm.total_tokens") == 570

    @pytest.mark.asyncio
    async def test_span_error_recorded_on_failure(
        self, agent_with_mock_tracer: _ConcreteAgent
    ) -> None:
        """
        When an agent call fails permanently, the failure must be visible on
        the span -- carrying the exception *type* only.

        Without any error signal a failed request looks like a successful span
        that just didn't return a result, which is confusing and misleading.
        But the span is exported to a third-party trace backend outside the
        HIPAA-scoped audit store, so THREAT-03 forbids putting the exception
        message or stack trace on it: an exception raised while handling a
        clinical case can carry PHI in either.

        This test previously asserted ``span.record_exception()`` was called.
        That call attaches the full message and stack trace, so it was removed
        in favour of a typed event. The assertion below is deliberately
        stronger than the one it replaces: it checks both that the failure is
        visible *and* that the message did not leak with it. The full error
        text is still retained in the audit record, which never leaves the
        audit store.
        """
        agent = agent_with_mock_tracer
        secret_message = "Patient MRN 12345678 triggered an unexpected format"
        agent.client.messages.create = AsyncMock(side_effect=ValueError(secret_message))

        events: list[tuple[str, dict[str, Any]]] = []
        statuses: list[tuple[Any, ...]] = []

        mock_span = MagicMock()
        mock_span.__enter__ = MagicMock(return_value=mock_span)
        mock_span.__exit__ = MagicMock(return_value=False)
        mock_span.set_attribute = MagicMock()
        mock_span.add_event = MagicMock(
            side_effect=lambda name, attrs=None: events.append((name, attrs or {}))
        )
        mock_span.set_status = MagicMock(side_effect=lambda *a: statuses.append(a))

        with (
            patch.object(agent._tracer, "start_as_current_span", return_value=mock_span),
            pytest.raises(ValueError),
        ):
            await agent.execute("test prompt", _TestOutput)

        # 1. The failure is visible: the span carries a typed exception event.
        exception_events = [e for e in events if e[0] == "exception"]
        assert exception_events, (
            "A permanent agent failure must record an 'exception' event on the span. "
            "Without it, errors are invisible in the trace backend."
        )
        assert exception_events[0][1].get("exception.type") == "ValueError", (
            "The exception event must name the exception type, so a failure is "
            f"classifiable in the backend. Got: {exception_events[0][1]!r}"
        )

        # 2. The span is marked as failed.
        assert statuses, "span.set_status() must be called on a permanent failure."

        # 3. THREAT-03: neither the message nor any of its content reaches the span.
        recorded_text = repr(events) + repr(statuses)
        assert secret_message not in recorded_text, (
            "THREAT-03: the exception message must not reach the span. Spans are "
            "exported outside the audit store and the message may carry PHI."
        )
        assert "12345678" not in recorded_text, (
            "THREAT-03: PHI from the exception message leaked onto the span."
        )
        mock_span.record_exception.assert_not_called()


# =============================================================================
# Tracing configuration tests
# =============================================================================


class TestTracingConfiguration:
    """Tests for configure_tracing() setup."""

    def test_configure_tracing_noop_when_disabled(self) -> None:
        """
        configure_tracing(enabled=False) must install a no-op provider.

        This ensures unit tests can call configure_tracing without setting
        up a real OTel exporter. All agent tracing calls become no-ops.
        """
        from opentelemetry import trace as otel_trace

        import pacca.config.tracing as tracing_module
        from pacca.config.tracing import configure_tracing

        # Reset the configured flag so we can call configure_tracing in tests
        tracing_module._tracing_configured = False

        configure_tracing(enabled=False)

        # After disabling, the tracer should be a no-op tracer
        tracer = otel_trace.get_tracer("test")
        with tracer.start_as_current_span("test_span") as span:
            # A no-op span's context should not be recording
            from opentelemetry.trace import NonRecordingSpan

            assert isinstance(span, NonRecordingSpan), (
                "With tracing disabled, spans should be NonRecordingSpan (no-op). "
                "This ensures tests don't accidentally export real traces."
            )

        # Reset for subsequent tests
        tracing_module._tracing_configured = False


class TestTracingStructlogMigration:
    """iter-6 chg-1: tracing.py's module logger must be structlog, not stdlib."""

    def test_logger_is_structlog_not_stdlib(self) -> None:
        # RED pre-migration: tracing_module.logger is a logging.Logger.
        # GREEN post-migration: it is a structlog BoundLogger (or lazy proxy),
        # neither of which is an instance of logging.Logger.
        assert not isinstance(tracing_module.logger, _stdlib_logging.Logger)

    def test_configure_tracing_console_path_accepts_kwargs(self) -> None:
        # Exercises the structured-kwargs call sites (logger.info(event, key=val))
        # on the console path. Must not raise after the migration.
        tracing_module._tracing_configured = False
        tracing_module.configure_tracing(service_name="pacca-test", endpoint=None, enabled=True)
        tracing_module._tracing_configured = False  # reset for other tests
