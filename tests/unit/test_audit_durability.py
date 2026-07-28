"""
Audit-write durability — crosses a REAL transaction boundary.

`tests/unit/test_audit_trail.py` patches `AuditRepository.log` wholesale and
hands the route an `AsyncMock` session, so it can only prove `log()` was
*called* with the right arguments in the right order — it cannot prove a row
actually survives a commit/rollback, because nothing in that test ever talks
to a database. This file exists to close that gap.

The defect (pre-fix): `AuditRepository.log()` did
`self.session.add(entry); await self.session.flush()` on the CALLER's own
session — the same session/transaction the business write uses. A flush is
only visible inside the still-open transaction. `get_session()` (the FastAPI
dependency, `pacca/db/session.py`) runs `await session.rollback()` on any
unhandled exception, which rolls the flushed-but-uncommitted audit row back
along with the failed business write. A failed request left ZERO audit rows
— exactly the case a "pre-write audit trail" exists to cover.

The fix: `AuditRepository.log()` now commits the audit row on an INDEPENDENT
session (`get_independent_session()`, `pacca/db/session.py`) drawn from the
same engine/pool, so its durability does not depend on the caller's
transaction ever committing.

Every test here uses a real SQLite engine + real `AuditLogModel` /
`AuthorizationRequestModel` rows via `Base.metadata.create_all` — no mocked
sessions, no patched `AuditRepository.log`.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from pacca.db.models import AuditLogModel, AuthorizationRequestModel, Base
from pacca.db.repository import AuditRepository, AuthorizationRepository, uuid7
from pacca.models.authorization import AuthorizationRequest
from pacca.models.clinical import ClinicalCase, EvidenceItem
from pacca.models.enums import EvidenceSourceType

# ── Shared fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
async def db(monkeypatch):
    """A real in-memory SQLite engine (StaticPool so every checkout — the
    'business' session AND every independent audit session — shares the same
    underlying connection/database, which is what proves cross-session
    visibility), with `AuditRepository`'s independent-session hook pointed at
    it so the repository under test writes to THIS engine, not the real app
    engine.

    `raising=False`: when this fixture is used to exercise the PRE-FIX
    `log()` (which never calls `get_independent_session` at all), setting an
    attribute that method doesn't use is a harmless no-op — that's what lets
    the same test function detect both the pre-fix and post-fix behavior by
    swapping the contents of `repository.py` underneath it (see the
    fail-then-pass proof in the session report).
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    @asynccontextmanager
    async def _independent_session():
        async with maker() as s:
            yield s

    monkeypatch.setattr(
        "pacca.db.repository.get_independent_session", _independent_session, raising=False
    )

    yield maker

    await engine.dispose()


