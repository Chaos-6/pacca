"""
Base agent class for all PACCA AI agents.

This module provides the foundation that every agent inherits. It handles
three concerns that are common to all agents:
  1. LLM API communication (Claude via the Anthropic async client)
  2. Retry with exponential backoff (via tenacity)
  3. OpenTelemetry span instrumentation (one span per LLM call)

Teaching note — why put retry and tracing in the base class?

  The alternative is putting them in each agent individually. That means
  every agent — DecisionAgent, MedicalDirectorAgent, EvidenceAgent,
  ClassificationAgent — needs identical retry and tracing boilerplate.
  If you later want to change the retry configuration, you change it in
  four places. If you forget one, that agent silently has different behavior.

  The base class pattern (also called the "Template Method" pattern) says:
  define the common algorithm once, let subclasses fill in the specifics.
  The "specifics" here are: system_prompt (what role does this agent play?)
  and name (what is it called in logs?). Everything else — how to call the
  LLM, how to retry, how to trace — is defined once here.

Teaching note — tenacity retry strategy

  The retry decorator we use is:
    @retry(
        stop=stop_after_attempt(N),       # Give up after N total tries
        wait=wait_exponential(min=1, max=30),  # Wait 1s, 2s, 4s, 8s... up to 30s
        retry=retry_if_exception_type(RETRIABLE_ERRORS),  # Only retry these
        before_sleep=log_retry_attempt,   # Log each retry
        reraise=True,                     # After all attempts, re-raise the last error
    )

  The wait_exponential strategy means:
    Attempt 1: fails → wait 1 second
    Attempt 2: fails → wait 2 seconds
    Attempt 3: fails → re-raise (or wait 4 seconds if max_attempts=4)

  This is respectful to the API: if it's struggling (rate limited, overloaded),
  waiting longer between retries gives it time to recover rather than hammering
  it with immediate retries.

Teaching note — what errors are retriable vs. not?

  RETRIABLE (transient — will likely succeed on retry):
    - 429 RateLimitError — too many requests, wait and retry
    - 500/502/503/504 APIStatusError — server-side errors, transient
    - APIConnectionError — network blip
    - APITimeoutError — request timed out

  NOT RETRIABLE (permanent — retrying won't help):
    - 400 BadRequestError — we sent invalid data; retrying sends the same bad data
    - 401 AuthenticationError — wrong API key; retrying with the same key fails again
    - ValueError from our own parsing — our code has a bug, not the API

  Retrying non-retriable errors wastes time and obscures the real problem.

Teaching note — OpenTelemetry span attributes

  Each span we create has attributes attached:
    span.set_attribute("agent.name", "DecisionAgent")
    span.set_attribute("model", "claude-sonnet-...")
    span.set_attribute("attempt_number", 1)
    span.set_attribute("input_tokens", 450)
    span.set_attribute("output_tokens", 120)

  These attributes are what make traces searchable and useful. In Langfuse
  or Jaeger, you can filter: "show me all agent calls where output_tokens > 500"
  or "show me all calls that hit attempt_number 2 or 3" (those are your
  retried requests — you want to know how often that happens).
"""

import os
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from typing import Any, TypeVar

from anthropic import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncAnthropic,
    RateLimitError,
)
from pydantic import BaseModel, Field
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..config import get_logger
from ..config.settings import effective_settings, get_settings
from ..config.tracing import get_tracer, record_span_error

# structlog-backed, per the repo convention (AGENT_LESSONS P-002). B5: a stdlib
# logger here would TypeError on the structured-kwarg calls below.
logger = get_logger(__name__)

# Generic type variable: T must be a Pydantic BaseModel.
# This is how we tell Python: "execute() returns whatever Pydantic model
# you pass in as response_model — not just 'some BaseModel'."
T = TypeVar("T", bound=BaseModel)

# Errors that are worth retrying — transient API/network failures.
# Tuple so it can be passed directly to retry_if_exception_type().
RETRIABLE_ERRORS = (
    RateLimitError,  # 429 — slow down
    APIConnectionError,  # Network unreachable
    APITimeoutError,  # Request timed out
)


def _is_retriable_status_error(exc: BaseException) -> bool:
    """
    Return True for 5xx server-side errors, False for 4xx client errors.

    We need a custom check for APIStatusError because it covers both
    retriable (500, 502, 503, 504) and non-retriable (400, 401, 403)
    HTTP errors. We only want to retry server-side errors.
    """
    if isinstance(exc, APIStatusError):
        return bool(exc.status_code >= 500)
    return False


