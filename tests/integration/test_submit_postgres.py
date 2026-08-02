"""
Real-Postgres integration test for the submit path (B2/B3/chg-23 masking guard).

The entire unit suite runs on SQLite, which cannot catch three whole classes of
production bug:

  * B2 — `postgresql.JSONB` columns that don't compile under SQLite.
  * B3 (superseded, see below) — foreign keys, which SQLite does not enforce.
  * chg-23 — commit-vs-flush durability: a single-process, single-connection
    SQLite test cannot distinguish "flushed on the caller's own transaction"
    from "committed on a genuinely independent connection" the way a second
    real Postgres connection can.

History: B3 was invisible to 712 green tests because the submit route writes
two `request_id`-bearing audit rows (`intent.declared`, `authorization_submitted`)
before the parent `authorization_requests` row exists, and a non-deferrable FK
rejects that first flush on Postgres. The original fix made the FK DEFERRABLE
INITIALLY DEFERRED (migration 003) — verified here.

chg-23 changed `AuditRepository.log()` to commit each audit row on an
INDEPENDENT session, specifically so a business-transaction rollback can no
longer erase an already-logged audit row (the actual defect this file's tests
now also cover). That broke the migration-003 deferred FK: the deferred check
now runs at the audit write's OWN immediate commit, before the parent row has
committed, on every request — measured live against this container as 0 of 7
audit rows persisted with the FK still in place. Migration 007 drops that FK
entirely (see its docstring for the full reasoning); this file's
`test_audit_request_id_has_no_fk_but_keeps_its_index` replaces the old
`test_audit_fk_is_deferrable_in_the_live_catalog`, which asserted the removed
constraint.

This test drives the actual submit handler against a migration-built Postgres
(schema from `alembic upgrade head`, so it is production-shaped and
migration-tracked, not `create_all`). The LLM and RAG are patched — this test is
about persistence and referential integrity, not clinical accuracy (the clinical
gate covers that) — but the audit writes, the parent/decision persistence, and
the schema all hit real Postgres.

Gated on `POSTGRES_TEST_URL`: it skips when unset (local SQLite dev), and the CI
`test-postgres` job / `make test-postgres` provide a Postgres 16 and set it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pacca.integrations.vector_store import RetrievalOutcome

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

pytestmark = pytest.mark.postgres

POSTGRES_TEST_URL = os.environ.get("POSTGRES_TEST_URL")

_SKIP = pytest.mark.skipif(
    not POSTGRES_TEST_URL,
    reason="POSTGRES_TEST_URL not set — real-Postgres test (see make test-postgres)",
)

# Tables written by the submit path, children before parents for TRUNCATE.
_TABLES = ["audit_logs", "authorization_decisions", "human_reviews", "authorization_requests"]


@pytest.fixture(scope="module")
def _migrated_url() -> str:
    """Apply `alembic upgrade head` to the test Postgres once, then hand back the URL.

    Sync on purpose: it only shells out to alembic, and a module-scoped async
    fixture would collide with the function-scoped asyncio loop.
    """
    assert POSTGRES_TEST_URL is not None
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True,
        env={**os.environ, "DATABASE_URL": POSTGRES_TEST_URL},
        capture_output=True,
    )
    return POSTGRES_TEST_URL


@pytest_asyncio.fixture
async def pg_session(_migrated_url: str) -> AsyncGenerator[AsyncSession, None]:
    """A clean real-Postgres session; tables truncated around each test."""
    engine = create_async_engine(_migrated_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {', '.join(_TABLES)} RESTART IDENTITY CASCADE"))
    async with factory() as session:
        yield session
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {', '.join(_TABLES)} RESTART IDENTITY CASCADE"))
    await engine.dispose()


def _sample_request() -> dict:
    return {
        "request_id": "AUTH-PG-001",
        "patient_id": "P-PG-001",
        "provider_npi": "1234567890",
        "clinical_case": {
            "patient_id": "P-PG-001",
            "primary_diagnosis_code": "C34.1",
            "procedure_code": "J9271",
            "evidence": [
                {
                    "id": "e1",
                    "source_type": "CLINICAL_NOTE",
                    "description": "Stage IIIA NSCLC",
                    "original_text": "Patient presents with stage IIIA NSCLC.",
                    "confidence": 0.95,
                }
            ],
        },
    }


@_SKIP
@pytest.mark.asyncio
async def test_submit_commits_with_zero_orphaned_audit_rows(pg_session: AsyncSession) -> None:
    """
    The B3 / chg-23 guard. Driving the real submit handler against real
    Postgres, the whole transaction must COMMIT and every audit row the
    happy path writes must actually persist, correctly linked by
    request_id.

    This test is the live proof of the chg-23 durability fix AND of the
    migration 007 FK removal it required: with the pre-chg-23
    `AuditRepository.log()` (flush-on-caller's-session) and the migration
    003 deferrable FK both still in place, this reproduced as 0 of 7 audit
    rows persisting per request — the deferred FK check, now running on an
    independently-committing session, rejected every one (see migration
    007's docstring and the chg-23 harness note for the full mechanism).
    SQLite cannot catch either half of this: it doesn't enforce FKs, and a
    single-process `:memory:` test can't distinguish "flushed" from
    "committed" the way a real, independent connection can.
    """
    from unittest.mock import AsyncMock, patch

    from pacca.api.routes.authorizations import submit_authorization
    from pacca.models.authorization import AuthorizationDecision, AuthorizationRequest
    from pacca.models.enums import AuthorizationStatus, ReviewTier

    decision = AuthorizationDecision(
        status=AuthorizationStatus.AUTO_APPROVED,
        confidence_score=0.98,
        rationale="synthetic — persistence test, not clinical",
        review_tier_used=ReviewTier.AUTOMATED,
        cited_evidence_ids=["e1"],
    )
    req = AuthorizationRequest(**_sample_request())

    with (
        patch(
            "pacca.api.routes.authorizations.orchestrator.process_decision",
            new_callable=AsyncMock,
            return_value=decision,
        ),
        patch(
            "pacca.api.routes.authorizations.rag_engine.query",
            return_value=RetrievalOutcome(
                text="Mock guideline content", mode="pipeline", degraded=False, reason=None
            ),
        ),
    ):
        # get_session commits on exit; we call the handler directly, so we own
        # the commit — and the deferred FK is checked exactly there.
        await submit_authorization(request=req, session=pg_session)
        await pg_session.commit()

    request_rows = (
        await pg_session.execute(text("SELECT count(*) FROM authorization_requests"))
    ).scalar_one()
    decision_rows = (
        await pg_session.execute(text("SELECT count(*) FROM authorization_decisions"))
    ).scalar_one()
    audit_rows = (await pg_session.execute(text("SELECT count(*) FROM audit_logs"))).scalar_one()
    audit_rows_linked = (
        await pg_session.execute(
            text("SELECT count(*) FROM audit_logs WHERE request_id = :rid"),
            {"rid": req.request_id},
        )
    ).scalar_one()
    orphans = (
        await pg_session.execute(
            text(
                "SELECT count(*) FROM audit_logs a "
                "LEFT JOIN authorization_requests r ON a.request_id = r.request_id "
                "WHERE a.request_id IS NOT NULL AND r.request_id IS NULL"
            )
        )
    ).scalar_one()

    assert request_rows == 1, "the parent authorization_requests row was not persisted"
    assert decision_rows == 1, "the decision row was not persisted"
    # The happy path writes exactly 8 audit.log() calls: intent.declared,
    # authorization_submitted, scope.allow (db.read_prior_denials), scope.allow
    # (db.write_request), scope.allow x2 (rag.query x nccn_guidelines /
    # case_precedents), authorization_decision_made, scope.allow
    # (db.write_decision) — all 8 must persist, not just "some".
    #
    # 7 -> 8 at chg-32, which added the prior-denial read. The count rises with
    # a named guarded call and must never fall without one: this assertion's job
    # is to prove every audit row SURVIVES (the chg-23 durability property), so
    # a silent drop is the failure it exists to catch.
    assert audit_rows == 8, f"expected exactly 8 audit rows on the happy path, got {audit_rows}"
    # Also 8: enforce_scope takes request_id from the INTENT, not from the
    # guarded call's kwargs, so every scope.allow row is linkable regardless of
    # which identifiers that particular call passes.
    assert audit_rows_linked == 8, (
        f"expected all 8 audit rows to carry request_id={req.request_id!r} intact, "
        f"only {audit_rows_linked} did"
    )
    assert orphans == 0, "an audit row references a non-existent request"


@_SKIP
@pytest.mark.asyncio
async def test_audit_row_survives_business_rollback_on_real_postgres(_migrated_url: str) -> None:
    """The chg-23 HEADLINE durability guarantee, proven on REAL Postgres.

    `tests/unit/test_audit_durability.py` proves the same property against
    SQLite (fast, run on every commit), but SQLite cannot fully stand in
    for this: it's a single-process, single-connection-pool test that
    cannot distinguish "flushed on the caller's own transaction" from
    "committed on a genuinely independent connection" the way a second
    real Postgres connection, with its own MVCC snapshot, can. This test
    uses two separate sessions against real Postgres: one that writes an
    audit row and then simulates a business failure + rollback, and a
    second, independent one that reads the result back.
    """
    from pacca.db.repository import AuditRepository

    engine = create_async_engine(_migrated_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    correlation_id = "corr-pg-rollback-headline"

    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {', '.join(_TABLES)} RESTART IDENTITY CASCADE"))

    try:
        async with maker() as business_session:
            repo = AuditRepository(business_session)
            await repo.log(
                action="test.postgres.rollback",
                actor="x",
                actor_type="system",
                correlation_id=correlation_id,
            )
            try:
                raise RuntimeError("simulated business failure")
            except RuntimeError:
                await business_session.rollback()

        async with maker() as reader_session:
            rows = (
                await reader_session.execute(
                    text("SELECT action FROM audit_logs WHERE correlation_id = :cid"),
                    {"cid": correlation_id},
                )
            ).all()
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f"TRUNCATE {', '.join(_TABLES)} RESTART IDENTITY CASCADE"))
        await engine.dispose()

    assert len(rows) == 1, (
        f"expected the audit row to survive the Postgres business rollback, found {len(rows)}"
    )
    assert rows[0].action == "test.postgres.rollback"


@_SKIP
@pytest.mark.asyncio
async def test_audit_request_id_has_no_fk_but_keeps_its_index(_migrated_url: str) -> None:
    """chg-23 (migration 007) DROPS the deferrable FK the previous version of
    this test asserted — a contract change, not a weakening.

    Before (migration 003, B3): `audit_logs.request_id` carried a
    DEFERRABLE INITIALLY DEFERRED FK to `authorization_requests`, checked
    at commit. That worked only because the audit write and the parent
    row shared one transaction/commit. Once `AuditRepository.log()`
    commits independently of the caller's transaction (chg-23's
    durability fix — see `db/repository.py` and migration 007's
    docstring), the deferred check runs at the audit write's OWN,
    immediate commit, before the parent row exists — and rejects every
    request, not just failed ones (reproduced against this same
    container: 0 of 7 audit rows persisted per request with the FK still
    in place; see `test_submit_commits_with_zero_orphaned_audit_rows`
    above, which is the live proof the FK is gone and rows now persist).

    After: no FK constraint at all on `audit_logs.request_id`. The column
    and its index (`ix_audit_logs_request_id`) are unchanged — request_id
    remains a plain, queryable, indexed value; audit-row survival no
    longer depends on referential integrity to a business table it must
    be able to outlive.
    """
    engine = create_async_engine(_migrated_url)
    async with engine.connect() as conn:
        fk_row = (
            await conn.execute(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = 'audit_logs'::regclass AND contype = 'f'"
                )
            )
        ).first()
        index_row = (
            await conn.execute(
                text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE tablename = 'audit_logs' AND indexname = 'ix_audit_logs_request_id'"
                )
            )
        ).first()
    await engine.dispose()
    assert fk_row is None, (
        f"audit_logs still has a foreign key ({fk_row}); migration 007 should have dropped it"
    )
    assert index_row is not None, "ix_audit_logs_request_id is missing — the index must survive"


# =============================================================================
# chg-24 FIX 1 (Validator's D4 review, HIGH) -- the in-flight-duplicate race,
# on REAL Postgres. The Validator's specific worry: Postgres aborts the
# WHOLE transaction on an IntegrityError (`current transaction is aborted,
# commands ignored until end of transaction block`), so the resolver's own
# SELECTs would fail unless the route's `session.rollback()` (authorizations.
# py, right after catching IntegrityError) genuinely clears that state before
# the resolver runs. `tests/unit/test_duplicate_request_id_race.py` proves
# the same two properties on SQLite; these are the Postgres counterparts.
# =============================================================================

_RACE_TABLES = ["audit_logs", "authorization_decisions", "authorization_requests"]


async def _assert_race_outcome_and_third_duplicate_replays(
    maker, request_id: str, patient_id: str, expected_decision_id: str
) -> None:
    """Shared tail for the in-flight-race test: exactly one decision row,
    the in-flight-conflict audit action present, and a THIRD (later)
    duplicate submission still never 500s -- idempotent replay of the same
    decision, no new orchestrator call. Extracted out of the race test
    itself to stay within ruff's statement budget."""
    from unittest.mock import AsyncMock, patch

    from pacca.api.routes.authorizations import submit_authorization
    from pacca.db.repository import AuditRepository, DecisionRepository
    from pacca.models.authorization import AuthorizationDecision, AuthorizationRequest
    from pacca.models.enums import AuthorizationStatus, ReviewTier

    async with maker() as verify:
        decisions = await DecisionRepository(verify).list_by_request_id(request_id)
        assert len(decisions) == 1, (
            f"expected exactly 1 decision row after the Postgres race, got {len(decisions)}"
        )
        audit_rows = await AuditRepository(verify).get_by_request_id(request_id)
        assert any(r.action == "submission.in_flight_conflict" for r in audit_rows)

    async with maker() as session_c:
        third_pd = AsyncMock(
            return_value=AuthorizationDecision(
                status=AuthorizationStatus.AUTO_APPROVED,
                confidence_score=0.97,
                rationale="test",
                review_tier_used=ReviewTier.AUTOMATED,
                cited_evidence_ids=["e1"],
            )
        )
        with patch("pacca.api.routes.authorizations.orchestrator.process_decision", third_pd):
            result_c = await submit_authorization(
                request=AuthorizationRequest(**_race_request(request_id, patient_id)),
                session=session_c,
            )
    assert isinstance(result_c, AuthorizationDecision)
    assert result_c.decision_id == expected_decision_id
    assert third_pd.call_count == 0


def _race_request(request_id: str, patient_id: str) -> dict:
    return {
        "request_id": request_id,
        "patient_id": patient_id,
        "provider_npi": "1234567890",
        "clinical_case": {
            "patient_id": patient_id,
            "primary_diagnosis_code": "C34.1",
            "procedure_code": "J9271",
            "evidence": [
                {
                    "id": "e1",
                    "source_type": "CLINICAL_NOTE",
                    "description": "Stage IIIA NSCLC",
                    "original_text": "Patient presents with stage IIIA NSCLC.",
                    "confidence": 0.95,
                }
            ],
        },
    }


@_SKIP
@pytest.mark.asyncio
async def test_concurrent_duplicate_while_first_in_flight_gets_409_on_postgres(
    _migrated_url: str,
) -> None:
    """The race itself, on real Postgres: submit A, and once A is confirmedly
    inside its own orchestrator call (T1 committed, no decision yet), submit
    the SAME request_id concurrently. B must get 409, not a second decision
    row, and not the `current transaction is aborted` PG-specific failure
    the Validator flagged as the thing most likely to break here.
    """
    import asyncio
    from unittest.mock import patch

    from pacca.integrations.vector_store import RetrievalOutcome
    from pacca.models.authorization import AuthorizationDecision, AuthorizationRequest
    from pacca.models.enums import AuthorizationStatus, ReviewTier

    request_id = "AUTH-PG-RACE-1"
    patient_id = "P-PG-RACE-1"

    engine = create_async_engine(_migrated_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {', '.join(_RACE_TABLES)} RESTART IDENTITY CASCADE"))

    a_reached_llm = asyncio.Event()
    b_may_release_a = asyncio.Event()
    orchestrator_call_count = 0

    def _decision() -> AuthorizationDecision:
        return AuthorizationDecision(
            status=AuthorizationStatus.AUTO_APPROVED,
            confidence_score=0.97,
            rationale="test",
            review_tier_used=ReviewTier.AUTOMATED,
            cited_evidence_ids=["e1"],
        )

    async def mock_process_decision(decision_ctx, *, audit, correlation_id, **_kwargs):
        # **_kwargs so a new orchestrator argument does not break stubs that
        # only care that the call happened (chg-32 added prior_denial_codes).
        nonlocal orchestrator_call_count
        orchestrator_call_count += 1
        a_reached_llm.set()
        await b_may_release_a.wait()
        return _decision()

    with (
        patch(
            "pacca.api.routes.authorizations.orchestrator.process_decision",
            mock_process_decision,
        ),
        patch(
            "pacca.api.routes.authorizations.rag_engine.query",
            return_value=RetrievalOutcome(
                text="Mock guideline content", mode="pipeline", degraded=False, reason=None
            ),
        ),
    ):
        from pacca.api.routes.authorizations import submit_authorization

        async def run_a():
            session_a = maker()
            try:
                return await submit_authorization(
                    request=AuthorizationRequest(**_race_request(request_id, patient_id)),
                    session=session_a,
                )
            finally:
                await session_a.close()

        async def run_b():
            await a_reached_llm.wait()
            session_b = maker()
            try:
                return await submit_authorization(
                    request=AuthorizationRequest(**_race_request(request_id, patient_id)),
                    session=session_b,
                )
            except Exception as e:
                return e
            finally:
                b_may_release_a.set()
                await session_b.close()

        result_a, result_b = await asyncio.gather(run_a(), run_b())

    assert isinstance(result_a, AuthorizationDecision)
    assert isinstance(result_b, HTTPException), (
        f"expected B to be refused with an HTTPException on real Postgres, got {result_b!r}"
    )
    assert result_b.status_code == 409
    assert orchestrator_call_count == 1

    await _assert_race_outcome_and_third_duplicate_replays(
        maker, request_id, patient_id, result_a.decision_id
    )

    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {', '.join(_RACE_TABLES)} RESTART IDENTITY CASCADE"))
    await engine.dispose()


@_SKIP
@pytest.mark.asyncio
async def test_pre_existing_two_decision_state_returns_defined_answer_on_postgres(
    _migrated_url: str,
) -> None:
    """A pre-existing two-decision anomaly (the state the race above could
    have produced before FIX 1) must return a defined answer -- the earliest
    decision, replayed -- not `MultipleResultsFound` -> 500, on real
    Postgres."""
    from unittest.mock import AsyncMock, patch

    from pacca.db.repository import AuditRepository, AuthorizationRepository, DecisionRepository
    from pacca.integrations.vector_store import RetrievalOutcome
    from pacca.models.authorization import AuthorizationDecision, AuthorizationRequest
    from pacca.models.enums import AuthorizationStatus, ReviewTier

    request_id = "AUTH-PG-ANOMALY-1"
    patient_id = "P-PG-ANOMALY-1"

    engine = create_async_engine(_migrated_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {', '.join(_RACE_TABLES)} RESTART IDENTITY CASCADE"))

    async with maker() as session:
        req = AuthorizationRequest(**_race_request(request_id, patient_id))
        await AuthorizationRepository(session).create(req)
        await session.commit()

        first = AuthorizationDecision(
            decision_id="PA-pgfirst000000000",
            status=AuthorizationStatus.AUTO_APPROVED,
            confidence_score=0.97,
            rationale="first decision (earliest)",
            review_tier_used=ReviewTier.AUTOMATED,
        )
        second = AuthorizationDecision(
            decision_id="PA-pgsecond0000000",
            status=AuthorizationStatus.IN_REVIEW,
            confidence_score=0.5,
            rationale="second decision (anomalous duplicate)",
            review_tier_used=ReviewTier.AUTOMATED,
        )
        await DecisionRepository(session).create(first, request_id=request_id)
        await session.commit()
        await DecisionRepository(session).create(second, request_id=request_id)
        await session.commit()

    with (
        patch(
            "pacca.api.routes.authorizations.orchestrator.process_decision",
            new_callable=AsyncMock,
        ) as no_call_pd,
        patch(
            "pacca.api.routes.authorizations.rag_engine.query",
            return_value=RetrievalOutcome(
                text="Mock guideline content", mode="pipeline", degraded=False, reason=None
            ),
        ),
    ):
        from pacca.api.routes.authorizations import submit_authorization

        async with maker() as session2:
            result = await submit_authorization(
                request=AuthorizationRequest(**_race_request(request_id, patient_id)),
                session=session2,
            )

    assert isinstance(result, AuthorizationDecision)
    assert result.decision_id == "PA-pgfirst000000000"
    assert no_call_pd.call_count == 0

    async with maker() as verify:
        audit_rows = await AuditRepository(verify).get_by_request_id(request_id)
        assert any(r.action == "submission.multiple_decisions_anomaly" for r in audit_rows)

    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {', '.join(_RACE_TABLES)} RESTART IDENTITY CASCADE"))
    await engine.dispose()
