"""
chg-22: every escalation exit from `submit_authorization` must persist a
decision row, or the escalation is invisible to `GET /review-queue`
(`AuthorizationRepository.list_escalated_for_review`, an INNER JOIN of
decisions to their request).

The defect this file locks down: the `ScopeViolation` except-handler built an
IN_REVIEW `AuthorizationDecision` and returned it WITHOUT ever calling
`DecisionRepository.create()` -- a "routed to human review" no human could
ever see. Same class of bug chg-20 already fixed for the RAG-degraded
escalation.

These tests use a REAL SQLite session + REAL repositories (mirrors
tests/unit/test_review_queue.py's fixture), not mocks -- a mocked session
cannot see whether a row actually landed in the table. Agents that would make
real Claude API calls (DecisionAgent, MedicalDirectorAgent, and the triage
agents) are mocked at the agent-method boundary, matching
tests/unit/test_escalation_tree.py's pattern, so the ORCHESTRATOR's own
branching logic runs for real -- only the LLM call itself is faked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from pacca.api.routes.authorizations import orchestrator, rag_engine, submit_authorization
from pacca.config.settings import apply_overrides, clear_all_overrides
from pacca.db.models import Base
from pacca.db.repository import AuthorizationRepository, DecisionRepository
from pacca.integrations.vector_store import RetrievalOutcome
from pacca.models import ClassificationOutput, EvidenceOutput, UrgencyLevel
from pacca.models.authorization import AuthorizationDecision, AuthorizationRequest
from pacca.models.clinical import ClinicalCase, EvidenceItem
from pacca.models.enums import AuthorizationStatus, EvidenceSourceType, ReviewTier
from pacca.models.intent import IntentRecord

# ── Shared fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


def _healthy_outcome(text: str = "Mock guideline content") -> RetrievalOutcome:
    return RetrievalOutcome(text=text, mode="pipeline", degraded=False, reason=None)


def _degraded_outcome(text: str = "Mock guideline (degraded)") -> RetrievalOutcome:
    return RetrievalOutcome(text=text, mode="direct_fallback", degraded=True, reason="RuntimeError")


def _request(
    request_id: str,
    *,
    procedure_code: str = "J9271",
    diagnosis_code: str = "C34.1",
    evidence_text: str = "Stage IIIA NSCLC, PD-L1 TPS >= 50%",
) -> AuthorizationRequest:
    """A routine oncology case that does NOT trip any pre-flight check on its
    own, so tests exercising Tier-1/Tier-2 routing reach the Decision Agent."""
    patient_id = f"P-{request_id}"
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
                    description=evidence_text,
                    original_text=evidence_text,
                    confidence=0.95,
                )
            ],
        ),
    )


def _mock_agents(
    monkeypatch,
    *,
    tier1_confidence: float,
    tier1_status: AuthorizationStatus = AuthorizationStatus.AUTO_APPROVED,
    tier1_cited_evidence_ids: list[str] | None = None,
    tier2_confidence: float | None = None,
) -> None:
    """Patch the route's module-level orchestrator singleton's sub-agents so
    process_decision's OWN branching logic runs for real, with no network
    call. Mirrors test_escalation_tree.py's make_orchestrator_with_mocks."""
    monkeypatch.setattr(
        orchestrator.decision_agent,
        "run",
        AsyncMock(
            return_value=AuthorizationDecision(
                decision_id="DEC-TIER1",
                status=tier1_status,
                confidence_score=tier1_confidence,
                rationale="Tier-1 test rationale.",
                review_tier_used=ReviewTier.AUTOMATED,
                cited_evidence_ids=tier1_cited_evidence_ids or ["e1"],
            )
        ),
    )
    if tier2_confidence is not None:
        monkeypatch.setattr(
            orchestrator.medical_director_agent,
            "run",
            AsyncMock(
                return_value=AuthorizationDecision(
                    decision_id="DEC-TIER2",
                    status=AuthorizationStatus.AUTO_APPROVED,
                    confidence_score=tier2_confidence,
                    rationale="Tier-2 test rationale.",
                    review_tier_used=ReviewTier.HUMAN,
                )
            ),
        )
    monkeypatch.setattr(
        orchestrator.evidence_agent,
        "run",
        AsyncMock(
            return_value=EvidenceOutput(
                clinical_narrative="", key_findings=[], evidence_gaps=[], confidence_score=0.9
            )
        ),
    )
    monkeypatch.setattr(
        orchestrator.classification_agent,
        "run",
        AsyncMock(
            return_value=ClassificationOutput(
                complexity=1,
                complexity_factors=[],
                primary_specialty="general",
                urgency=UrgencyLevel.ROUTINE,
                routing_rationale="",
                confidence_score=0.9,
            )
        ),
    )


async def _queue(session) -> list[tuple]:
    return await AuthorizationRepository(session).list_escalated_for_review()


