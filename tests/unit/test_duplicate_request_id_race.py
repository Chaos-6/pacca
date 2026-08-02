"""
chg-24 FIX 1 (Validator's D4 review, HIGH): a duplicate `request_id` arriving
while the FIRST submission is still in flight (T1 committed, no decision yet,
genuinely still running) must not be resumed as if the first attempt had
died -- resuming produces a SECOND decision row for one request_id, and
because `authorization_decisions.request_id` carries no UNIQUE constraint,
every subsequent submission of that id then permanently 500s
(`MultipleResultsFound` from the old `scalar_one_or_none()` lookup).

Two tests here, real SQLite (file-based, genuinely separate connections --
same technique as test_txn_concurrency.py):

  1. `test_concurrent_duplicate_while_first_in_flight_gets_409_not_second_
     decision` -- the race itself: submit A, and once A is confirmedly
     inside its own orchestrator call (T1 committed, no decision yet),
     submit the SAME request_id concurrently. The second caller must get
     409 (`submission.in_flight_conflict`), NOT resume, NOT invoke the
     orchestrator a second time. After A completes: exactly one decision
     row. A THIRD, later duplicate submission (decision now exists) must
     still not 500 -- it replays idempotently.
  2. `test_pre_existing_two_decision_state_returns_defined_answer_not_500`
     -- directly constructs the anomalous two-decision state (bypassing the
     route, simulating a state a bug already created) and asserts a
     resubmission returns a defined 200 replay of the earliest decision,
     audited as an anomaly, rather than crashing.

See `tests/integration/test_submit_postgres.py` for the same two tests
against real Postgres 16 (the Validator's probe specifically worried about
PG's `current transaction is aborted` state after the IntegrityError).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from pacca.api.routes.authorizations import orchestrator, rag_engine, submit_authorization
from pacca.db.models import Base
from pacca.db.repository import AuditRepository, AuthorizationRepository, DecisionRepository
from pacca.integrations.vector_store import RetrievalOutcome
from pacca.models.authorization import AuthorizationDecision, AuthorizationRequest
from pacca.models.clinical import ClinicalCase, EvidenceItem
from pacca.models.enums import AuthorizationStatus, EvidenceSourceType, ReviewTier

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

REQUEST_ID = "AUTH-RACE-1"
PATIENT_ID = "P-RACE-1"


def _healthy_outcome() -> RetrievalOutcome:
    return RetrievalOutcome(
        text="Mock guideline content", mode="pipeline", degraded=False, reason=None
    )


def _request(request_id: str = REQUEST_ID, patient_id: str = PATIENT_ID) -> AuthorizationRequest:
    return AuthorizationRequest(
        request_id=request_id,
        patient_id=patient_id,
        provider_npi="1234567890",
        clinical_case=ClinicalCase(
            patient_id=patient_id,
            primary_diagnosis_code="C34.1",
            procedure_code="J9271",
            evidence=[
                EvidenceItem(
                    id="e1",
                    source_type=EvidenceSourceType.CLINICAL_NOTE,
                    description="Stage IIIA NSCLC",
                    original_text="Patient presents with stage IIIA NSCLC.",
                    confidence=0.95,
                )
            ],
        ),
    )


def _decision() -> AuthorizationDecision:
    return AuthorizationDecision(
        status=AuthorizationStatus.AUTO_APPROVED,
        confidence_score=0.97,
        rationale="test",
        review_tier_used=ReviewTier.AUTOMATED,
        cited_evidence_ids=["e1"],
    )


@pytest.fixture
async def db_url(tmp_path) -> AsyncGenerator[str, None]:
    db_path = tmp_path / "race.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    eng = create_async_engine(url)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await eng.dispose()
    yield url


@pytest.mark.asyncio
async def test_concurrent_duplicate_while_first_in_flight_gets_409_not_second_decision(
    db_url, monkeypatch
):
    engine = create_async_engine(db_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    a_reached_llm = asyncio.Event()
    b_may_release_a = asyncio.Event()
    orchestrator_call_count = 0

    async def mock_process_decision(decision_ctx, *, audit, correlation_id, **_kwargs):
        # **_kwargs so a new orchestrator argument does not break stubs that
        # only care that the call happened (chg-32 added prior_denial_codes).
        nonlocal orchestrator_call_count
        orchestrator_call_count += 1
        a_reached_llm.set()
        await b_may_release_a.wait()
        return _decision()

    monkeypatch.setattr(orchestrator, "process_decision", mock_process_decision)
    monkeypatch.setattr(rag_engine, "query", lambda *a, **k: _healthy_outcome())

    async def run_a():
        session_a = maker()
        try:
            return await submit_authorization(request=_request(), session=session_a)
        finally:
            await session_a.close()

    async def run_b():
        await a_reached_llm.wait()
        session_b = maker()
        try:
            # SAME request_id, same case -- B is a genuine duplicate arriving
            # while A is still inside its own orchestrator call.
            return await submit_authorization(request=_request(), session=session_b)
        except Exception as e:
            return e
        finally:
            b_may_release_a.set()
            await session_b.close()

    results = await asyncio.gather(run_a(), run_b())
    result_a, result_b = results

    assert isinstance(result_a, AuthorizationDecision)
    assert isinstance(result_b, HTTPException), (
        f"expected B (in-flight duplicate) to be refused with an HTTPException, got {result_b!r}"
    )
    assert result_b.status_code == 409
    assert "processed" in str(result_b.detail).lower()

    # The orchestrator must have been invoked exactly ONCE -- B never got a
    # chance to start a second run against the same request_id.
    assert orchestrator_call_count == 1

    async with maker() as verify:
        decisions = await DecisionRepository(verify).list_by_request_id(REQUEST_ID)
    assert len(decisions) == 1, (
        f"expected exactly 1 decision row after the race, got {len(decisions)} -- "
        "a second one means the in-flight guard did not hold"
    )

    async with maker() as audit_session:
        audit_rows = await AuditRepository(audit_session).get_by_request_id(REQUEST_ID)
    assert any(r.action == "submission.in_flight_conflict" for r in audit_rows)

    # A THIRD, later duplicate submission -- decision now exists -- must
    # still never 500: idempotent replay.
    async with maker() as session_c:
        third_pd = AsyncMock(return_value=_decision())
        monkeypatch.setattr(orchestrator, "process_decision", third_pd)
        result_c = await submit_authorization(request=_request(), session=session_c)
    assert isinstance(result_c, AuthorizationDecision)
    assert result_c.decision_id == result_a.decision_id
    assert third_pd.call_count == 0, "the replay must not invoke the orchestrator again"

    await engine.dispose()


@pytest.mark.asyncio
async def test_pre_existing_two_decision_state_returns_defined_answer_not_500(db_url, monkeypatch):
    """Simulates the anomaly a bug (or, before this fix, the race above)
    could already have created: two decision rows for one request_id. A
    resubmission must return a defined answer -- the earliest decision,
    replayed -- rather than crashing with `MultipleResultsFound`."""
    engine = create_async_engine(db_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as session:
        req = _request("AUTH-ANOMALY-1", "P-ANOMALY-1")
        await AuthorizationRepository(session).create(req)
        await session.commit()

        first_decision = AuthorizationDecision(
            decision_id="PA-first0000000000",
            status=AuthorizationStatus.AUTO_APPROVED,
            confidence_score=0.97,
            rationale="first decision (earliest)",
            review_tier_used=ReviewTier.AUTOMATED,
        )
        second_decision = AuthorizationDecision(
            decision_id="PA-second00000000",
            status=AuthorizationStatus.IN_REVIEW,
            confidence_score=0.5,
            rationale="second decision (anomalous duplicate)",
            review_tier_used=ReviewTier.AUTOMATED,
        )
        await DecisionRepository(session).create(first_decision, request_id="AUTH-ANOMALY-1")
        await session.commit()
        await DecisionRepository(session).create(second_decision, request_id="AUTH-ANOMALY-1")
        await session.commit()

        decisions = await DecisionRepository(session).list_by_request_id("AUTH-ANOMALY-1")
        assert len(decisions) == 2, (
            "test setup: two decision rows must exist before the resubmission"
        )

    async with maker() as session2:
        no_call_pd = AsyncMock(return_value=_decision())
        monkeypatch.setattr(orchestrator, "process_decision", no_call_pd)
        monkeypatch.setattr(rag_engine, "query", lambda *a, **k: _healthy_outcome())
        result = await submit_authorization(
            request=_request("AUTH-ANOMALY-1", "P-ANOMALY-1"), session=session2
        )

    assert isinstance(result, AuthorizationDecision), (
        "a pre-existing two-decision anomaly must produce a defined answer, not raise"
    )
    assert result.decision_id == "PA-first0000000000", "the earliest decision is authoritative"
    assert no_call_pd.call_count == 0

    async with maker() as verify:
        audit_rows = await AuditRepository(verify).get_by_request_id("AUTH-ANOMALY-1")
    assert any(r.action == "submission.multiple_decisions_anomaly" for r in audit_rows)

    await engine.dispose()
