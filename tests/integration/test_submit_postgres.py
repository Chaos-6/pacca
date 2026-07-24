"""
Real-Postgres integration test for the submit path (B2/B3 masking guard).

The entire unit suite runs on SQLite, which cannot catch two whole classes of
production bug:

  * B2 — `postgresql.JSONB` columns that don't compile under SQLite.
  * B3 — foreign keys, which SQLite does not enforce at all.

B3 in particular was invisible to 712 green tests: the submit route writes two
`request_id`-bearing audit rows (`intent.declared`, `authorization_submitted`)
before the parent `authorization_requests` row, and on Postgres a non-deferrable
FK rejects that first flush. The fix (deferrable FK) can only be *verified* on
real Postgres.

This test drives the actual submit handler against a migration-built Postgres
(schema from `alembic upgrade head`, so it is production-shaped and
migration-tracked, not `create_all`). The LLM and RAG are patched — this test is
about persistence and referential integrity, not clinical accuracy (the clinical
gate covers that) — but the audit writes, the parent/decision persistence, and
the FK all hit real Postgres.

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
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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
    The B3 guard. Driving the real submit handler against real Postgres, the
    whole transaction must COMMIT and leave no audit row referencing a
    non-existent request. On a non-deferrable FK this raises at the first audit
    flush; SQLite would silently pass either way.
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
            return_value="Mock guideline content",
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
    assert audit_rows >= 2, "the pre-flight + decision audit rows were not persisted"
    assert orphans == 0, "an audit row references a non-existent request (B3)"


@_SKIP
@pytest.mark.asyncio
async def test_audit_fk_is_deferrable_in_the_live_catalog(_migrated_url: str) -> None:
    """The migration produced a genuinely deferrable FK in the real Postgres catalog."""
    engine = create_async_engine(_migrated_url)
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT condeferrable, condeferred FROM pg_constraint "
                    "WHERE conname = 'audit_logs_request_id_fkey' AND contype = 'f'"
                )
            )
        ).first()
    await engine.dispose()
    assert row is not None, "audit_logs_request_id_fkey is missing on the live DB"
    assert row.condeferrable and row.condeferred, "the FK is not DEFERRABLE INITIALLY DEFERRED"
