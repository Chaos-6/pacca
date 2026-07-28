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
session (`get_independent_session(bind)`, `pacca/db/session.py`) bound to the
SAME engine as the caller's own session (`self.session.bind` — NOT the
process-global default engine; see the chg-23 CRITICAL 2 note on
`TestEngineMatchesCallerSession` below), so its durability does not depend on
the caller's transaction ever committing.

chg-23 also removed the request_id FK entirely (migration 007 — see that
migration's docstring). `TestPersistenceFailureDoesNotPoisonTheReturn`
reconstructs the exact condition that used to trigger a retry (and the
poisoned-return bug the Validator found in it) with a throwaway FK-enforced
SQLite table, to prove the no-retry replacement handles a hard persistence
failure without raising OR corrupting the object handed back to the caller.

Every test here uses a real SQLite engine + real `AuditLogModel` /
`AuthorizationRequestModel` rows via `Base.metadata.create_all` — no mocked
sessions, no patched `AuditRepository.log`.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from pacca.db.models import AuditLogModel, AuthorizationRequestModel, Base
from pacca.db.repository import AuditRepository, AuthorizationRepository, uuid7
from pacca.models.authorization import AuthorizationRequest
from pacca.models.clinical import ClinicalCase, EvidenceItem
from pacca.models.enums import EvidenceSourceType

# ── Shared fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
async def db():
    """A real in-memory SQLite engine (StaticPool so every checkout shares the
    same underlying connection/database — required to prove cross-session
    visibility).

    No monkeypatching of `get_independent_session` here (unlike this file's
    first version): chg-23's CRITICAL 2 fix makes `AuditRepository.log()`
    derive its independent session's engine from `self.session.bind`, so a
    session created from THIS fixture's `maker` is automatically enough —
    the repository never reaches for the process-global engine at all. See
    `TestEngineMatchesCallerSession` for the test that pins this down
    directly.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

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


class _CapturingLogger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    def error(self, event: str, **kw) -> None:
        self.calls.append(("error", event, kw))

    def warning(self, event: str, **kw) -> None:
        self.calls.append(("warning", event, kw))

    def info(self, event: str, **kw) -> None:  # pragma: no cover - unused here
        pass


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
        the session report. Also proven on real Postgres in
        `tests/integration/test_submit_postgres.py::test_audit_row_survives_business_rollback_on_real_postgres`."""
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
        # request_id has no FK anymore (chg-23, migration 007) but the value
        # itself is still recorded plainly on the audit rows.
        assert {r.request_id for r in audit_rows} == {"AUTH-HP-1"}


# ── Test 3: an audit-write failure must never 500 a working request ────────


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
        async def _boom(bind: AsyncEngine):
            raise RuntimeError("simulated audit-connection outage")
            yield  # pragma: no cover - unreachable, required for generator shape

        monkeypatch.setattr("pacca.db.repository.get_independent_session", _boom)
        capturing_logger = _CapturingLogger()
        monkeypatch.setattr("pacca.db.repository.logger", capturing_logger)

        async with maker() as session:
            repo = AuditRepository(session)
            entry = await repo.log(action="test.failure", actor="x", actor_type="system")

        # No exception propagated — this IS the assertion. Additionally: the
        # caller still gets back a fully populated, safely-readable entry.
        assert entry.action == "test.failure"
        assert entry.actor == "x"

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
        async def _boom(bind: AsyncEngine):
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


# ── Test 4: no duplicate rows on the happy path ─────────────────────────────


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


# ── Test 5: persistence failure must not raise AND must not poison `entry` ──


class TestPersistenceFailureDoesNotPoisonTheReturn:
    @pytest.mark.asyncio
    async def test_hard_db_failure_leaves_the_returned_entry_safely_readable(self, monkeypatch):
        """Reconstructs the EXACT condition that used to trigger chg-23's
        (now-removed) request_id FK retry, to prove the no-retry replacement
        handles it correctly: `request_id` pointing at a not-yet-committed
        `authorization_requests` row, against a throwaway SQLite table that
        still carries the pre-migration-007 FK with `PRAGMA foreign_keys=ON`
        forcing real enforcement (SQLite disables it by default — this is
        why the original defect needed a real Postgres container to surface
        at all).

        This is precisely the scenario the chg-23 Validator review flagged
        as the poisoned-return bug: SQLAlchemy's flush-during-commit
        assigns the ORM instance an identity key before the deferred FK
        check fails, so any object added to that session becomes a
        detached, expired instance once the session closes — reading any
        attribute off it afterward raises `DetachedInstanceError`. The
        fix (`AuditRepository._persist_independently`) never adds the
        object it returns to any session at all; only a disposable COPY
        is added and (here) fails to commit. Every field on the returned
        `entry` must remain safely readable regardless.
        """
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)

        @event.listens_for(engine.sync_engine, "connect")
        def _enable_fk(dbapi_conn, _connection_record):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

        # The pre-migration-007 audit_logs shape: same columns, but WITH the
        # FK to authorization_requests, built inline rather than importing
        # AuditLogModel (which no longer has it) — this table intentionally
        # does NOT match the current model; it exists only to reproduce the
        # old, already-fixed condition.
        import sqlalchemy as sa

        metadata = sa.MetaData()
        sa.Table(
            "audit_logs",
            metadata,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("entry_id", sa.String(50), unique=True),
            sa.Column("timestamp", sa.DateTime(), nullable=False, default=sa.func.now()),
            sa.Column(
                "request_id",
                sa.String(50),
                sa.ForeignKey("authorization_requests.request_id"),
                nullable=True,
            ),
            sa.Column("decision_id", sa.String(50), nullable=True),
            sa.Column("correlation_id", sa.String(50), nullable=True),
            sa.Column("action", sa.String(100), nullable=False),
            sa.Column("actor", sa.String(100), nullable=False),
            sa.Column("actor_type", sa.String(30), nullable=False),
            sa.Column("details", sa.JSON(), nullable=True),
            sa.Column("input_summary", sa.Text(), nullable=True),
            sa.Column("output_summary", sa.Text(), nullable=True),
            sa.Column("success", sa.Boolean(), default=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("duration_ms", sa.Integer(), nullable=True),
            sa.Column("token_usage", sa.JSON(), nullable=True),
        )
        sa.Table(
            "authorization_requests",
            metadata,
            sa.Column("request_id", sa.String(50), primary_key=True),
        )
        async with engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
        maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

        capturing_logger = _CapturingLogger()
        monkeypatch.setattr("pacca.db.repository.logger", capturing_logger)

        async with maker() as session:
            repo = AuditRepository(session)
            entry = await repo.log(
                action="intent.declared",
                actor="orchestrator",
                actor_type="system",
                request_id="AUTH-DOES-NOT-EXIST-YET",
                correlation_id="corr-poison-1",
            )

        # The failure happened (logged, not swallowed) ...
        assert any(event == "audit_write_failed" for level, event, kw in capturing_logger.calls)
        # ... and did NOT raise out of log() ...
        # ... and every field on the returned object is still safely
        # readable — this is the assertion that would have raised
        # DetachedInstanceError under the old retry-by-mutating-`entry`
        # design once the first commit's flush had assigned it an
        # identity key.
        assert entry.action == "intent.declared"
        assert entry.actor == "orchestrator"
        assert entry.request_id == "AUTH-DOES-NOT-EXIST-YET"
        assert entry.correlation_id == "corr-poison-1"

        await engine.dispose()


# ── Test 6 (CRITICAL 2): the independent write must land in the CALLER's DB ─


class TestEngineMatchesCallerSession:
    @pytest.mark.asyncio
    async def test_audit_write_never_touches_the_global_engine(self, db, monkeypatch):
        """chg-23 Validator CRITICAL 2: an earlier version of
        `get_independent_session()` always called the process-global
        `get_session_factory()` / `get_engine()` (bound to
        `settings.database_url`), regardless of what engine the CALLER's
        own session actually used. In this test's fixture (`db`, a
        dedicated per-test SQLite engine, never the global one) that meant
        the audit row silently landed in a DIFFERENT database than the
        business write it was supposed to accompany.

        Proof: monkeypatch the global factory/engine getters to raise if
        called at all, then perform a real audit write through a session
        bound to the per-test engine. The write must still succeed AND
        land in the per-test engine's database — proving the code path
        never reaches for global state and instead uses the caller's own
        engine (`self.session.bind`).
        """

        def _must_not_be_called(*a, **kw):
            raise AssertionError(
                "get_independent_session must not fall back to the process-global "
                "engine/session factory — it must use the caller's own session.bind"
            )

        monkeypatch.setattr("pacca.db.session.get_session_factory", _must_not_be_called)
        monkeypatch.setattr("pacca.db.session.get_engine", _must_not_be_called)

        maker = db
        async with maker() as session:
            repo = AuditRepository(session)
            await repo.log(
                action="test.engine.matches.caller",
                actor="x",
                actor_type="system",
                correlation_id="corr-engine-match-1",
            )

        async with maker() as check:
            rows = (
                (
                    await check.execute(
                        select(AuditLogModel).where(
                            AuditLogModel.correlation_id == "corr-engine-match-1"
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == 1, (
            "the audit row must land in the SAME database as the caller's "
            "session, not silently vanish into a different (global) engine"
        )


# ── Test 7: the full submit route, real DB, expected audit sequence ────────


class TestSubmitRouteEndToEnd:
    @pytest.mark.asyncio
    async def test_submit_route_writes_expected_audit_sequence_against_a_real_db(self, db):
        """Unlike `test_audit_trail.py` (which patches `AuditRepository.log`
        and uses an `AsyncMock` session), this drives `submit_authorization`
        with a REAL session against a REAL engine, proving the full audit
        sequence — `intent.declared` first, then `authorization_submitted`,
        then the scope-guard `scope.allow` records, then
        `authorization_decision_made` — actually lands as real committed
        rows, not just mock-call arguments. The full 7-row happy-path
        census (all request_id-linked) is proven against real Postgres in
        `tests/integration/test_submit_postgres.py::test_submit_commits_with_zero_orphaned_audit_rows`."""
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
