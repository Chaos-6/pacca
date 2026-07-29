"""
chg-24 (D4 spec §3.1): the request_id duplicate-submission contract.

Before this fix, `request_id`'s unique constraint (db/models.py) had NO
handling in the submit route at all -- a duplicate submission raised an
unhandled IntegrityError that the route's `except Exception` turned into an
HTTP 500 (verified by execution in the design spec). This is not a contract
change; it is the FIRST defined contract for the case. Five rows, all
audited, never a 500 (the fifth -- in-flight vs. died -- is FIX 1 from the
Validator's D4 review, closing a race the original spec's "no decision ->
resume" language didn't disambiguate):

  | Case                                             | Behaviour              |
  |---------------------------------------------------|------------------------|
  | duplicate id, decision exists, SAME case           | 200, idempotent replay |
  | duplicate id, decision exists, DIFFERENT case      | 409, refuse            |
  | duplicate id, no decision, request row YOUNG       | 409, in-flight, refuse |
  | duplicate id, no decision, request row OLD         | resume                 |
  | new id                                             | normal path            |

"Young" vs. "old" is a heuristic (request row age vs. `settings.agent_
timeout`), not a lease -- see `_resolve_duplicate_request_id`'s docstring in
authorizations.py for the full reasoning and its known window.

THE SAFETY TEST is `test_duplicate_different_patient_returns_409_not_stored_
decision` -- a client that reuses a request_id with a different patient must
NEVER receive that patient's stored decision. It is written first in this
file (before the replay/resume tests) per spec instruction, and
`test_safety_test_fails_against_naive_replay_implementation` proves it can
actually fail (P-010) by patching `_same_case` to always return True --
simulating a naive "any duplicate replays" implementation -- and watching
the safety test go RED.

Real SQLite session + real repositories (mirrors test_escalation_persistence.
py's fixture) -- a mocked session can't prove a row landed or didn't.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from pacca.api.routes import authorizations as authz_module
from pacca.api.routes.authorizations import orchestrator, rag_engine, submit_authorization
from pacca.db.models import Base
from pacca.db.repository import AuthorizationRepository, DecisionRepository
from pacca.integrations.vector_store import RetrievalOutcome
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


def _healthy_outcome() -> RetrievalOutcome:
    return RetrievalOutcome(
        text="Mock guideline content", mode="pipeline", degraded=False, reason=None
    )


def _request(
    request_id: str,
    patient_id: str,
    *,
    diagnosis_code: str = "C34.1",
    procedure_code: str = "J9271",
) -> AuthorizationRequest:
    return AuthorizationRequest(
        request_id=request_id,
        patient_id=patient_id,
        provider_npi="1234567890",
        clinical_case=ClinicalCase(
            patient_id=patient_id,
            primary_diagnosis_code=diagnosis_code,
            procedure_code=procedure_code,
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
        rationale="original decision — patient A",
        review_tier_used=ReviewTier.AUTOMATED,
        cited_evidence_ids=["e1"],
    )


async def _submit(session, request, monkeypatch, *, process_decision=None) -> AuthorizationDecision:
    mock_pd = process_decision or AsyncMock(return_value=_decision())
    monkeypatch.setattr(orchestrator, "process_decision", mock_pd)
    monkeypatch.setattr(rag_engine, "query", lambda *a, **k: _healthy_outcome())
    return await submit_authorization(request=request, session=session)


async def _backdate_request(session, request_id: str, *, seconds_ago: float) -> None:
    """Simulate a request row from a prior attempt that genuinely DIED
    (rather than one still in flight) by pushing `submitted_at` further into
    the past than `settings.agent_timeout` (FIX 1a's disambiguation) --
    otherwise a row inserted moments ago in a test is indistinguishable from
    one an in-flight submission is still working, and the resolver correctly
    (per FIX 1a) treats it as in-flight and refuses with 409."""
    from datetime import datetime, timedelta

    from sqlalchemy import update

    from pacca.db.models import AuthorizationRequestModel

    await session.execute(
        update(AuthorizationRequestModel)
        .where(AuthorizationRequestModel.request_id == request_id)
        .values(submitted_at=datetime.utcnow() - timedelta(seconds=seconds_ago))
    )
    await session.commit()


# =============================================================================
# THE SAFETY TEST -- written first, per spec instruction
# =============================================================================


@pytest.mark.asyncio
async def test_duplicate_different_patient_returns_409_not_stored_decision(session, monkeypatch):
    """Case 2 (spec §3.1): duplicate request_id, decision exists, DIFFERENT
    patient -> 409, and patient A's stored decision is NEVER handed back for
    patient B's resubmitted request_id. This is the cross-patient leak this
    system exists to prevent."""
    req_a = _request("AUTH-DUP-1", "P-PATIENT-A")
    first = await _submit(session, req_a, monkeypatch)
    assert first.status == AuthorizationStatus.AUTO_APPROVED

    # Same request_id, DIFFERENT patient (and different clinical case).
    req_b = _request("AUTH-DUP-1", "P-PATIENT-B", diagnosis_code="J44.0", procedure_code="94060")
    with pytest.raises(HTTPException) as exc_info:
        await _submit(session, req_b, monkeypatch)

    assert exc_info.value.status_code == 409
    # The response must not carry patient A's decision anywhere retrievable
    # from the exception raised to the client.
    assert "PA-" not in str(exc_info.value.detail) or first.decision_id not in str(
        exc_info.value.detail
    )

    # Exactly one request row (the original), exactly one decision row
    # (patient A's) -- B's submission left no trace of having been accepted.
    stored_request = await AuthorizationRepository(session).get_by_id("AUTH-DUP-1")
    assert stored_request is not None
    assert stored_request.patient_id == "P-PATIENT-A", (
        "the persisted request row must still be patient A's -- it must not "
        "have been overwritten by B's resubmission"
    )
    stored_decision = await DecisionRepository(session).get_by_request_id("AUTH-DUP-1")
    assert stored_decision is not None
    assert stored_decision.decision_id == first.decision_id


@pytest.mark.asyncio
async def test_safety_test_fails_against_naive_replay_implementation(session, monkeypatch):
    """P-010: watch the safety test fail. Patches `_same_case` to always
    return True -- simulating a NAIVE "any duplicate replays the stored
    decision" implementation that skips the cross-patient check -- and
    confirms the cross-patient case then WRONGLY returns 200 with patient
    A's decision instead of 409. This proves the safety test above is
    capable of catching the exact regression it exists to catch."""
    req_a = _request("AUTH-DUP-NAIVE-1", "P-PATIENT-A")
    first = await _submit(session, req_a, monkeypatch)

    monkeypatch.setattr(authz_module, "_same_case", lambda existing, request: True)

    req_b = _request(
        "AUTH-DUP-NAIVE-1", "P-PATIENT-B", diagnosis_code="J44.0", procedure_code="94060"
    )
    # Under the naive stand-in, no HTTPException(409) is raised at all --
    # the route happily returns patient A's decision for patient B's request.
    leaked = await _submit(session, req_b, monkeypatch)
    assert leaked.decision_id == first.decision_id, (
        "expected the naive implementation to leak patient A's decision "
        "(demonstrating the safety test catches this) -- if this assertion "
        "itself now fails, the naive stand-in stopped reproducing the bug"
    )


# =============================================================================
# The other three cases
# =============================================================================


@pytest.mark.asyncio
async def test_duplicate_same_case_replays_idempotently_without_new_llm_call(session, monkeypatch):
    """Case 1: duplicate request_id, decision exists, SAME case -> 200, the
    EXISTING decision returned, exactly one request row, exactly one decision
    row, no new LLM call (orchestrator.process_decision not invoked again)."""
    req = _request("AUTH-DUP-2", "P-PATIENT-SAME")
    mock_pd = AsyncMock(return_value=_decision())
    first = await _submit(session, req, monkeypatch, process_decision=mock_pd)
    assert mock_pd.call_count == 1

    # Identical resubmission (same request_id, same patient, same case).
    second_pd = AsyncMock(return_value=_decision())
    replay = await _submit(session, req, monkeypatch, process_decision=second_pd)

    assert replay.decision_id == first.decision_id
    assert second_pd.call_count == 0, "no new LLM call should be made on an idempotent replay"

    # Exactly one of each row -- the replay did not create a second decision.
    engine = session.bind
    async with async_sessionmaker(engine, expire_on_commit=False)() as verify:
        from sqlalchemy import func, select

        from pacca.db.models import AuthorizationDecisionModel, AuthorizationRequestModel

        req_count = (
            await verify.execute(
                select(func.count(AuthorizationRequestModel.id)).where(
                    AuthorizationRequestModel.request_id == "AUTH-DUP-2"
                )
            )
        ).scalar_one()
        dec_count = (
            await verify.execute(
                select(func.count(AuthorizationDecisionModel.id)).where(
                    AuthorizationDecisionModel.request_id == "AUTH-DUP-2"
                )
            )
        ).scalar_one()
    assert req_count == 1
    assert dec_count == 1


@pytest.mark.asyncio
async def test_duplicate_no_decision_resumes_processing(session, monkeypatch):
    """Case 3: duplicate request_id, NO decision yet, and the request row is
    OLDER than agent_timeout -- a prior attempt is assumed to have genuinely
    died between T1 and T2 (FIX 1a's disambiguation) -> resumes processing
    against the already-persisted row rather than re-inserting or raising,
    still exactly one request row, and now completes with a real decision."""
    req = _request("AUTH-DUP-3", "P-PATIENT-RESUME")

    # Simulate a prior attempt that reached T1 but died before T2: persist
    # the request row directly, write no decision, and backdate it past
    # agent_timeout so the resolver reads it as died, not in-flight.
    await AuthorizationRepository(session).create(req)
    await session.commit()
    await _backdate_request(session, "AUTH-DUP-3", seconds_ago=120)

    assert await DecisionRepository(session).get_by_request_id("AUTH-DUP-3") is None

    resumed = await _submit(session, req, monkeypatch)
    assert resumed.status == AuthorizationStatus.AUTO_APPROVED

    engine = session.bind
    async with async_sessionmaker(engine, expire_on_commit=False)() as verify:
        from sqlalchemy import func, select

        from pacca.db.models import AuthorizationRequestModel

        req_count = (
            await verify.execute(
                select(func.count(AuthorizationRequestModel.id)).where(
                    AuthorizationRequestModel.request_id == "AUTH-DUP-3"
                )
            )
        ).scalar_one()
    assert req_count == 1, "resuming must not re-insert a second request row"

    decision_row = await DecisionRepository(session).get_by_request_id("AUTH-DUP-3")
    assert decision_row is not None


@pytest.mark.asyncio
async def test_new_request_id_takes_normal_path_never_500s(session, monkeypatch):
    """Case 4 / spec item 7: a brand-new request_id is unaffected by the
    duplicate-handling code path at all -- normal processing, no 500."""
    req = _request("AUTH-DUP-NEW-1", "P-PATIENT-NEW")
    result = await _submit(session, req, monkeypatch)
    assert result.status == AuthorizationStatus.AUTO_APPROVED


@pytest.mark.asyncio
async def test_audit_actions_for_each_duplicate_case(session, monkeypatch):
    """Each of the duplicate-handling outcomes writes the specific audit
    action spec §3.1 / FIX 1a names, so the contract is queryable after the
    fact, not just observable in the HTTP response."""
    from pacca.db.repository import AuditRepository

    # Same-case replay -> submission.idempotent_replay
    req = _request("AUTH-DUP-AUDIT-1", "P-A")
    await _submit(session, req, monkeypatch)
    await _submit(session, req, monkeypatch)
    rows = await AuditRepository(session).get_by_request_id("AUTH-DUP-AUDIT-1")
    assert any(r.action == "submission.idempotent_replay" for r in rows)

    # Different-case -> submission.id_reused_for_different_case
    req2 = _request("AUTH-DUP-AUDIT-2", "P-B")
    await _submit(session, req2, monkeypatch)
    req2_diff = _request("AUTH-DUP-AUDIT-2", "P-C", diagnosis_code="J44.0", procedure_code="94060")
    with pytest.raises(HTTPException):
        await _submit(session, req2_diff, monkeypatch)
    rows2 = await AuditRepository(session).get_by_request_id("AUTH-DUP-AUDIT-2")
    assert any(r.action == "submission.id_reused_for_different_case" for r in rows2)

    # No decision yet, request row OLD (past agent_timeout) -> died, resume
    # -> submission.resumed_after_failure
    req3 = _request("AUTH-DUP-AUDIT-3", "P-D")
    await AuthorizationRepository(session).create(req3)
    await session.commit()
    await _backdate_request(session, "AUTH-DUP-AUDIT-3", seconds_ago=120)
    await _submit(session, req3, monkeypatch)
    rows3 = await AuditRepository(session).get_by_request_id("AUTH-DUP-AUDIT-3")
    assert any(r.action == "submission.resumed_after_failure" for r in rows3)

    # No decision yet, request row YOUNG (within agent_timeout) -> in-flight
    # -> submission.in_flight_conflict, 409, no orchestrator call
    req4 = _request("AUTH-DUP-AUDIT-4", "P-E")
    await AuthorizationRepository(session).create(req4)
    await session.commit()
    in_flight_pd = AsyncMock(return_value=_decision())
    with pytest.raises(HTTPException) as exc_info:
        await _submit(session, req4, monkeypatch, process_decision=in_flight_pd)
    assert exc_info.value.status_code == 409
    assert in_flight_pd.call_count == 0, "in-flight conflict must not invoke the orchestrator"
    rows4 = await AuditRepository(session).get_by_request_id("AUTH-DUP-AUDIT-4")
    assert any(r.action == "submission.in_flight_conflict" for r in rows4)
