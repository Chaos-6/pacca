"""
Provenance of ``decision_id`` — regression tests for PRODUCTION_READINESS B6.

The bug: ``DecisionAgent.run()`` passed ``response_model=AuthorizationDecision``,
so the model's forced tool-use output *was* the decision object — ``decision_id``
included. That value was persisted into a ``unique=True`` column, which meant:

  * resubmitting the same case produced the same id and 500'd on an IntegrityError
  * the id format was whatever the model felt like emitting that run
  * ``audit_logs.decision_id`` and the ``human_reviews`` FK both point at this
    value, so a reused id silently cross-links two decisions' audit trails

The invariant these tests protect is deliberately broader than the crash:

    No LLM-supplied value may land in a unique, indexed, or foreign-keyed column.

Teaching note — why assert on the *schema* and not just the outcome:

  Asserting "two runs produce different ids" would pass if someone re-introduced
  a model-supplied id that merely happened to vary. Asserting that ``decision_id``
  is absent from the tool schema pins the actual contract: the model is never
  asked for an identifier in the first place. Both tests are here because they
  fail for different reasons.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from pacca.agents.decision import DecisionAgent, DecisionContext, MedicalDirectorAgent
from pacca.models.authorization import AuthorizationDecision, ClinicalCase
from pacca.models.enums import AuthorizationStatus, ReviewTier


def _case() -> ClinicalCase:
    return ClinicalCase(
        patient_id="SYN-3117",
        primary_diagnosis_code="M54.16",
        procedure_code="72148",
    )


def _context() -> DecisionContext:
    return DecisionContext(case=_case(), relevant_guidelines="synthetic guideline text")


def _captured_response_model(calls: list[dict[str, Any]]) -> type:
    assert calls, "BaseAgent.execute was never called"
    return calls[-1]["response_model"]


#: What a deterministic model returns for identical input, including the exact
#: id observed in the wild during the B6 reproduction. ``model_construct``
#: bypasses validation so the fake works against both the buggy response model
#: (AuthorizationDecision) and the fixed one (DecisionDraft) — the test then
#: fails on its assertion rather than on a schema mismatch.
_MODEL_OUTPUT = {
    "decision_id": "dec_p_demo_72148",
    "status": AuthorizationStatus.AUTO_APPROVED,
    "confidence_score": 0.98,
    "rationale": "identical rationale every time",
    "cited_evidence_ids": [],
}


def _fake_execute_factory(
    calls: list[dict[str, Any]] | None = None,
    payload: dict[str, Any] | None = None,
) -> Any:
    async def _fake_execute(self: Any, *, user_input: str, response_model: type) -> Any:
        if calls is not None:
            calls.append({"user_input": user_input, "response_model": response_model})
        return response_model.model_construct(**(payload or _MODEL_OUTPUT))

    return _fake_execute


@pytest.mark.asyncio
async def test_decision_id_is_not_requested_from_the_model() -> None:
    """The tool schema handed to Claude must not contain a decision_id field."""
    calls: list[dict[str, Any]] = []

    with patch("pacca.agents.base.BaseAgent.execute", new=_fake_execute_factory(calls)):
        await DecisionAgent().run(_context())

    schema = _captured_response_model(calls).model_json_schema()
    assert "decision_id" not in schema["properties"], (
        "decision_id is exposed in the LLM tool schema — the model can supply a "
        "value that lands in a unique column (B6)"
    )


@pytest.mark.asyncio
async def test_repeat_runs_of_the_same_case_get_distinct_decision_ids() -> None:
    """The original crash: identical input must not yield an identical id."""
    with patch("pacca.agents.base.BaseAgent.execute", new=_fake_execute_factory()):
        first = await DecisionAgent().run(_context())
        second = await DecisionAgent().run(_context())

    assert first.decision_id and second.decision_id
    assert first.decision_id != second.decision_id, (
        "two runs of the same case produced the same decision_id — this is the "
        "UNIQUE constraint violation from B6"
    )


@pytest.mark.asyncio
async def test_a_model_supplied_decision_id_is_ignored() -> None:
    """Even if the model emits decision_id, it must not reach the decision."""
    with patch("pacca.agents.base.BaseAgent.execute", new=_fake_execute_factory()):
        decision = await DecisionAgent().run(_context())

    assert decision.decision_id != "dec_p_demo_72148"


@pytest.mark.asyncio
async def test_medical_director_review_also_mints_server_side() -> None:
    """Tier 2 has the same contract as Tier 1."""

    payload = {
        "decision_id": "dec_p_demo_72148",
        "status": AuthorizationStatus.AUTO_APPROVED,
        "confidence_score": 0.96,
        "rationale": "director rationale",
        "cited_evidence_ids": [],
    }

    tier1 = AuthorizationDecision(
        decision_id="TIER1-FIXED",
        status=AuthorizationStatus.IN_REVIEW,
        confidence_score=0.93,
        rationale="tier-1 rationale",
        review_tier_used=ReviewTier.AUTOMATED,
        model_id="claude-sonnet-4-5-20250929",
        prompt_version="v2.7",
    )

    with patch("pacca.agents.base.BaseAgent.execute", new=_fake_execute_factory(payload=payload)):
        reviewed = await MedicalDirectorAgent().run(_context(), tier1)

    assert reviewed.decision_id
    assert reviewed.decision_id != "dec_p_demo_72148", (
        "Tier 2 accepted a model-supplied decision_id (B6)"
    )
    assert reviewed.decision_id != tier1.decision_id
    assert reviewed.review_tier_used is ReviewTier.MEDICAL_DIRECTOR_AGENT


def test_authorization_decision_mints_its_own_id_when_not_given() -> None:
    """Hand-constructed decisions (pre-flight, tests) still get a unique id."""
    a = AuthorizationDecision(
        status=AuthorizationStatus.IN_REVIEW,
        confidence_score=0.1,
        rationale="r",
        review_tier_used=ReviewTier.AUTOMATED,
        model_id="claude-sonnet-4-5-20250929",
        prompt_version="v2.7",
    )
    b = AuthorizationDecision(
        status=AuthorizationStatus.IN_REVIEW,
        confidence_score=0.1,
        rationale="r",
        review_tier_used=ReviewTier.AUTOMATED,
        model_id="claude-sonnet-4-5-20250929",
        prompt_version="v2.7",
    )
    assert a.decision_id != b.decision_id


def test_explicit_decision_id_is_still_honoured() -> None:
    """The escape hatches (PREESC-…, SCOPE-…) pass an explicit id; keep that."""
    d = AuthorizationDecision(
        decision_id="PREESC-72148-1750000000",
        status=AuthorizationStatus.IN_REVIEW,
        confidence_score=0.1,
        rationale="r",
        review_tier_used=ReviewTier.AUTOMATED,
        model_id="claude-sonnet-4-5-20250929",
        prompt_version="v2.7",
    )
    assert d.decision_id == "PREESC-72148-1750000000"
