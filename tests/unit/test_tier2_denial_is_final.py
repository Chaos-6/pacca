"""
Tier 2 cannot be talked out of a denial by its own confidence — SC-02, C-HARD-01.

`_run_medical_director` discarded the Medical Director Agent's status and
recomputed it from the confidence score alone:

    if md_decision.confidence_score >= auto_approve_confidence_threshold:
        md_decision.status = AUTO_APPROVED
    else:
        md_decision.status = IN_REVIEW

That reads `confidence_score` as "confidence the request should be approved".
The agent produces it as "confidence in the determination it just made", which
is a different quantity, and the two come apart exactly on a denial: a Medical
Director that denies a case *and is sure about it* had its denial rewritten
into an autonomous approval, while the same denial held with less conviction
went to a human. Doubt was the only thing routing these cases safely.

The inversion is worst where the stakes are highest. Tier 2 exists because
Tier 1 was uncertain, so every case reaching it is already ambiguous, and a
confident denial there is the strongest signal in the system that a human
should not be bypassed.

These tests assert the decision the agent reached is the decision that stands.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from pacca.agents.decision import DecisionContext
from pacca.agents.orchestrator import Orchestrator
from pacca.config.settings import effective_settings
from pacca.models.enums import AuthorizationStatus
from tests.unit.test_escalation_tree import make_case, make_decision

if TYPE_CHECKING:
    from pacca.models.authorization import AuthorizationDecision

THRESHOLD = effective_settings().auto_approve_confidence_threshold


def _ctx() -> DecisionContext:
    return DecisionContext(case=make_case(), relevant_guidelines="")


async def _tier2(status: AuthorizationStatus, confidence: float) -> AuthorizationDecision:
    """Run the Tier-2 helper with a Medical Director that returns `status`."""
    orch = Orchestrator()
    orch.medical_director_agent.run = AsyncMock(  # type: ignore[method-assign]
        return_value=make_decision(status=status, confidence=confidence)
    )
    return await orch._run_medical_director(
        context=_ctx(),
        tier1_decision=make_decision(status=AuthorizationStatus.IN_REVIEW, confidence=0.6),
        audit=None,
        correlation_id="corr-tier2",
    )


class TestAConfidentDenialIsNotAnApproval:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("confidence", [THRESHOLD, 0.97, 0.99, 1.0])
    async def test_denial_never_becomes_an_autonomous_approval(self, confidence: float) -> None:
        decision = await _tier2(AuthorizationStatus.DENIED, confidence)
        assert decision.status is not AuthorizationStatus.AUTO_APPROVED, (
            f"A Medical Director denial held at confidence {confidence} was rewritten "
            "to an autonomous approval. Confidence measures conviction in the "
            "determination, not support for approving."
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("confidence", [THRESHOLD, 0.97, 1.0, 0.55])
    async def test_denial_routes_to_a_human(self, confidence: float) -> None:
        """
        Tier 2 mirrors Tier 1 rather than inventing a denial policy of its own.

        `select_confidence_branch` auto-approves only on high confidence AND an
        AUTO_APPROVED status; every other Tier-1 outcome, a confident denial
        included, goes to human review. Preserving DENIED here instead would
        make Tier 2 *more* autonomous than Tier 1, which is backwards: Tier 2
        exists because Tier 1 found the case ambiguous.

        The denial is not lost. Its rationale rides on the decision and the
        Tier-2 audit record captures the status the agent returned; what changes
        is that a human issues the adverse determination.
        """
        decision = await _tier2(AuthorizationStatus.DENIED, confidence)
        assert decision.status is AuthorizationStatus.IN_REVIEW


class TestApprovalStillRequiresConfidence:
    """The threshold is not removed — it still gates autonomy in the approve direction."""

    @pytest.mark.asyncio
    async def test_confident_approval_is_autonomous(self) -> None:
        decision = await _tier2(AuthorizationStatus.AUTO_APPROVED, 0.97)
        assert decision.status is AuthorizationStatus.AUTO_APPROVED

    @pytest.mark.asyncio
    @pytest.mark.parametrize("confidence", [0.0, 0.5, 0.94])
    async def test_unconfident_approval_goes_to_a_human(self, confidence: float) -> None:
        decision = await _tier2(AuthorizationStatus.AUTO_APPROVED, confidence)
        assert decision.status is AuthorizationStatus.IN_REVIEW, (
            "An approval the agent is not confident in must not be granted "
            "autonomously; that direction of the threshold is the autonomy boundary."
        )

    @pytest.mark.asyncio
    async def test_threshold_boundary_is_inclusive(self) -> None:
        assert (await _tier2(AuthorizationStatus.AUTO_APPROVED, THRESHOLD)).status is (
            AuthorizationStatus.AUTO_APPROVED
        )


class TestIndeterminateOutcomesRouteToAHuman:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "status",
        [
            AuthorizationStatus.IN_REVIEW,
            AuthorizationStatus.INFORMATION_NEEDED,
            AuthorizationStatus.PENDING,
        ],
    )
    async def test_non_terminal_status_never_becomes_an_approval(
        self, status: AuthorizationStatus
    ) -> None:
        """High confidence in "I need more information" is not support for approving."""
        decision = await _tier2(status, 0.99)
        assert decision.status is not AuthorizationStatus.AUTO_APPROVED
