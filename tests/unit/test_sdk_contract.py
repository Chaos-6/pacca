"""
Contract tests against the INSTALLED Anthropic SDK.

Every other test in this suite replaces `client.messages.create` with an
AsyncMock. That is the right call for behavioural tests -- they must not make
network calls -- but it has a blind spot with teeth: an AsyncMock accepts any
keyword argument at all. A suite of 977 mocked tests will stay green while the
production call path raises TypeError on every single invocation, because the
real signature is never exercised.

That is not hypothetical. It happened. `anthropic` removed `temperature`,
`top_p` and `top_k` from `messages.create` in the 1.x line and the method takes
no `**kwargs`, so `base.py` passing `temperature=` raised

    TypeError: AsyncMessages.create() got an unexpected keyword argument 'temperature'

in the SDK, before any request was constructed -- while CI reported success.
The dependency declaration that allowed it is `anthropic>=0.40.0`, unbounded.

These tests bind the arguments the production code actually passes against the
installed SDK's real signature. No network, no API key, no request: binding a
signature is a pure local operation. The cost is a few milliseconds; the thing
it catches is total, silent breakage of every clinical decision.
"""

import inspect

from anthropic.resources.messages.messages import AsyncMessages


def _create_signature() -> inspect.Signature:
    return inspect.signature(AsyncMessages.create)


class TestMessagesCreateContract:
    """The kwargs BaseAgent passes must be accepted by the installed SDK."""

    def test_production_kwargs_bind_against_installed_sdk(self) -> None:
        """
        Bind the exact argument set `BaseAgent._call_with_retry` sends.

        Keep this list in sync with the `client.messages.create(...)` call in
        src/pacca/agents/base.py. If a future change adds a kwarg the installed
        SDK does not accept, this fails here rather than at the first real
        clinical request.
        """
        sig = _create_signature()
        production_kwargs = {
            "model": "claude-sonnet-4-5-20250929",
            "max_tokens": 4096,
            "system": [{"type": "text", "text": "sys"}],
            "messages": [{"role": "user", "content": "case"}],
            "tools": [{"name": "submit_result", "input_schema": {}}],
            "tool_choice": {"type": "tool", "name": "submit_result"},
        }
        try:
            sig.bind(self=None, **production_kwargs)
        except TypeError as exc:
            raise AssertionError(
                "The arguments BaseAgent sends are not accepted by the installed "
                f"anthropic SDK: {exc}. Every agent call would raise TypeError "
                "before reaching the API. Reconcile src/pacca/agents/base.py with "
                "the installed SDK version."
            ) from exc

    def test_sampling_parameters_are_not_passed(self) -> None:
        """
        Sampling parameters must not reappear in the production call.

        SDD v3.0 removed RES-DSA-04 (temperature 0.0) and RES-PEA-03
        (temperature 0.1) and replaced them with observable consistency
        contracts. This asserts the code cannot regress to passing them --
        whether or not a given SDK version happens to tolerate it.
        """
        base_src = (
            inspect.getsource(
                __import__("pacca.agents.base", fromlist=["_"]),
            )
            .split("client.messages.create")[-1]
            .split("return await _attempt()")[0]
        )
        for param in ("temperature", "top_p", "top_k"):
            assert f"{param}=" not in base_src, (
                f"src/pacca/agents/base.py passes {param}= to messages.create. "
                "Sampling parameters were removed from the SDK and from the "
                "specification (RES-DSA-04). Passing one is a TypeError on the "
                "installed SDK and a 400 on newer models."
            )

    def test_agent_config_declares_no_sampling_fields(self) -> None:
        """AgentConfig must not carry a sampling field that nothing can send."""
        from pacca.agents.base import AgentConfig

        for param in ("temperature", "top_p", "top_k"):
            assert param not in AgentConfig.model_fields, (
                f"AgentConfig declares '{param}'. The SDK has no such parameter, "
                "so the field can only mislead a reader into believing sampling "
                "is being controlled."
            )
