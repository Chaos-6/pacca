"""
The Medical Director review queue is backed by real escalated decisions.

Failure this locks down
-----------------------
`frontend/src/components/DirectorQueue.tsx` rendered a hardcoded one-element
`DEMO_QUEUE` array. The UI showed the same synthetic case whether the system had
escalated nothing or a hundred cases, and no endpoint existed to ask.

The obvious backing query — `list_requests(status=IN_REVIEW)` — returns nothing,
because the submit route never updates `authorization_requests.status` after the
orchestrator runs; every row still reads "submitted". The queue is therefore
keyed on the DECISION's outcome, joined back to its request. These tests assert
that, so a future refactor to the request-status filter fails here rather than
silently emptying the queue.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from pacca.db.models import Base
from pacca.db.repository import AuthorizationRepository, DecisionRepository
from pacca.models.authorization import AuthorizationDecision, AuthorizationRequest
from pacca.models.clinical import ClinicalCase, EvidenceItem
from pacca.models.enums import AuthorizationStatus, EvidenceSourceType, ReviewTier


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


def _request(request_id: str, age: int | None = 47) -> AuthorizationRequest:
    return AuthorizationRequest(
        request_id=request_id,
        patient_id=f"P-{request_id}",
        provider_npi="1234567890",
        clinical_case=ClinicalCase(
            patient_id=f"P-{request_id}",
            primary_diagnosis_code="M54.5",
            procedure_code="72148",
            patient_age=age,
            clinical_notes="Low back pain for 2 weeks. No red flags.",
            evidence=[
                EvidenceItem(
                    id="e1",
                    source_type=EvidenceSourceType.CLINICAL_NOTE,
                    description="Low back pain, 2 weeks",
                    original_text="Patient reports 2 weeks of low back pain.",
                    confidence=0.9,
                )
            ],
        ),
    )


def _decision(status: AuthorizationStatus, rationale: str) -> AuthorizationDecision:
    return AuthorizationDecision(
        status=status,
        confidence_score=0.82,
        rationale=rationale,
        review_tier_used=ReviewTier.AUTOMATED,
        model_id="claude-sonnet-4-5-20250929",
        prompt_version="v2.7",
    )


async def _persist(session, request_id: str, status: AuthorizationStatus, rationale: str) -> None:
    await AuthorizationRepository(session).create(_request(request_id))
    await DecisionRepository(session).create(_decision(status, rationale), request_id=request_id)
    await session.commit()


# =============================================================================
# The repository query
# =============================================================================


@pytest.mark.asyncio
async def test_queue_returns_escalated_cases(session):
    await _persist(session, "AUTH-Q-1", AuthorizationStatus.IN_REVIEW, "needs a human")

    rows = await AuthorizationRepository(session).list_escalated_for_review()

    assert len(rows) == 1
    decision, request = rows[0]
    assert request.request_id == "AUTH-Q-1"
    assert decision.outcome == AuthorizationStatus.IN_REVIEW.value
    assert decision.rationale_data["text"] == "needs a human"


@pytest.mark.asyncio
async def test_queue_excludes_decided_cases(session):
    """Approved and denied cases are not awaiting review."""
    await _persist(session, "AUTH-Q-OK", AuthorizationStatus.AUTO_APPROVED, "meets criteria")
    await _persist(session, "AUTH-Q-NO", AuthorizationStatus.DENIED, "fails criteria")
    await _persist(session, "AUTH-Q-HUMAN", AuthorizationStatus.IN_REVIEW, "ambiguous")

    rows = await AuthorizationRepository(session).list_escalated_for_review()

    assert [r.request_id for _, r in rows] == ["AUTH-Q-HUMAN"]


@pytest.mark.asyncio
async def test_queue_is_empty_when_nothing_is_escalated(session):
    """An empty queue is a real answer, not a prompt to show sample data."""
    await _persist(session, "AUTH-Q-OK", AuthorizationStatus.AUTO_APPROVED, "meets criteria")

    assert await AuthorizationRepository(session).list_escalated_for_review() == []


@pytest.mark.asyncio
async def test_queue_is_not_keyed_on_the_request_status_column(session):
    """
    The rows the queue must return still carry status "submitted".

    This is the trap: filtering requests by AuthorizationStatus.IN_REVIEW reads
    naturally and returns an empty queue forever, because nothing updates that
    column after the orchestrator runs.
    """
    await _persist(session, "AUTH-Q-1", AuthorizationStatus.IN_REVIEW, "needs a human")
    repo = AuthorizationRepository(session)

    assert await repo.list_requests(status=AuthorizationStatus.IN_REVIEW) == []
    assert len(await repo.list_escalated_for_review()) == 1


@pytest.mark.asyncio
async def test_queue_is_oldest_first(session):
    """A review queue is worked front to back, unlike the newest-first listing."""
    for i in range(3):
        await _persist(session, f"AUTH-Q-{i}", AuthorizationStatus.IN_REVIEW, f"case {i}")

    rows = await AuthorizationRepository(session).list_escalated_for_review()

    assert [r.request_id for _, r in rows] == ["AUTH-Q-0", "AUTH-Q-1", "AUTH-Q-2"]


@pytest.mark.asyncio
async def test_queue_respects_the_limit(session):
    for i in range(5):
        await _persist(session, f"AUTH-Q-{i}", AuthorizationStatus.IN_REVIEW, f"case {i}")

    rows = await AuthorizationRepository(session).list_escalated_for_review(limit=2)

    assert len(rows) == 2


# =============================================================================
# The response contract
# =============================================================================


def test_response_declares_that_resolution_is_unsupported():
    """
    The UI must learn from the API — not a frontend constant — that acting on a
    case does not clear it. /feedback records a precedent; nothing records a
    disposition, so a reviewed case reappears on the next fetch.
    """
    from pacca.api.routes.authorizations import ReviewQueueResponse

    assert ReviewQueueResponse(items=[]).resolution_supported is False


def test_queue_item_carries_no_name_or_date_of_birth():
    """The payload is opaque-id + age only, per the repo's PHI convention."""
    from pacca.api.routes.authorizations import ReviewQueueItem

    fields = set(ReviewQueueItem.model_fields)
    assert not fields & {"patient_name", "name", "dob", "date_of_birth", "birth_date"}
    assert "patient_ref" in fields


def test_review_queue_endpoint_requires_authentication():
    """The queue lists clinical detail; it must not be readable anonymously."""
    from pacca.api.auth import verify_token
    from pacca.api.routes.authorizations import router

    route = next(r for r in router.routes if getattr(r, "path", None) == "/review-queue")
    assert any(d.call is verify_token for d in route.dependant.dependencies)
