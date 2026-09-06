"""
Model and prompt provenance on a decision — SCHEMA-INV-04.

SDD v3.0 requires every AuthorizationDecision to name the substrate that
produced it. The requirement exists because a pinned model identifier is not
by itself a record of what ran: docs/DECISIONS.md:136-140 documents a full
accuracy evaluation that executed on a substituted model, recoverable
afterwards only from prose a human typed into a markdown file while the
iteration manifest still declared the model that had not run.

Three provenance states must stay distinguishable, and the tests below pin
each one because collapsing any two of them destroys the requirement:

    none:deterministic     code decided this; no model was involved
    unknown:pre-provenance the row predates provenance capture
    <a real model id>      that model decided this

"Unknown" is the one that matters most. It is tempting to fill it with the
currently configured model on read, since that is almost always what actually
ran — but "almost always" is the property the requirement exists to refuse. A
decision whose substrate is not recoverable has to say so.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pacca.api.routes.authorizations import _decision_from_model
from pacca.db.models import AuthorizationDecisionModel
from pacca.models.authorization import DETERMINISTIC_PROVENANCE, AuthorizationDecision
from pacca.models.enums import AuthorizationStatus, ReviewTier

LEGACY_SENTINEL = "unknown:pre-provenance"

AGENT_TIERS = (ReviewTier.AUTOMATED, ReviewTier.MEDICAL_DIRECTOR_AGENT)


def _decision(**overrides: object) -> AuthorizationDecision:
    kwargs: dict[str, object] = {
        "decision_id": "PA-provenance-0001",
        "status": AuthorizationStatus.AUTO_APPROVED,
        "confidence_score": 0.97,
        "rationale": "Meets criteria.",
        "review_tier_used": ReviewTier.AUTOMATED,
        "timestamp": datetime.now(UTC),
    }
    kwargs.update(overrides)
    return AuthorizationDecision(**kwargs)  # type: ignore[arg-type]


class TestAgentDecisionsMustNameTheirSubstrate:
    """The validator's job: an agent tier cannot carry the deterministic sentinel."""

    @pytest.mark.parametrize("tier", AGENT_TIERS)
    def test_agent_tier_rejects_the_deterministic_default(self, tier: ReviewTier) -> None:
        with pytest.raises(ValueError, match="SCHEMA-INV-04"):
            _decision(review_tier_used=tier)

    @pytest.mark.parametrize(
        ("model_id", "prompt_version"),
        [
            (DETERMINISTIC_PROVENANCE, "v2.7"),
            ("claude-sonnet-4-5-20250929", DETERMINISTIC_PROVENANCE),
            ("", "v2.7"),
            ("claude-sonnet-4-5-20250929", ""),
        ],
    )
    def test_either_field_missing_is_rejected(self, model_id: str, prompt_version: str) -> None:
        """Half-recorded provenance answers neither question, so it is not accepted."""
        with pytest.raises(ValueError, match="SCHEMA-INV-04"):
            _decision(model_id=model_id, prompt_version=prompt_version)

    def test_agent_tier_with_real_provenance_is_accepted(self) -> None:
        decision = _decision(model_id="claude-sonnet-4-5-20250929", prompt_version="v2.7")
        assert decision.model_id == "claude-sonnet-4-5-20250929"
        assert decision.prompt_version == "v2.7"

    def test_deterministic_tier_keeps_the_sentinel(self) -> None:
        """A human-tier decision is not model-produced; the sentinel is the truth."""
        decision = _decision(review_tier_used=ReviewTier.HUMAN)
        assert decision.model_id == DETERMINISTIC_PROVENANCE
        assert decision.prompt_version == DETERMINISTIC_PROVENANCE


def _row(model_id: str, prompt_version: str) -> AuthorizationDecisionModel:
    return AuthorizationDecisionModel(
        decision_id="PA-replay-0001",
        request_id="AUTH-REPLAY-1",
        outcome=AuthorizationStatus.AUTO_APPROVED.value,
        confidence_score=0.97,
        rationale_data={"text": "Meets criteria."},
        decided_at=datetime.now(UTC),
        decided_by=ReviewTier.AUTOMATED.value,
        model_id=model_id,
        prompt_version=prompt_version,
    )


class TestReplayCarriesTheStoredProvenance:
    """
    An idempotent replay reports what produced the original decision.

    This is where the requirement was actually dropped: the write path recorded
    provenance correctly while `_decision_from_model` rebuilt the domain object
    without it, so every replayed agent decision fell back to the deterministic
    default and 500'd on its own validator. The route's error path stringifies
    the exception, so the visible symptom was a 500 on a duplicate request_id —
    a correctness bug wearing an availability bug's clothes.
    """

    def test_replay_reports_the_model_that_decided(self) -> None:
        decision = _decision_from_model(_row("claude-sonnet-4-5-20250929", "v2.7"))
        assert decision.model_id == "claude-sonnet-4-5-20250929"
        assert decision.prompt_version == "v2.7"

    def test_legacy_row_replays_as_unknown_not_as_deterministic(self) -> None:
        """
        A pre-migration row must not be laundered into either of the other states.

        Reading it as `none:deterministic` would assert no model was involved in
        a case a model decided. Substituting the configured model would assert a
        specific substrate the row does not record. Both are answers to
        "which model decided this" that the data cannot support.
        """
        decision = _decision_from_model(_row(LEGACY_SENTINEL, LEGACY_SENTINEL))

        assert decision.model_id == LEGACY_SENTINEL
        assert decision.prompt_version == LEGACY_SENTINEL
        assert decision.model_id != DETERMINISTIC_PROVENANCE

    def test_three_provenance_states_stay_distinct(self) -> None:
        assert len({DETERMINISTIC_PROVENANCE, LEGACY_SENTINEL, "claude-sonnet-4-5-20250929"}) == 3


class TestStorageDefaultMatchesTheMigration:
    """
    The column default is load-bearing: it is what pre-migration rows get.

    Pinning it here means a change to the ORM default that the Alembic revision
    does not mirror fails a fast unit test rather than surfacing as a
    migration-drift CI failure with no explanation of why the value matters.
    """

    @pytest.mark.parametrize("column", ["model_id", "prompt_version"])
    def test_server_default_is_the_legacy_sentinel(self, column: str) -> None:
        col = AuthorizationDecisionModel.__table__.columns[column]
        assert col.server_default is not None, f"{column} must backfill pre-existing rows"
        assert col.server_default.arg == LEGACY_SENTINEL
        assert not col.nullable, (
            f"{column} is NOT NULL on purpose: NULL would force every reader to "
            "decide what absence means, which is the ambiguity the sentinels remove."
        )