def _sample_request(request_id: str) -> AuthorizationRequest:
    return AuthorizationRequest(
        request_id=request_id,
        patient_id=f"P-{request_id}",
        provider_npi="1234567890",
        clinical_case=ClinicalCase(
            patient_id=f"P-{request_id}",
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


async def _legacy_log(session, **kwargs) -> AuditLogModel:
    """The PRE-FIX `AuditRepository.log()` body, reconstructed inline (no
    `git stash` — see CLAUDE.md worktree rules): `session.add()` +
    `await session.flush()` on the CALLER's own session. Used only to prove
    the defect this bugfix addresses actually existed; not code under test."""
    entry = AuditLogModel(
        entry_id=str(uuid7()),
        request_id=kwargs.get("request_id"),
        decision_id=kwargs.get("decision_id"),
        correlation_id=kwargs.get("correlation_id"),
        action=kwargs["action"],
        actor=kwargs["actor"],
        actor_type=kwargs["actor_type"],
        details=kwargs.get("details"),
        success=kwargs.get("success", True),
    )
    session.add(entry)
    await session.flush()
    return entry


# ── Test 1 (headline): rollback must not erase the audit trail ─────────────


class TestSurvivesRollback:
    @pytest.mark.asyncio
    async def test_prefix_behavior_documented_as_losing_the_row(self, db):
        """Documents the defect directly: an audit row that is only
        flush()ed on the caller's own session (the pre-fix code path,
        reconstructed as `_legacy_log` since we cannot `git stash`) does
        NOT survive that session's rollback. This is not the fix under
        test — it is proof the failure mode is real, not hypothetical."""
        maker = db
        async with maker() as session:
            await _legacy_log(session, action="test.legacy", actor="x", actor_type="system")
            try:
                raise RuntimeError("simulated business failure")
            except RuntimeError:
                await session.rollback()

        async with maker() as check:
            rows = (await check.execute(select(AuditLogModel))).scalars().all()
        assert rows == [], (
            "Pre-fix behavior: a flush()-only audit row is rolled back with "
            "the business transaction — zero audit rows survive a failure."
        )

    @pytest.mark.asyncio
    async def test_audit_row_survives_business_transaction_rollback(self, db):
        """THE headline regression test. Run this against the pre-fix
        `AuditRepository.log()` (git-show the old file in over
        `src/pacca/db/repository.py`, keeping this test file as-is) and it
        FAILS with the same assertion as the test above. Run it against the
        fixed `log()` and it PASSES — see the pasted fail-then-pass proof in
        the session report."""
        maker = db
        async with maker() as session:
            repo = AuditRepository(session)
            await repo.log(action="test.postfix", actor="x", actor_type="system")
            try:
                raise RuntimeError("simulated business failure")
            except RuntimeError:
                await session.rollback()

        async with maker() as check:
            rows = (await check.execute(select(AuditLogModel))).scalars().all()
        assert len(rows) == 1, (
            f"Expected the audit row to survive the business rollback, found "
            f"{len(rows)} row(s). An independently-committed audit write must "
            "not be undone by the caller's own transaction failing."
        )
        assert rows[0].action == "test.postfix"


# ── Test 2: happy path — ordering AND presence ──────────────────────────────


class TestHappyPathOrdering:
    @pytest.mark.asyncio
    async def test_audit_row_is_durable_before_business_write_even_happens(self, db):
        """Proves the ordering guarantee precisely: by the time `log()`
        returns, the audit row is ALREADY visible to a totally independent
        reader connection — before the business write is even attempted,
        let alone committed."""
        maker = db
        async with maker() as session:
            audit = AuditRepository(session)
            await audit.log(
                action="intent.declared",
                actor="orchestrator",
                actor_type="system",
                request_id="AUTH-HP-1",
                correlation_id="corr-hp-1",
            )

            # A separate reader, before this session does anything else and
            # long before it commits, already sees the audit row.
            async with maker() as mid_flight_reader:
                mid_rows = (await mid_flight_reader.execute(select(AuditLogModel))).scalars().all()
            assert len(mid_rows) == 1
            assert mid_rows[0].action == "intent.declared"

            await AuthorizationRepository(session).create(_sample_request("AUTH-HP-1"))
            await audit.log(
                action="authorization_submitted",
                actor="provider",
                actor_type="provider",
                request_id="AUTH-HP-1",
                correlation_id="corr-hp-1",
            )
            await session.commit()

        async with maker() as check:
            audit_rows = (
                (await check.execute(select(AuditLogModel).order_by(AuditLogModel.id)))
                .scalars()
                .all()
            )
            request_rows = (await check.execute(select(AuthorizationRequestModel))).scalars().all()

        assert [r.action for r in audit_rows] == ["intent.declared", "authorization_submitted"]
        assert len(request_rows) == 1
        assert request_rows[0].request_id == "AUTH-HP-1"


# ── Test 3: an audit-write failure must never 500 a working request ────────


class _CapturingLogger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    def error(self, event: str, **kw) -> None:
        self.calls.append(("error", event, kw))

    def warning(self, event: str, **kw) -> None:
        self.calls.append(("warning", event, kw))

    def info(self, event: str, **kw) -> None:  # pragma: no cover - unused here
        pass


class TestAuditFailureIsNonFatal:
    @pytest.mark.asyncio
    async def test_independent_write_failure_does_not_raise(self, db, monkeypatch):
        """If the independent audit connection itself cannot be opened
        (simulating a DB outage affecting just that new connection), `log()`
        must not raise — a working request must never become a 500 because
        its OWN audit write failed. The failure must still be logged loudly
        (never swallowed silently)."""
        maker = db

        @asynccontextmanager
        async def _boom():
            raise RuntimeError("simulated audit-connection outage")
            yield  # pragma: no cover - unreachable, required for generator shape

        monkeypatch.setattr("pacca.db.repository.get_independent_session", _boom)
        capturing_logger = _CapturingLogger()
        monkeypatch.setattr("pacca.db.repository.logger", capturing_logger)

        async with maker() as session:
            repo = AuditRepository(session)
            entry = await repo.log(action="test.failure", actor="x", actor_type="system")

        # No exception propagated — this IS the assertion. Additionally: the
        # caller still gets back a populated (if unpersisted) entry object.
        assert entry.action == "test.failure"

        assert any(event == "audit_write_failed" for level, event, kw in capturing_logger.calls)
        assert all(level == "error" for level, event, kw in capturing_logger.calls)

    @pytest.mark.asyncio
    async def test_submit_route_returns_decision_even_if_audit_write_fails(self, db, monkeypatch):
        """End-to-end version of the above: even with every independent
        audit write failing, `submit_authorization` must still return a
        real decision, not a 500 — the audit subsystem is not on the
        request's critical path for success."""
        from pacca.integrations.vector_store import RetrievalOutcome
        from pacca.models.authorization import AuthorizationDecision
        from pacca.models.enums import AuthorizationStatus, ReviewTier

        maker = db

        @asynccontextmanager
        async def _boom():
            raise RuntimeError("simulated audit-connection outage")
            yield  # pragma: no cover

        monkeypatch.setattr("pacca.db.repository.get_independent_session", _boom)
        monkeypatch.setattr("pacca.db.repository.logger", _CapturingLogger())

        mock_decision = AuthorizationDecision(
            decision_id="DEC-NOAUDIT-1",
            status=AuthorizationStatus.AUTO_APPROVED,
            confidence_score=0.9,
            rationale="ok",
            review_tier_used=ReviewTier.AUTOMATED,
        )

        with (
            patch(
                "pacca.api.routes.authorizations.orchestrator.process_decision",
                new_callable=AsyncMock,
                return_value=mock_decision,
            ),
            patch(
                "pacca.api.routes.authorizations.rag_engine.query",
                return_value=RetrievalOutcome(
                    text="guideline text", mode="pipeline", degraded=False, reason=None
                ),
            ),
        ):
            from pacca.api.routes.authorizations import submit_authorization

            req = _sample_request("AUTH-NOAUDIT-1")
            async with maker() as session:
                result = await submit_authorization(request=req, session=session)
                await session.commit()

        assert result.status == AuthorizationStatus.AUTO_APPROVED


# ── Test 4: no duplicate rows on any path, including the FK-retry path ─────


class TestNoDuplicateRows:
    @pytest.mark.asyncio
    async def test_happy_path_writes_exactly_one_row_per_call(self, db):
        maker = db
        async with maker() as session:
            repo = AuditRepository(session)
            await repo.log(action="test.once", actor="x", actor_type="system")

        async with maker() as check:
            rows = (await check.execute(select(AuditLogModel))).scalars().all()
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_fk_deferred_retry_persists_exactly_one_row_not_two(self, monkeypatch):
        """Simulates the exact Postgres condition `test_audit_fk_deferrable.py`
        documents (B3): `audit_logs.request_id` carries a DEFERRABLE INITIALLY
        DEFERRED FK to `authorization_requests`, deferred specifically so the
        pre-write `intent.declared` / `authorization_submitted` audit rows —
        written before the parent request row exists — pass at the SAME
        transaction's eventual commit. Moving the audit write onto its own,
        immediately-committing transaction breaks that: the deferred check
        now runs at OUR commit, before the parent row exists, and fails.
        `log()` retries once with `request_id=None` (preserved in
        `details['_unlinked_request_id']`) so the row is not lost — this
        proves the retry lands exactly ONE row, not a duplicate from the
        failed first attempt plus the retry.

        SQLite does not enforce FKs by default (unlike Postgres); an
        explicit `PRAGMA foreign_keys=ON` on connect reproduces real FK
        enforcement so this test can exercise the fallback without a live
        Postgres.
        """
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)

        @event.listens_for(engine.sync_engine, "connect")
        def _enable_fk(dbapi_conn, _connection_record):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

        @asynccontextmanager
        async def _independent_session():
            async with maker() as s:
                yield s

        monkeypatch.setattr("pacca.db.repository.get_independent_session", _independent_session)

        async with maker() as session:
            repo = AuditRepository(session)
            entry = await repo.log(
                action="intent.declared",
                actor="orchestrator",
                actor_type="system",
                request_id="AUTH-NOT-YET-COMMITTED",
                correlation_id="corr-fk-1",
            )

        async with maker() as check:
            rows = (await check.execute(select(AuditLogModel))).scalars().all()

        assert len(rows) == 1, (
            f"Expected exactly one persisted row from the FK-retry fallback, "
            f"got {len(rows)} — a duplicate would mean the failed first "
            "attempt also left a row behind."
        )
        assert rows[0].request_id is None
        assert rows[0].details["_unlinked_request_id"] == "AUTH-NOT-YET-COMMITTED"
        assert rows[0].correlation_id == "corr-fk-1"
        # The entry returned to the caller reflects what was actually
        # persisted (unlinked), not the originally-requested request_id.
        assert entry.request_id is None

        await engine.dispose()


# ── Test 5: the full submit route, real DB, expected audit sequence ────────


class TestSubmitRouteEndToEnd:
    @pytest.mark.asyncio
    async def test_submit_route_writes_expected_audit_sequence_against_a_real_db(self, db):
        """Unlike `test_audit_trail.py` (which patches `AuditRepository.log`
        and uses an `AsyncMock` session), this drives `submit_authorization`
        with a REAL session against a REAL engine, proving the full audit
        sequence — `intent.declared` first, then `authorization_submitted`,
        then the scope-guard `scope.allow` records, then
        `authorization_decision_made` — actually lands as real committed
        rows, not just mock-call arguments."""
        from pacca.integrations.vector_store import RetrievalOutcome
        from pacca.models.authorization import AuthorizationDecision
        from pacca.models.enums import AuthorizationStatus, ReviewTier

        maker = db

        mock_decision = AuthorizationDecision(
            decision_id="DEC-E2E-1",
            status=AuthorizationStatus.AUTO_APPROVED,
            confidence_score=0.9,
            rationale="NCCN guidelines support this treatment.",
            review_tier_used=ReviewTier.AUTOMATED,
        )

        with (
            patch(
                "pacca.api.routes.authorizations.orchestrator.process_decision",
                new_callable=AsyncMock,
                return_value=mock_decision,
            ),
            patch(
                "pacca.api.routes.authorizations.rag_engine.query",
                return_value=RetrievalOutcome(
                    text="guideline text", mode="pipeline", degraded=False, reason=None
                ),
            ),
        ):
            from pacca.api.routes.authorizations import submit_authorization

            req = _sample_request("AUTH-E2E-1")
            async with maker() as session:
                result = await submit_authorization(request=req, session=session)
                await session.commit()

        assert result.status == AuthorizationStatus.AUTO_APPROVED

        async with maker() as check:
            rows = (
                (await check.execute(select(AuditLogModel).order_by(AuditLogModel.id)))
                .scalars()
                .all()
            )
        actions = [r.action for r in rows]
        assert actions[0] == "intent.declared"
        assert "authorization_submitted" in actions
        assert "authorization_decision_made" in actions
        assert actions.index("authorization_submitted") < actions.index(
            "authorization_decision_made"
        )
        # Every row shares one correlation_id — the whole point of the trail.
        correlation_ids = {r.correlation_id for r in rows}
        assert len(correlation_ids) == 1