# =============================================================================
# Part 1 -- the ScopeViolation fix
# =============================================================================


@pytest.mark.asyncio
async def test_scope_violation_after_request_persisted_is_persisted_and_queued(
    session, monkeypatch
):
    """
    THE defect test: force a ScopeViolation at the rag.query guard (request
    row already committed to this session by that point, same as every real
    call site after db.write_request), and assert exactly one decision row
    lands in the table and surfaces via list_escalated_for_review().

    Denies specifically on the SECOND rag.query call (case_precedents) by
    narrowing the declared intent's allowed_collections to only
    nccn_guidelines -- the first collection check still passes (audited
    scope.allow), so this is not a db.write_request-time violation.
    """

    def _narrowed(*, correlation_id, request_id, subject_ref):
        return IntentRecord(
            correlation_id=correlation_id,
            request_id=request_id,
            subject_ref=subject_ref,
            allowed_collections=["nccn_guidelines"],  # drops case_precedents
        )

    monkeypatch.setattr(IntentRecord, "for_prior_auth", staticmethod(_narrowed))
    monkeypatch.setattr(rag_engine, "query", lambda *a, **k: _healthy_outcome())

    req = _request("AUTH-SV-1")
    result = await submit_authorization(request=req, session=session)
    await session.commit()

    assert result.status == AuthorizationStatus.IN_REVIEW
    assert result.review_tier_used == ReviewTier.HUMAN

    rows = await _queue(session)
    assert len(rows) == 1, f"expected exactly 1 escalated row, got {len(rows)}"
    decision, request_row = rows[0]
    assert request_row.request_id == "AUTH-SV-1"
    assert decision.outcome == AuthorizationStatus.IN_REVIEW.value

    # Never double-persisted: only the SCOPE-* decision id exists for this run.
    all_decisions_for_request = [d for d, r in rows if r.request_id == "AUTH-SV-1"]
    assert len(all_decisions_for_request) == 1
    assert decision.decision_id.startswith("SCOPE-AUTH-SV-1-")


@pytest.mark.asyncio
async def test_scope_violation_at_write_request_does_not_persist_or_crash(session, monkeypatch):
    """
    The one case the fix deliberately does NOT persist: a violation on
    db.write_request itself means the request row was never written, so
    there is nothing for a decision to join against. Writing the request
    anyway (just to make the decision persistable) would defeat the very
    guard that flagged it. This must still fail closed -- IN_REVIEW/HUMAN,
    no exception -- just with zero rows in the queue (documented limitation,
    not a silent bug).
    """

    def _leaky(*, correlation_id, request_id, subject_ref):
        return IntentRecord(
            correlation_id=correlation_id, request_id=request_id, subject_ref="OTHER-PATIENT"
        )

    monkeypatch.setattr(IntentRecord, "for_prior_auth", staticmethod(_leaky))
    monkeypatch.setattr(rag_engine, "query", lambda *a, **k: _healthy_outcome())

    req = _request("AUTH-SV-2")
    result = await submit_authorization(request=req, session=session)
    await session.commit()

    assert result.status == AuthorizationStatus.IN_REVIEW
    assert result.review_tier_used == ReviewTier.HUMAN
    assert await _queue(session) == []
    assert await AuthorizationRepository(session).get_by_id("AUTH-SV-2") is None


# =============================================================================
# Part 2 -- audit every other escalation exit, by execution
# =============================================================================


@pytest.mark.asyncio
async def test_preflight_escalation_persists_and_queues(session, monkeypatch):
    """Branches 4-7 (here: experimental treatment, Q2041 CAR-T): the
    Orchestrator returns via _handle_pre_flight_escalation before any agent
    call, and the ROUTE's normal fall-through persist (after
    orchestrator.process_decision returns) picks it up."""
    monkeypatch.setattr(rag_engine, "query", lambda *a, **k: _healthy_outcome())

    req = _request("AUTH-PF-1", procedure_code="Q2041")
    result = await submit_authorization(request=req, session=session)
    await session.commit()

    assert result.status == AuthorizationStatus.IN_REVIEW
    assert result.review_tier_used == ReviewTier.HUMAN
    rows = await _queue(session)
    assert [r.request_id for _, r in rows] == ["AUTH-PF-1"]


@pytest.mark.asyncio
async def test_evidence_grounding_short_circuit_persists_and_queues(session, monkeypatch):
    """P-5: the Decision Agent cites an evidence id absent from the
    submission -- the orchestrator forces IN_REVIEW and returns via the
    normal `return decision` inside process_decision (not an early return
    from the route), so the route's fall-through persist covers it too."""
    monkeypatch.setattr(rag_engine, "query", lambda *a, **k: _healthy_outcome())
    _mock_agents(
        monkeypatch,
        tier1_confidence=0.97,
        tier1_status=AuthorizationStatus.AUTO_APPROVED,
        tier1_cited_evidence_ids=["NOT-IN-SUBMISSION"],
    )

    req = _request("AUTH-EG-1")
    result = await submit_authorization(request=req, session=session)
    await session.commit()

    assert result.status == AuthorizationStatus.IN_REVIEW
    rows = await _queue(session)
    assert [r.request_id for _, r in rows] == ["AUTH-EG-1"]


