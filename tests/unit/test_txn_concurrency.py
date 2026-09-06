"""
chg-24 (D4) headline test: two concurrent SQLite submissions both complete.

Before this fix, `submit_authorization` held a DB write transaction open
across the Claude API call. On SQLite that means the file-level write lock
is held for the whole request: a second concurrent writer cannot proceed
while the first is still "thinking" (waiting on the LLM). The fix closes T1
before any network call, so the write lock for the FIRST request's insert is
held only for the commit itself -- microseconds -- freeing a second writer
to proceed while the first is still in its (now transaction-free) LLM-call
window.

Determinism, not a sleep race: rather than hoping two sleeps overlap, this
test uses two `asyncio.Event`s to force an exact interleaving --

  1. Request A runs until its mocked `orchestrator.process_decision` is
     invoked, then PAUSES there (this is the moment its T1 transaction is
     either closed (fixed code) or still open (pre-fix code)).
  2. Only once A has reached that pause point does request B start its OWN
     `submit_authorization` call -- so B's T1 write attempt happens
     precisely while A's T1 state is whatever the code under test leaves it
     in.
  3. B finishes completely (or raises), then releases A to finish.

Against the pre-fix code (`git show f3eb7a3:...` restored inline, per
P-010), A's transaction is still open when B attempts its T1 insert on a
SEPARATE real connection to the SAME SQLite file (`connect_args={"timeout":
0}` removes any busy-wait, so contention fails immediately rather than
serializing) -- B's insert raises `OperationalError: database is locked`
inside `submit_authorization`, which the route's own `except Exception`
handler turns into an unhandled `HTTPException(500)`.

Against the fixed code, A's T1 has already committed before it ever calls
the orchestrator, so B's T1 write finds no contention and B completes in
full -- including its OWN full round trip through T2 -- while A is still
paused mid-orchestrator-call. Both persist; zero `audit_write_failed`.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from pacca.api.routes.authorizations import orchestrator, rag_engine, submit_authorization
from pacca.db import repository as repository_module
from pacca.db.models import Base
from pacca.db.repository import AuthorizationRepository, DecisionRepository
from pacca.integrations.vector_store import RetrievalOutcome
from pacca.models.authorization import AuthorizationDecision, AuthorizationRequest
from pacca.models.clinical import ClinicalCase, EvidenceItem
from pacca.models.enums import AuthorizationStatus, EvidenceSourceType, ReviewTier

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

PATIENT_A = "P-CONC-A"
PATIENT_B = "P-CONC-B"


def _healthy_outcome() -> RetrievalOutcome:
    return RetrievalOutcome(
        text="Mock guideline content", mode="pipeline", degraded=False, reason=None
    )


def _request(request_id: str, patient_id: str) -> AuthorizationRequest:
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


@pytest.fixture
async def db_url(tmp_path) -> AsyncGenerator[str, None]:
    """A real FILE-based SQLite database (not :memory:), shared across two
    genuinely separate connections -- required to reproduce SQLite's real
    file-level write lock. Default pooling (not StaticPool) so each session
    checks out its own connection, exactly like two concurrent FastAPI
    requests would."""
    db_path = tmp_path / "concurrency.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    eng = create_async_engine(url, connect_args={"timeout": 0})
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await eng.dispose()
    yield url


async def _run_concurrent_pair(db_url: str, monkeypatch) -> dict:
    """Drives the A/B interleaving described in the module docstring against
    whatever `submit_authorization` currently is (fixed or reverted-for-RED).
    Returns a dict of measured outcomes for the caller to assert on."""
    engine = create_async_engine(db_url, connect_args={"timeout": 0})
    maker = async_sessionmaker(engine, expire_on_commit=False)

    a_reached_llm = asyncio.Event()
    b_may_release_a = asyncio.Event()

    async def mock_process_decision(decision_ctx, *, audit, correlation_id, **_kwargs):
        # **_kwargs so a new orchestrator argument does not break stubs that
        # only care that the call happened (chg-32 added prior_denial_codes).
        if decision_ctx.case.patient_id == PATIENT_A:
            a_reached_llm.set()
            await b_may_release_a.wait()
        return AuthorizationDecision(
            status=AuthorizationStatus.AUTO_APPROVED,
            confidence_score=0.97,
            rationale="test",
            review_tier_used=ReviewTier.AUTOMATED,
            model_id="claude-sonnet-4-5-20250929",
            prompt_version="v2.7",
            cited_evidence_ids=["e1"],
        )

    audit_write_failed_calls = []
    original_error = repository_module.logger.error

    def _capture_logger_error(event, *args, **kwargs):
        if event == "audit_write_failed":
            audit_write_failed_calls.append(kwargs)
        return original_error(event, *args, **kwargs)

    monkeypatch.setattr(orchestrator, "process_decision", mock_process_decision)
    monkeypatch.setattr(rag_engine, "query", lambda *a, **k: _healthy_outcome())
    monkeypatch.setattr(repository_module.logger, "error", _capture_logger_error)

    async def run_a():
        session_a = maker()
        try:
            return await submit_authorization(
                request=_request("AUTH-CONC-A", PATIENT_A), session=session_a
            )
        finally:
            await session_a.close()

    async def run_b():
        await a_reached_llm.wait()
        session_b = maker()
        try:
            result = await submit_authorization(
                request=_request("AUTH-CONC-B", PATIENT_B), session=session_b
            )
            return result
        finally:
            b_may_release_a.set()
            await session_b.close()

    results = await asyncio.gather(run_a(), run_b(), return_exceptions=True)

    verify_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with verify_maker() as verify_session:
        request_count = 0
        decision_count = 0
        for rid in ("AUTH-CONC-A", "AUTH-CONC-B"):
            if await AuthorizationRepository(verify_session).get_by_id(rid) is not None:
                request_count += 1
            if await DecisionRepository(verify_session).get_by_request_id(rid) is not None:
                decision_count += 1

    await engine.dispose()

    return {
        "results": results,
        "request_count": request_count,
        "decision_count": decision_count,
        "audit_write_failed_count": len(audit_write_failed_calls),
    }


@pytest.mark.asyncio
async def test_two_concurrent_sqlite_submissions_both_complete(db_url, monkeypatch):
    """THE HEADLINE TEST (spec §4 item 1). Must pass against the fixed code
    on this branch. The P-010 fail-then-pass proof (RED against f3eb7a3,
    restored and confirmed GREEN) was an inline revert
    (`git show f3eb7a3:... > authorizations.py`, backed up first, restored
    after -- never `git stash`), not a second committed test -- see the
    executor's final report for the pasted RED/GREEN output."""
    outcome = await _run_concurrent_pair(db_url, monkeypatch)

    exceptions = [r for r in outcome["results"] if isinstance(r, BaseException)]
    assert exceptions == [], (
        f"expected both submissions to complete without raising, got {exceptions}"
    )

    completions = [r for r in outcome["results"] if isinstance(r, AuthorizationDecision)]
    assert len(completions) == 2, f"expected 2 completed decisions, got {len(completions)}"

    assert outcome["request_count"] == 2, (
        f"expected both request rows persisted, got {outcome['request_count']}"
    )
    assert outcome["decision_count"] == 2, (
        f"expected both decision rows persisted, got {outcome['decision_count']}"
    )
    assert outcome["audit_write_failed_count"] == 0, (
        f"expected zero audit_write_failed events, got {outcome['audit_write_failed_count']}"
    )