def _parse_retry_after_seconds(exc: BaseException | None) -> float | None:
    """
    Extract a server-directed wait time (seconds) from an APIStatusError's
    response headers, preferring the more precise `retry-after-ms` over
    `retry-after` -- same precedence as the Anthropic SDK's own
    _parse_retry_after_header(). Returns None if the exception carries no
    HTTP response, or neither header is present/well-formed; callers fall
    back to exponential backoff in that case.

    Only `str`/`bytes` header values are accepted. This matters beyond
    input hygiene: `unittest.mock.MagicMock` implements `__float__` (default
    1.0), so an un-configured `MagicMock(status_code=...).headers.get(...)`
    -- the exact pattern this file's existing tests use to build fake
    Anthropic errors -- would otherwise "successfully" parse as a 1-second
    retry-after and cause a REAL asyncio.sleep(1.0) in every test that
    doesn't explicitly set headers. The isinstance guard makes that
    impossible: a MagicMock is never a str or bytes, so it is treated as
    "no header", exactly like real httpx.Headers.get() returning None.
    """
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None or not hasattr(headers, "get"):
        return None

    for header_name, is_milliseconds in (("retry-after-ms", True), ("retry-after", False)):
        raw = headers.get(header_name)
        if not isinstance(raw, (str, bytes)):
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        return value / 1000 if is_milliseconds else value

    return None


def _build_retry_wait(min_seconds: float, max_seconds: float) -> Callable[[RetryCallState], float]:
    """
    Build a tenacity wait strategy that honors the Anthropic API's
    server-directed `retry-after` / `retry-after-ms` header when the
    failed attempt's exception carries one, falling back to
    wait_exponential(min, max) otherwise.

    Rationale (chg-21 follow-up): BaseAgent.__init__ sets max_retries=0 on
    the Anthropic client so tenacity is the SOLE retry authority (avoiding
    a double-retry under two uncoordinated backoff schedules -- see that
    comment). But the SDK's own retry loop also read this header, and
    honoring server-directed backoff under sustained 429 rate limiting is
    what keeps a brief throttle from turning into a cascading one.
    Disabling the SDK loop would have silently discarded that behavior;
    this restores it at the tenacity layer instead.

    Clamp: a header value is capped to `max_seconds` (settings.
    llm_retry_wait_max_seconds) -- a hostile or buggy `retry-after: 86400`
    must not hang an agent call for a day. A non-positive, missing, or
    malformed header falls back to exponential backoff without raising.
    """
    fallback = wait_exponential(min=min_seconds, max=max_seconds)

    def _wait(retry_state: RetryCallState) -> float:
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        seconds = _parse_retry_after_seconds(exc)
        if seconds is not None and seconds > 0:
            return min(seconds, max_seconds)
        # float(...) is an explicit coercion, not a suppression: tenacity's
        # wait_exponential.__call__ already returns a real float at runtime.
        # The wrap makes that fact visible to mypy regardless of whether the
        # checking environment has tenacity's stubs available (the repo's
        # own .pre-commit-config.yaml mypy hook runs in an isolated venv
        # whose additional_dependencies list does not include tenacity,
        # where wait_exponential's return type is otherwise seen as Any and
        # trips warn_return_any) -- environment-proofing the touched
        # function in-file rather than adding a suppression.
        return float(fallback(retry_state))

    return _wait


def _log_retry_attempt(retry_state: RetryCallState) -> None:
    """
    Called by tenacity before each sleep between retry attempts.
    """
    attempt = retry_state.attempt_number
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    wait = retry_state.next_action.sleep if retry_state.next_action else 0

    logger.warning(
        "llm_api_retry",
        attempt=attempt,
        wait_seconds=round(wait, 2),
        error_type=type(exc).__name__ if exc else "unknown",
    )


class AgentConfig(BaseModel):
    """
    Configuration for a single agent instance.

    Attributes:
        model:       The Claude model ID to use. Defaults to the value in
                     settings, which can be overridden via environment variable.
        temperature: Sampling temperature. 0.0 = deterministic (same input →
                     same output). For clinical decisions we always use 0.0.
        max_tokens:  Maximum response length. 4096 is sufficient for structured
                     clinical decision output.
    """

    # Single source of truth: settings.default_model (override via env DEFAULT_MODEL).
    model: str = Field(default_factory=lambda: get_settings().default_model)
    temperature: float = 0.0
    max_tokens: int = 4096