@pytest.mark.asyncio
async def test_confidence_branch3_low_confidence_persists_and_queues(session, monkeypatch):
    """Branch 3: Tier-1 confidence below the escalation threshold (0.90
    default) routes to human review without ever calling the Medical
    Director Agent."""
    monkeypatch.setattr(rag_engine, "query", lambda *a, **k: _healthy_outcome())
    _mock_agents(monkeypatch, tier1_confidence=0.5, tier1_status=AuthorizationStatus.AUTO_APPROVED)

    req = _request("AUTH-B3-1")
    result = await submit_authorization(request=req, session=session)
    await session.commit()

    assert result.status == AuthorizationStatus.IN_REVIEW
    rows = await _queue(session)
    assert [r.request_id for _, r in rows] == ["AUTH-B3-1"]


@pytest.mark.asyncio
async def test_kill_switch_persists_and_queues(session, monkeypatch):
    """enable_autonomous_decisions=False forces IN_REVIEW at Tier-1 routing
    regardless of a high Tier-1 confidence."""
    monkeypatch.setattr(rag_engine, "query", lambda *a, **k: _healthy_outcome())
    _mock_agents(monkeypatch, tier1_confidence=0.99, tier1_status=AuthorizationStatus.AUTO_APPROVED)

    apply_overrides({"enable_autonomous_decisions": False})
    try:
        req = _request("AUTH-KS-1")
        result = await submit_authorization(request=req, session=session)
        await session.commit()
    finally:
        clear_all_overrides()

    assert result.status == AuthorizationStatus.IN_REVIEW
    rows = await _queue(session)
    assert [r.request_id for _, r in rows] == ["AUTH-KS-1"]


@pytest.mark.asyncio
async def test_medical_director_in_review_outcome_persists_and_queues(session, monkeypatch):
    """Branch 2: Tier-1 confidence lands in the Medical Director band
    (0.90-0.95); the Tier-2 agent's own confidence is too low to
    auto-approve, so the Orchestrator's own logic sets IN_REVIEW."""
    monkeypatch.setattr(rag_engine, "query", lambda *a, **k: _healthy_outcome())
    _mock_agents(
        monkeypatch,
        tier1_confidence=0.92,
        tier1_status=AuthorizationStatus.AUTO_APPROVED,
        tier2_confidence=0.80,
    )

    req = _request("AUTH-MD-1")
    result = await submit_authorization(request=req, session=session)
    await session.commit()

    assert result.status == AuthorizationStatus.IN_REVIEW
    rows = await _queue(session)
    assert [r.request_id for _, r in rows] == ["AUTH-MD-1"]


@pytest.mark.asyncio
async def test_medical_director_auto_approved_outcome_still_persists(session, monkeypatch):
    """Branch 2's OTHER outcome: Tier-2 confidence clears the auto-approve
    threshold. This decision does NOT belong in the review queue (it is not
    IN_REVIEW), but it still must be a persisted decision row -- "persists?"
    and "visible in /review-queue?" are different questions for this path."""
    monkeypatch.setattr(rag_engine, "query", lambda *a, **k: _healthy_outcome())
    _mock_agents(
        monkeypatch,
        tier1_confidence=0.92,
        tier1_status=AuthorizationStatus.AUTO_APPROVED,
        tier2_confidence=0.99,
    )

    req = _request("AUTH-MD-2")
    result = await submit_authorization(request=req, session=session)
    await session.commit()

    assert result.status == AuthorizationStatus.AUTO_APPROVED
    assert await _queue(session) == []  # correctly absent -- not an escalation
    persisted = await DecisionRepository(session).get_by_request_id("AUTH-MD-2")
    assert persisted is not None
    assert persisted.outcome == AuthorizationStatus.AUTO_APPROVED.value


@pytest.mark.asyncio
async def test_rag_degraded_escalation_still_persists_and_queues(session, monkeypatch):
    """Confirms the chg-20 fix (already shipped) keeps working against a real
    DB: this path has its own inline enforce_scope + DecisionRepository.create
    before its early return from the route."""
    monkeypatch.setattr(rag_engine, "query", lambda *a, **k: _degraded_outcome())
    apply_overrides({"rag_degraded_escalates": True})
    try:
        req = _request("AUTH-RD-1")
        result = await submit_authorization(request=req, session=session)
        await session.commit()
    finally:
        clear_all_overrides()

    assert result.status == AuthorizationStatus.IN_REVIEW
    rows = await _queue(session)
    assert [r.request_id for _, r in rows] == ["AUTH-RD-1"]
