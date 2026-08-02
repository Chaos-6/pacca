"""Branch 7's data source: prior denials for the same patient.

Why this file exists
---------------------
Branch 7 (`prior_denial_same_service`) is one of the seven deterministic
pre-flight checks, and `CLAUDE.md` treats the escalation tree as a safety
boundary that overrides model confidence. It has never been able to fire in
production. `ClinicalRiskDetector._check_prior_denial` matches the current
procedure code against `prior_denial_codes`, the Orchestrator defaults that
parameter to `[]`, and **no route has ever populated it** — the detector's own
docstring describes the missing query as "a Week 3 enhancement".

So the branch fires only in the evaluation harness, where each golden case hands
it the codes directly (GC-008, GC-036). On a real submission it is unreachable:
a safety check that cannot trigger, guarded by tests that supply what production
does not. Verified 2026-07-31; see docs/AGENT_LESSONS.md.

These tests cover the lookup that closes that gap. Real SQLite, real
repositories — a mocked session cannot prove a row was or was not matched, and
the cross-patient case below is precisely a claim about what the SQL does.

The cross-patient test is the important one. This query reads rows belonging to
OTHER authorization requests, which is exactly the kind of widening the P-4
minimum-necessary scope guard exists to govern: legitimate within one patient's
own history, a PHI leak across patients.
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


def _request(request_id: str, patient_id: str, procedure_code: str) -> AuthorizationRequest:
    return AuthorizationRequest(
        request_id=request_id,
        patient_id=patient_id,
        provider_npi="1234567890",
        clinical_case=ClinicalCase(
            patient_id=patient_id,
            primary_diagnosis_code="C34.1",
            procedure_code=procedure_code,
            evidence=[
                EvidenceItem(
                    id="e1",
                    source_type=EvidenceSourceType.CLINICAL_NOTE,
                    description="note",
                    original_text="note",
                    confidence=0.9,
                )
            ],
        ),
    )


def _decision(status: AuthorizationStatus) -> AuthorizationDecision:
    return AuthorizationDecision(
        status=status,
        confidence_score=0.9,
        rationale="rationale",
        review_tier_used=ReviewTier.AUTOMATED,
        cited_evidence_ids=["e1"],
    )


async def _persist(session, request_id, patient_id, procedure_code, status) -> None:
    await AuthorizationRepository(session).create(_request(request_id, patient_id, procedure_code))
    await DecisionRepository(session).create(_decision(status), request_id=request_id)
    await session.commit()


async def test_returns_procedure_code_of_a_prior_denial(session) -> None:
    """The case Branch 7 exists for: same patient, same service, previously denied."""
    await _persist(session, "AUTH-1", "PAT-A", "J0135", AuthorizationStatus.DENIED)

    codes = await AuthorizationRepository(session).get_denied_procedure_codes("PAT-A")

    assert codes == ["J0135"]


async def test_does_not_leak_another_patients_denials(session) -> None:
    """The PHI boundary. A denial belonging to PAT-B must never surface for PAT-A.

    This query reads rows outside the current request, so it is exactly the
    widening the minimum-necessary scope guard governs. A leak here would also
    fire Branch 7 on a patient with no history at all — escalating a clean case
    on someone else's record.
    """
    await _persist(session, "AUTH-1", "PAT-B", "J0135", AuthorizationStatus.DENIED)

    codes = await AuthorizationRepository(session).get_denied_procedure_codes("PAT-A")

    assert codes == []


@pytest.mark.parametrize(
    "status",
    [
        AuthorizationStatus.AUTO_APPROVED,
        AuthorizationStatus.IN_REVIEW,
        AuthorizationStatus.PENDING,
    ],
)
async def test_only_denials_count(session, status) -> None:
    """An approved or escalated history is not a prior denial.

    Branch 7's question is "was this same service previously refused", not "has
    this patient been here before". Counting a prior IN_REVIEW would escalate
    every returning patient forever.
    """
    await _persist(session, "AUTH-1", "PAT-A", "J0135", status)

    codes = await AuthorizationRepository(session).get_denied_procedure_codes("PAT-A")

    assert codes == []


async def test_excludes_the_current_request(session) -> None:
    """A request must not find itself.

    The submit route persists the incoming request BEFORE the orchestrator runs,
    so by lookup time the current row already exists. Without the exclusion, a
    resubmission that is itself denied later is fine — but any request whose own
    decision lands first would self-trigger Branch 7.
    """
    await _persist(session, "AUTH-1", "PAT-A", "J0135", AuthorizationStatus.DENIED)

    codes = await AuthorizationRepository(session).get_denied_procedure_codes(
        "PAT-A", exclude_request_id="AUTH-1"
    )

    assert codes == []


async def test_no_history_returns_empty(session) -> None:
    codes = await AuthorizationRepository(session).get_denied_procedure_codes("PAT-NEW")

    assert codes == []


async def test_deduplicates_repeated_denials_of_the_same_service(session) -> None:
    """Three denials of one service is still one code for the detector.

    `_check_prior_denial` does a membership test, so duplicates change nothing
    functionally — but they would inflate the audit detail string and make the
    escalation reason read as though three different services were refused.
    """
    await _persist(session, "AUTH-1", "PAT-A", "J0135", AuthorizationStatus.DENIED)
    await _persist(session, "AUTH-2", "PAT-A", "J0135", AuthorizationStatus.DENIED)
    await _persist(session, "AUTH-3", "PAT-A", "J9271", AuthorizationStatus.DENIED)

    codes = await AuthorizationRepository(session).get_denied_procedure_codes("PAT-A")

    assert sorted(codes) == ["J0135", "J9271"]