class BaseAgent(ABC):
    """
    Abstract base class for all PACCA AI agents.

    Provides:
      - Anthropic async client (shared per agent instance)
      - execute() method with retry + OTel span instrumentation
      - Structured output via Claude's tool-use API

    Subclasses must implement:
      - name (property): human-readable agent name for logs and traces
      - system_prompt (property): the clinical persona and instructions

    Usage:
        class MyAgent(BaseAgent):
            @property
            def name(self) -> str:
                return "MyAgent"

            @property
            def system_prompt(self) -> str:
                return "You are a clinical specialist..."

            async def run(self, context: MyContext) -> MyOutput:
                return await self.execute(
                    user_input=build_prompt(context),
                    response_model=MyOutput,
                )
    """

    def __init__(self, config: AgentConfig | None = None) -> None:
        self.config = config or AgentConfig()
        # One client instance per agent — the async client is thread-safe
        # and reuses the underlying HTTP connection pool efficiently.
        #
        # max_retries=0 (chg-21): the SDK's own default (max_retries=2, i.e.
        # up to 3 HTTP attempts per call) retries 408/409/429/5xx internally
        # in _base_client._should_retry(), BELOW tenacity and invisible to
        # it — invisible to effective_settings()/PATCH-driven retry tuning,
        # to _log_retry_attempt's logging, and to the span's attempt_number.
        # With tenacity now also retrying 5xx (see _call_with_retry below),
        # leaving the SDK's default would multiply attempts under two
        # uncoordinated backoff schedules: up to
        # llm_retry_max_attempts (tenacity) x 3 (SDK) HTTP requests for one
        # persistent 5xx. Tenacity is the single retry authority; the SDK
        # must not retry underneath it.
        self.client = AsyncAnthropic(
            api_key=os.getenv("ANTHROPIC_API_KEY") or "",
            max_retries=0,
        )
        # Get a tracer named after the agent's module — shows up in OTel
        self._tracer = get_tracer(f"pacca.agents.{self.name.lower()}")
        self._settings = get_settings()

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name used in logs, traces, and audit records."""
        ...

    @property
    @abstractmethod
    def system_prompt(self) -> str:
        """
        The clinical persona and instructions for this agent.

        This is sent as the `system` parameter in every Claude API call.
        It defines: who the agent is, what its job is, what format to
        use for output, and what safety rules to follow.
        """
        ...

    async def execute(self, user_input: str, response_model: type[T]) -> T:
        """
        Call the Claude API with retry and OTel span instrumentation.

        This is the central method of the entire agent framework. Every
        agent's run() method ultimately calls this.

        The flow:
          1. Open an OTel span for this agent call
          2. Build the tool definition from the response model's JSON schema
          3. Call _call_with_retry() which handles the actual API call + retries
          4. Parse the structured output from the tool_use response
          5. Close the span (timing recorded automatically)

        Args:
            user_input:     The formatted clinical case / prompt for this call
            response_model: Pydantic model class defining the expected output
                            shape. Its JSON schema becomes the tool definition.

        Returns:
            An instance of response_model populated from the LLM's output

        Raises:
            ValueError:    If the LLM didn't use the structured tool (shouldn't
                           happen with tool_choice forced, but defensive)
            RateLimitError, APIStatusError: After all retry attempts exhausted
        """
        # The tool definition derives from the Pydantic model's JSON schema.
        # Teaching note: instead of asking the LLM to "return JSON in this format"
        # (which it can misformat), we define the schema as a tool and force the
        # model to call that tool. The model MUST populate every required field
        # or the API returns a validation error — making structured output a
        # guarantee rather than a hope.
        tool_def = {
            "name": "submit_result",
            "description": f"Submit the structured result for {self.name}",
            "input_schema": response_model.model_json_schema(),
        }

        # Open a span covering the entire agent call including retries.
        # The span name format "agent.<AgentName>" is consistent across all
        # agents, making traces filterable and comparable.
        with self._tracer.start_as_current_span(f"agent.{self.name}") as span:
            span.set_attribute("agent.name", self.name)
            span.set_attribute("llm.model", self.config.model)
            span.set_attribute("llm.max_tokens", self.config.max_tokens)
            span.set_attribute("llm.temperature", self.config.temperature)
            span.set_attribute("input.length_chars", len(user_input))

            call_start = time.time()
            try:
                response = await self._call_with_retry(user_input, tool_def)

                # Extract the structured output from the tool_use content block.
                # The API guarantees a tool_use block when tool_choice is forced.
                for content_block in response.content:
                    if content_block.type == "tool_use":
                        # Record token usage on the span — critical for cost tracking
                        if response.usage:
                            span.set_attribute("llm.input_tokens", response.usage.input_tokens)
                            span.set_attribute("llm.output_tokens", response.usage.output_tokens)
                            span.set_attribute(
                                "llm.total_tokens",
                                response.usage.input_tokens + response.usage.output_tokens,
                            )

                        duration_ms = int((time.time() - call_start) * 1000)
                        span.set_attribute("duration_ms", duration_ms)

                        # Validate and return the structured output.
                        # model_validate() raises ValidationError (not retried) if
                        # the LLM returned data that doesn't match the schema.
                        return response_model.model_validate(content_block.input)

                # This branch should never be reached when tool_choice is forced,
                # but we handle it defensively.
                raise ValueError(
                    f"Agent {self.name} did not return a tool_use response. "
                    f"Content blocks: {[b.type for b in response.content]}"
                )

            except Exception as exc:
                record_span_error(span, exc)
                logger.error(
                    "agent_call_failed",
                    agent=self.name,
                    error_type=type(exc).__name__,
                )
                raise

    async def _call_with_retry(
        self,
        user_input: str,
        tool_def: Mapping[str, object],
    ) -> Any:
        """
        Call the Anthropic API with tenacity retry logic.

        This is a separate method (not inlined in execute()) so that tenacity
        can wrap it cleanly. The @retry decorator applies to the entire method
        including the await — retrying the full API call on failure.

        Teaching note — why separate from execute()?
          tenacity's @retry decorator wraps a function and calls it again on
          failure. If we put the API call and the span in the same function,
          each retry attempt would open a NEW span — giving us multiple spans
          for one logical agent call. By separating them, the span stays open
          across all retry attempts and the final span captures the total
          duration including retries.

        Args:
            user_input: The formatted prompt
            tool_def:   The tool definition derived from the response model

        Returns:
            Anthropic API response object
        """
        # Read retry knobs from the CURRENT effective settings (env + runtime
        # overrides applied via PATCH /config), NOT the construction-time
        # snapshot in self._settings. This decorator is re-applied on every
        # call, so evaluating effective_settings() here makes the three
        # llm_retry_* fields tunable at runtime. Static fields (model name,
        # etc.) still come from self.config/self._settings — only the retry
        # knobs need to be dynamic.
        settings = effective_settings()

        @retry(  # type: ignore[misc,unused-ignore]
            stop=stop_after_attempt(settings.llm_retry_max_attempts),
            # chg-21 follow-up: honor server-directed retry-after /
            # retry-after-ms (clamped to llm_retry_wait_max_seconds) when
            # present, else the original wait_exponential(min, max). See
            # _build_retry_wait's docstring for why this exists now that
            # the SDK's own retry-after-aware loop is disabled below.
            wait=_build_retry_wait(
                settings.llm_retry_wait_min_seconds,
                settings.llm_retry_wait_max_seconds,
            ),
            # OR'd predicate (chg-21): retry the original transient-error
            # tuple (429/connection/timeout) AND any 5xx APIStatusError
            # (500/502/503/504/529), via the _is_retriable_status_error
            # helper that previously existed but was referenced nowhere —
            # a 500/529 used to fail on the first attempt. 4xx errors
            # (400 BadRequestError, 401 AuthenticationError, etc.) match
            # neither branch and are still raised on the first attempt.
            retry=retry_if_exception_type(RETRIABLE_ERRORS)
            | retry_if_exception(_is_retriable_status_error),
            before_sleep=_log_retry_attempt,
            reraise=True,
        )
        async def _attempt() -> Any:
            # The Anthropic SDK's create() has dozens of overloads; mypy can't
            # narrow them given our dynamic model/tool inputs. The runtime call
            # is correct (200+ passing tests confirm); the type-ignore is on the
            # SDK's overload resolution, not on our argument values.
            return await self.client.messages.create(  # type: ignore[call-overload,unused-ignore]
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                temperature=self.config.temperature,
                # system_prompt is a fixed per-agent-type prompt (loaded from a
                # static file/constant per subclass — see decision.py,
                # evidence_agent.py, etc.), not built fresh with per-request
                # data. Marking it as the cache_control breakpoint caches the
                # tool definition too, since render order is tools -> system
                # -> messages. Per-case data lives entirely in `messages`
                # (user_input below), which stays uncached and varies per call.
                system=[
                    {
                        "type": "text",
                        "text": self.system_prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": user_input}],
                tools=[tool_def],
                tool_choice={"type": "tool", "name": "submit_result"},
            )

        return await _attempt()
