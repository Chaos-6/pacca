"""
chg-24 (D4): the transaction-scope properties the fix exists to prove.

`submit_authorization` used to hold a DB write transaction open across the
Claude API calls (~23s measured — see the D4 design spec, harness note).
This file proves the two properties spec §4 items 2-3 name directly and
deterministically (no timing/sleep races):

  1. The T1 write is committed and visible to an INDEPENDENT connection
     BEFORE the orchestrator (the LLM call) is even invoked.
  2. No transaction is open on the route's own session at the moment the
     orchestrator is invoked.

Both are checked from INSIDE a mocked `orchestrator.process_decision` — the
exact point spec §4 items 2-3 name — so there is no race to get right; the
assertion runs precisely when the property must hold, not "soon after."

Also covers spec §3.3: the five `enforce_scope` call sites survive chg-24's
extraction of three helper functions out of `submit_authorization` (done to
stay within ruff's statement/branch budget — see the chg-24 commit). The
count is asserted across the whole module source rather than just
`submit_authorization`'s own text, since three of the five sites now live in
`_handle_rag_degraded_escalation` / `_persist_scope_violation_escalation`
rather than inline in the route function itself.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from pacca.api.routes import authorizations as authz_module
from pacca.api.routes.authorizations import orchestrator, rag_engine, submit_authorization
from pacca.db.models import AuthorizationRequestModel, Base
from pacca.db.repository import AuthorizationRepository
from pacca.integrations.vector_store import RetrievalOutcome
from pacca.models.authorization import AuthorizationDecision, AuthorizationRequest
from pacca.models.clinical import ClinicalCase, EvidenceItem
from pacca.models.enums import AuthorizationStatus, EvidenceSourceType, ReviewTier

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


def _healthy_outcome(text: str = "Mock guideline content") -> RetrievalOutcome:
    return RetrievalOutcome(text=text, mode="pipeline", degraded=False, reason=None)


def _request(request_id: str) -> AuthorizationRequest:
    patient_id = f"P-{request_id}"
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
async def engine(tmp_path) -> AsyncGenerator:
    db_path = tmp_path / "txn_boundaries.db"
    eng = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.mark.asyncio
async def test_t1_committed_and_visible_to_independent_connection_before_orchestrator(
    engine, monkeypatch
):
    """Spec §4 item 2: the request row must be committed and visible to an
    INDEPENDENT connection before `process_decision` is called (proves T1
    closed). Checked from a SEPARATE session/connection than the one the
    route itself uses, at the exact moment the orchestrator is invoked."""
    maker = async_sessionmaker(engine, expire_on_commit=False)
    route_session = maker()
    reader_session = maker()  # a genuinely separate connection

    captured = {}

    async def mock_process_decision(decision_ctx, *, audit, correlation_id):
        result = await reader_session.execute(
            select(AuthorizationRequestModel).where(
                AuthorizationRequestModel.request_id == "AUTH-TXN-1"
            )
        )
        captured["row"] = result.scalar_one_or_none()
        return AuthorizationDecision(
            status=AuthorizationStatus.AUTO_APPROVED,
            confidence_score=0.97,
            rationale="test",
            review_tier_used=ReviewTier.AUTOMATED,
            cited_evidence_ids=["e1"],
        )

    monkeypatch.setattr(orchestrator, "process_decision", mock_process_decision)
    monkeypatch.setattr(rag_engine, "query", lambda *a, **k: _healthy_outcome())

    req = _request("AUTH-TXN-1")
    await submit_authorization(request=req, session=route_session)

    assert captured["row"] is not None, (
        "the T1 request row was not visible to an independent connection "
        "at the moment the orchestrator was invoked -- T1 is not actually closed"
    )
    assert captured["row"].request_id == "AUTH-TXN-1"

    await route_session.close()
    await reader_session.close()


@pytest.mark.asyncio
async def test_no_transaction_open_on_route_session_during_orchestrator_call(engine, monkeypatch):
    """Spec §4 item 3: no transaction is open on the ROUTE's OWN session at
    the moment the orchestrator is invoked. `AsyncSession.in_transaction()`
    is a direct, no-I/O check of session state -- no timing involved."""
    maker = async_sessionmaker(engine, expire_on_commit=False)
    route_session = maker()

    captured = {}

    async def mock_process_decision(decision_ctx, *, audit, correlation_id):
        captured["in_transaction"] = route_session.in_transaction()
        return AuthorizationDecision(
            status=AuthorizationStatus.AUTO_APPROVED,
            confidence_score=0.97,
            rationale="test",
            review_tier_used=ReviewTier.AUTOMATED,
            cited_evidence_ids=["e1"],
        )

    monkeypatch.setattr(orchestrator, "process_decision", mock_process_decision)
    monkeypatch.setattr(rag_engine, "query", lambda *a, **k: _healthy_outcome())

    req = _request("AUTH-TXN-2")
    await submit_authorization(request=req, session=route_session)

    assert captured["in_transaction"] is False, (
        "a transaction was still open on the route's own session while the "
        "orchestrator (LLM call) was running -- T1 did not actually close "
        "before the network call"
    )

    await route_session.close()


@pytest.mark.asyncio
async def test_enforce_scope_still_wraps_every_write(engine):
    """Spec §3.3: five `enforce_scope` call sites existed before chg-24 (four
    DB writes + the RAG query) and must survive the restructuring. chg-24
    extracted three helper functions out of `submit_authorization` to stay
    within ruff's statement/branch budget, so two of the five sites
    (db.write_decision in the RAG-degraded and ScopeViolation escalation
    exits) now live in `_handle_rag_degraded_escalation` /
    `_persist_scope_violation_escalation` rather than inline in the route.
    Counting across all four functions' source (not just the route's own
    text) is the faithful update of the original invariant -- the guarded
    envelope is unchanged, only which function's text contains the call.
    """
    sources = "".join(
        inspect.getsource(fn)
        for fn in (
            authz_module.submit_authorization,
            authz_module._resolve_duplicate_request_id,
            authz_module._handle_rag_degraded_escalation,
            authz_module._persist_scope_violation_escalation,
        )
    )
    count = sources.count("await enforce_scope(")
    assert count == 5, (
        f"expected exactly 5 enforce_scope call sites across the submit path "
        f"(4 DB writes + the RAG query), found {count}"
    )


@pytest.mark.asyncio
async def test_partial_state_after_t1_only_is_queryable_and_honest(engine):
    """Spec §3.2: a crash between T1 and T2 leaves a request row with no
    decision -- the correct, legible state. There is no
    `GET /authorizations/{request_id}` route in this codebase (verified by
    grepping src/pacca/api/routes/*.py: only POST / , POST /feedback, and
    GET /review-queue exist under this router) -- the D4 spec's assumption
    of such a route does not match the code. This test instead proves the
    state is honestly queryable through the repository method every real
    caller (including a future status route) would use.
    """
    maker = async_sessionmaker(engine, expire_on_commit=False)
    session = maker()

    async def _crash(*a, **k):
        raise RuntimeError("simulated crash before T2")

    from unittest.mock import patch

    with (
        patch(
            "pacca.api.routes.authorizations.orchestrator.process_decision",
            new_callable=AsyncMock,
            side_effect=_crash,
        ),
        patch(
            "pacca.api.routes.authorizations.rag_engine.query",
            return_value=_healthy_outcome(),
        ),
    ):
        req = _request("AUTH-TXN-CRASH")
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            await submit_authorization(request=req, session=session)

    # T1 already committed before the simulated crash -- the request row
    # must survive even though the whole route call raised.
    row = await AuthorizationRepository(session).get_by_id("AUTH-TXN-CRASH")
    assert row is not None, "the T1-committed request row did not survive the crash"
    assert row.status == "submitted", (
        f"a request with no decision yet should still read status='submitted' -- got {row.status!r}"
    )

    from pacca.db.repository import DecisionRepository

    decision = await DecisionRepository(session).get_by_request_id("AUTH-TXN-CRASH")
    assert decision is None, "no decision should exist -- T2 never ran"

    await session.close()
