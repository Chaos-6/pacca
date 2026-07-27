"""
RBAC test suite — the completion bar for the design spec's §5 test matrix.

Layout mirrors the spec's own sections:
  5.1 Core mechanics            -> TestRankOrdering, TestRequireMinRoleAllows*,
                                    TestRequireMinRoleDenies*, TestGetCurrentUser
  5.2 Fail-closed                -> TestFailClosedRoleParsing,
                                    TestFailClosedThroughRequireMinRole
  5.3 Privilege-escalation       -> TestRegistrationCannotSetRole,
                                    TestForgedRoleClaimGrantsNoElevation
  5.4 Endpoint matrix            -> TestEndpointMatrix
                                    (WebSocket 4403/4401 lives in
                                    tests/unit/api/test_sme_authoring_websocket.py,
                                    next to the rest of that protocol's tests)
  5.5 Promotion endpoint         -> TestPromotionEndpoint
  5.6 Migration                  -> TestMigrationRoundTrip
  5.7 CLI                        -> TestCreateAdminCLI
  5.8 Regression                 -> satisfied by `make test` itself (see the
                                    Executor's final report, not a function here)

Two test-DB strategies are used, chosen per what a test actually needs:
  - In-memory SQLite + direct dependency calls (no HTTP, no on-disk file) for
    the pure dependency-layer mechanics (5.1, 5.2) — fast and DB-light.
  - An isolated on-disk SQLite file behind a real TestClient(app) for
    anything that must prove the ACTUAL router wiring in main.py /
    routes/*.py is correct end-to-end (5.3 endpoint tests, 5.4, 5.5) — this
    is what would catch someone wiring the wrong dependency on the real app.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest
from click.testing import CliRunner
from fastapi import HTTPException
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import pacca.api.models.user as user_module
import pacca.db.session as db_session_module
from pacca.api.auth import ALGORITHM, SECRET_KEY, get_password_hash, verify_password
from pacca.api.database import Base as AuthBase
from pacca.api.main import app
from pacca.api.models.user import User
from pacca.api.rbac import (
    _RANK,
    Role,
    get_current_user,
    parse_role,
    require_min_role,
)
from pacca.cli import pacca_cli
from pacca.config.settings import get_settings
from pacca.db.models import Base as DomainBase
from pacca.integrations.vector_store import RetrievalOutcome

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

REPO_ROOT = Path(__file__).resolve().parents[2]


# =============================================================================
# Shared helpers
# =============================================================================


def _unsaved_user(role: str | None, username: str = "u") -> User:
    """A plain (never persisted) User row carrying the given raw role string.

    require_min_role/get_current_user are split on purpose (see rbac.py):
    once you already have a resolved `User`, the role check needs no DB at
    all, so these mechanics tests construct the object directly rather than
    round-tripping through SQLite.
    """
    return User(username=username, hashed_password="x", role=role)


def _token_for(username: str, extra_claims: dict[str, Any] | None = None) -> str:
    payload: dict[str, Any] = {"sub": username, "exp": datetime.now(UTC) + timedelta(minutes=30)}
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _minimal_authorization_payload(request_id: str) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "patient_id": f"P-{request_id}",
        "provider_npi": "1234567890",
        "clinical_case": {
            "patient_id": f"P-{request_id}",
            "primary_diagnosis_code": "Z00.0",
            "procedure_code": "99999",
            "evidence": [
                {
                    "id": "e1",
                    "source_type": "CLINICAL_NOTE",
                    "description": "RBAC boundary test — not a clinical assertion",
                    "original_text": "...",
                    "confidence": 1.0,
                }
            ],
        },
    }


def _alembic_env(database_url: str) -> dict[str, str]:
    """
    Build the subprocess environment for an `alembic` invocation.

    `pyproject.toml`'s `pythonpath = ["src"]` is a pytest-only mechanism — it
    puts `src/` on `sys.path` for the CURRENT pytest process, but a spawned
    subprocess starts with a fresh `sys.path` and never sees it. Whichever
    interpreter `sys.executable` names may not have `pacca` pip-installed at
    all (a system/conda Python on the shell PATH, for instance) — it can
    still run `alembic` (a real dependency), but `alembic/env.py`'s
    `import pacca.api.models.user` fails with `ModuleNotFoundError` unless
    `src/` is explicitly back on PYTHONPATH for that child process.
    """
    env = {**os.environ, "DATABASE_URL": database_url}
    src_path = str(REPO_ROOT / "src")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{src_path}{os.pathsep}{existing}" if existing else src_path
    return env


def _run_alembic(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,  # callers assert on .returncode themselves
    )


# =============================================================================
# 5.1 Core mechanics
# =============================================================================


class TestRankOrdering:
    def test_rank_ordering_clinician_lt_medical_director_lt_admin(self) -> None:
        """Item 1: clinician < medical_director < admin."""
        assert _RANK[Role.CLINICIAN] < _RANK[Role.MEDICAL_DIRECTOR] < _RANK[Role.ADMIN]


class TestRequireMinRoleAllows:
    @pytest.mark.parametrize(
        "minimum,actual",
        [
            (Role.CLINICIAN, Role.CLINICIAN),
            (Role.CLINICIAN, Role.MEDICAL_DIRECTOR),
            (Role.CLINICIAN, Role.ADMIN),
            (Role.MEDICAL_DIRECTOR, Role.MEDICAL_DIRECTOR),
            (Role.MEDICAL_DIRECTOR, Role.ADMIN),
            (Role.ADMIN, Role.ADMIN),
        ],
    )
    async def test_allows_role_and_every_role_above(self, minimum: Role, actual: Role) -> None:
        """Item 2: require_min_role(X) allows the exact role and every role above it."""
        dependency = require_min_role(minimum)
        result = await dependency(user=_unsaved_user(actual.value))
        assert result.role == actual.value


class TestRequireMinRoleDenies:
    @pytest.mark.parametrize(
        "minimum,actual",
        [
            (Role.MEDICAL_DIRECTOR, Role.CLINICIAN),
            (Role.ADMIN, Role.CLINICIAN),
            (Role.ADMIN, Role.MEDICAL_DIRECTOR),
        ],
    )
    async def test_denies_every_role_below(self, minimum: Role, actual: Role) -> None:
        """Item 3: require_min_role(X) denies (403) every role below it."""
        dependency = require_min_role(minimum)
        with pytest.raises(HTTPException) as exc_info:
            await dependency(user=_unsaved_user(actual.value))
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "Insufficient role for this operation"


@pytest.fixture
async def auth_session() -> AsyncIterator[AsyncSession]:
    """An in-memory `users`-table-only async session (no on-disk file)."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(AuthBase.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


class TestGetCurrentUser:
    async def test_401_when_username_has_no_db_row(self, auth_session: AsyncSession) -> None:
        """Item 4: a token's username with no DB row -> 401 (account, not credential, gone)."""
        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(username="ghost-user", session=auth_session)
        assert exc_info.value.status_code == 401

    async def test_resolves_existing_user(self, auth_session: AsyncSession) -> None:
        auth_session.add(User(username="alice", hashed_password="x", role="clinician"))
        await auth_session.commit()
        user = await get_current_user(username="alice", session=auth_session)
        assert user.username == "alice"
        assert user.role == "clinician"


# =============================================================================
# 5.2 Fail-closed (each condition gets its own test)
# =============================================================================


class TestFailClosedRoleParsing:
    def test_unknown_role_string_parses_to_none(self) -> None:
        """Item 5: stored role "superuser" (not a Role member) -> None, never defaulted."""
        assert parse_role("superuser") is None

    def test_empty_and_null_role_parse_to_none(self) -> None:
        """Item 6: stored role "" / NULL -> None."""
        assert parse_role("") is None
        assert parse_role(None) is None

    def test_wrong_case_role_parses_to_none(self) -> None:
        """Item 7: role comparison is exact and case-sensitive ("Admin" != Role.ADMIN)."""
        assert parse_role("Admin") is None
        assert parse_role("ADMIN") is None
        assert parse_role("clinician") is Role.CLINICIAN  # sanity: the real value still parses


class TestFailClosedThroughRequireMinRole:
    """The same three conditions, proven through the actual dependency (not just the parser)."""

    async def test_unknown_role_string_denied(self) -> None:
        dependency = require_min_role(Role.CLINICIAN)
        with pytest.raises(HTTPException) as exc_info:
            await dependency(user=_unsaved_user("superuser"))
        assert exc_info.value.status_code == 403

    async def test_null_role_denied(self) -> None:
        dependency = require_min_role(Role.CLINICIAN)
        with pytest.raises(HTTPException) as exc_info:
            await dependency(user=_unsaved_user(None))
        assert exc_info.value.status_code == 403

    async def test_wrong_case_role_denied(self) -> None:
        dependency = require_min_role(Role.CLINICIAN)
        with pytest.raises(HTTPException) as exc_info:
            await dependency(user=_unsaved_user("Admin"))
        assert exc_info.value.status_code == 403


# =============================================================================
# Shared on-disk-DB fixture for the HTTP-level tests (5.3, 5.4, 5.5)
# =============================================================================


@pytest.fixture
def rbac_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """
    Full-app TestClient wired to an isolated on-disk SQLite DB.

    Builds BOTH declarative Bases' schema — auth `users` AND the domain
    tables (authorization_requests, authorization_decisions, ...) — because
    GET /review-queue joins across them; a users-only schema would 500 on
    that endpoint with "no such table", which would look like an RBAC
    failure but wouldn't be one. DATABASE_URL is monkeypatched per-test so
    the real dev `./pacca.db` is never touched, and the process-global
    engine/session-factory singletons in `pacca.db.session` are reset so the
    new URL actually takes effect.
    """
    db_path = tmp_path / "rbac_endpoint_test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    get_settings.cache_clear()
    db_session_module._engine = None
    db_session_module._session_factory = None

    sync_engine = create_engine(f"sqlite:///{db_path}")
    try:
        AuthBase.metadata.create_all(sync_engine)
        DomainBase.metadata.create_all(sync_engine)
    finally:
        sync_engine.dispose()

    yield TestClient(app)

    db_session_module._engine = None
    db_session_module._session_factory = None
    get_settings.cache_clear()


def _seed_user(username: str, role: str | None, password: str = "test-password") -> None:
    """Insert a user row into whichever DB DATABASE_URL currently points at."""
    sync_url = os.environ["DATABASE_URL"].replace("+aiosqlite", "")
    engine = create_engine(sync_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                user_module.User.__table__.insert().values(
                    username=username,
                    hashed_password=get_password_hash(password),
                    role=role,
                )
            )
    finally:
        engine.dispose()


def _read_role(username: str) -> str | None:
    sync_url = os.environ["DATABASE_URL"].replace("+aiosqlite", "")
    engine = create_engine(sync_url)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT role FROM users WHERE username = :u"), {"u": username}
            ).first()
    finally:
        engine.dispose()
    return None if row is None else row.role


# =============================================================================
# 5.3 Privilege-escalation surface
# =============================================================================


class TestRegistrationCannotSetRole:
    def test_role_field_in_register_body_returns_422(self, rbac_client: TestClient) -> None:
        """Item 8: POST /register/ with a "role" key -> 422 (extra="forbid")."""
        response = rbac_client.post(
            "/api/v1/register/",
            json={"username": "sneaky-registrant", "password": "pw123456", "role": "admin"},
        )
        assert response.status_code == 422

    def test_registered_user_has_clinician_role_in_db(self, rbac_client: TestClient) -> None:
        """Item 9: a user created through /register/ has role clinician in the database."""
        response = rbac_client.post(
            "/api/v1/register/",
            json={"username": "freshly-registered", "password": "pw123456"},
        )
        assert response.status_code == 200
        assert _read_role("freshly-registered") == "clinician"


class TestForgedRoleClaimGrantsNoElevation:
    def test_forged_admin_claim_in_jwt_grants_no_elevation(self, rbac_client: TestClient) -> None:
        """
        Item 10 (headline adversarial test): a JWT hand-forged to carry
        {"role": "admin"} grants NO elevation. The account is a real
        clinician in the DB; the claim is signed with the correct
        SECRET_KEY (this isn't a signature-forgery test — it's proving the
        claim is never even READ) but must still be ignored, because role
        is resolved from the database on every request, never from the
        token (see rbac.py module docstring for why).
        """
        _seed_user("forged-claim-clinician", role="clinician")
        token = _token_for("forged-claim-clinician", extra_claims={"role": "admin"})

        response = rbac_client.get(
            "/api/v1/admin/config", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 403


# =============================================================================
# 5.4 Endpoint matrix
# =============================================================================


class TestEndpointMatrix:
    def test_admin_router_allows_admin(self, rbac_client: TestClient) -> None:
        _seed_user("admin-boundary", role="admin")
        response = rbac_client.get(
            "/api/v1/admin/config",
            headers={"Authorization": f"Bearer {_token_for('admin-boundary')}"},
        )
        assert response.status_code == 200

    def test_admin_router_denies_medical_director(self, rbac_client: TestClient) -> None:
        """Explicit spec callout: /admin/* must 403 for medical_director."""
        _seed_user("md-below-admin", role="medical_director")
        response = rbac_client.get(
            "/api/v1/admin/config",
            headers={"Authorization": f"Bearer {_token_for('md-below-admin')}"},
        )
        assert response.status_code == 403

    def test_review_queue_allows_medical_director(self, rbac_client: TestClient) -> None:
        _seed_user("md-boundary", role="medical_director")
        response = rbac_client.get(
            "/api/v1/authorizations/review-queue",
            headers={"Authorization": f"Bearer {_token_for('md-boundary')}"},
        )
        assert response.status_code == 200

    def test_review_queue_denies_clinician(self, rbac_client: TestClient) -> None:
        """Passes the router-wide clinician floor but fails the per-endpoint
        medical_director floor — proves the two guards stack (§2 note)."""
        _seed_user("clinician-below-md", role="clinician")
        response = rbac_client.get(
            "/api/v1/authorizations/review-queue",
            headers={"Authorization": f"Bearer {_token_for('clinician-below-md')}"},
        )
        assert response.status_code == 403

    def test_feedback_allows_medical_director(self, rbac_client: TestClient) -> None:
        _seed_user("md-feedback", role="medical_director")
        response = rbac_client.post(
            "/api/v1/authorizations/feedback",
            headers={"Authorization": f"Bearer {_token_for('md-feedback')}"},
            json={
                "case_summary": "RBAC endpoint-matrix test case",
                "decision": "AUTO_APPROVED",
                "rationale": "RBAC boundary test — not a clinical assertion",
            },
        )
        assert response.status_code == 200

    def test_feedback_denies_clinician(self, rbac_client: TestClient) -> None:
        _seed_user("clinician-feedback", role="clinician")
        response = rbac_client.post(
            "/api/v1/authorizations/feedback",
            headers={"Authorization": f"Bearer {_token_for('clinician-feedback')}"},
            json={
                "case_summary": "RBAC endpoint-matrix test case",
                "decision": "AUTO_APPROVED",
                "rationale": "RBAC boundary test — not a clinical assertion",
            },
        )
        assert response.status_code == 403

    def test_sme_authoring_allows_medical_director(self, rbac_client: TestClient) -> None:
        _seed_user("md-sme", role="medical_director")
        response = rbac_client.get(
            "/api/v1/sme-authoring/status",
            headers={"Authorization": f"Bearer {_token_for('md-sme')}"},
        )
        assert response.status_code == 200

    def test_sme_authoring_denies_clinician(self, rbac_client: TestClient) -> None:
        _seed_user("clinician-sme", role="clinician")
        response = rbac_client.get(
            "/api/v1/sme-authoring/status",
            headers={"Authorization": f"Bearer {_token_for('clinician-sme')}"},
        )
        assert response.status_code == 403

    def test_authorizations_router_allows_clinician_boundary(self, rbac_client: TestClient) -> None:
        """clinician is the LOWEST rank, so it is the boundary (allowed) case
        for the router-wide /authorizations/* floor — there is no rank below
        it to deny (see the paired test below for this router's fail-closed
        floor instead: an invalid/unparseable role)."""
        _seed_user("clinician-submit", role="clinician")

        from pacca.models.authorization import AuthorizationDecision
        from pacca.models.enums import AuthorizationStatus, ReviewTier

        decision = AuthorizationDecision(
            status=AuthorizationStatus.AUTO_APPROVED,
            confidence_score=0.99,
            rationale="RBAC boundary test — not a clinical assertion",
            review_tier_used=ReviewTier.AUTOMATED,
        )
        with (
            patch(
                "pacca.api.routes.authorizations.orchestrator.process_decision",
                new_callable=AsyncMock,
                return_value=decision,
            ),
            patch(
                "pacca.api.routes.authorizations.rag_engine.query",
                # chg-19 changed query() from `str` to RetrievalOutcome. This mock was
                # authored on a parallel branch that predated that contract, so the two
                # only collided at merge — neither lane's suite could see it alone.
                # Non-degraded: this test asserts an RBAC boundary, not RAG behaviour,
                # so it must take the healthy path.
                return_value=RetrievalOutcome(
                    text="mock guideline content",
                    mode="pipeline",
                    degraded=False,
                    reason=None,
                ),
            ),
        ):
            response = rbac_client.post(
                "/api/v1/authorizations/",
                headers={"Authorization": f"Bearer {_token_for('clinician-submit')}"},
                json=_minimal_authorization_payload("RBAC-MATRIX-1"),
            )
        assert response.status_code == 200

    def test_authorizations_router_denies_unparseable_role(self, rbac_client: TestClient) -> None:
        """clinician has no rank below it, so THIS router's fail-closed floor
        is an invalid/unparseable role, not a lower-ranked one."""
        _seed_user("bad-role-submit", role="not_a_real_role")
        response = rbac_client.post(
            "/api/v1/authorizations/",
            headers={"Authorization": f"Bearer {_token_for('bad-role-submit')}"},
            json=_minimal_authorization_payload("RBAC-MATRIX-2"),
        )
        assert response.status_code == 403


# =============================================================================
# 5.5 Promotion endpoint
# =============================================================================


class TestPromotionEndpoint:
    def test_non_admin_gets_403(self, rbac_client: TestClient) -> None:
        _seed_user("promoter-md", role="medical_director")
        _seed_user("promotion-target-1", role="clinician")
        response = rbac_client.patch(
            "/api/v1/admin/users/promotion-target-1/role",
            headers={"Authorization": f"Bearer {_token_for('promoter-md')}"},
            json={"role": "medical_director"},
        )
        assert response.status_code == 403

    def test_admin_gets_200_and_role_actually_changes(self, rbac_client: TestClient) -> None:
        _seed_user("promoter-admin-1", role="admin")
        _seed_user("promotion-target-2", role="clinician")
        response = rbac_client.patch(
            "/api/v1/admin/users/promotion-target-2/role",
            headers={"Authorization": f"Bearer {_token_for('promoter-admin-1')}"},
            json={"role": "medical_director"},
        )
        assert response.status_code == 200
        assert _read_role("promotion-target-2") == "medical_director"

    def test_invalid_role_value_returns_422(self, rbac_client: TestClient) -> None:
        _seed_user("promoter-admin-2", role="admin")
        _seed_user("promotion-target-3", role="clinician")
        response = rbac_client.patch(
            "/api/v1/admin/users/promotion-target-3/role",
            headers={"Authorization": f"Bearer {_token_for('promoter-admin-2')}"},
            json={"role": "supreme-overlord"},
        )
        assert response.status_code == 422
        assert _read_role("promotion-target-3") == "clinician"  # unchanged

    def test_unknown_username_returns_404(self, rbac_client: TestClient) -> None:
        _seed_user("promoter-admin-3", role="admin")
        response = rbac_client.patch(
            "/api/v1/admin/users/does-not-exist/role",
            headers={"Authorization": f"Bearer {_token_for('promoter-admin-3')}"},
            json={"role": "admin"},
        )
        assert response.status_code == 404

    def test_demoting_last_admin_returns_409_and_role_is_unchanged(
        self, rbac_client: TestClient
    ) -> None:
        """The only admin in the DB tries to demote itself — refused, DB unchanged."""
        _seed_user("last-admin", role="admin")
        response = rbac_client.patch(
            "/api/v1/admin/users/last-admin/role",
            headers={"Authorization": f"Bearer {_token_for('last-admin')}"},
            json={"role": "clinician"},
        )
        assert response.status_code == 409
        assert _read_role("last-admin") == "admin"  # unchanged

    def test_demoting_one_of_several_admins_succeeds(self, rbac_client: TestClient) -> None:
        """The last-admin guard only fires when it's the ONLY admin left."""
        _seed_user("admin-a", role="admin")
        _seed_user("admin-b", role="admin")
        response = rbac_client.patch(
            "/api/v1/admin/users/admin-b/role",
            headers={"Authorization": f"Bearer {_token_for('admin-a')}"},
            json={"role": "clinician"},
        )
        assert response.status_code == 200
        assert _read_role("admin-b") == "clinician"

    def test_successful_change_writes_one_audit_record_with_acting_admin_as_actor(
        self, rbac_client: TestClient
    ) -> None:
        _seed_user("auditing-admin", role="admin")
        _seed_user("audit-target", role="clinician")
        response = rbac_client.patch(
            "/api/v1/admin/users/audit-target/role",
            headers={"Authorization": f"Bearer {_token_for('auditing-admin')}"},
            json={"role": "medical_director"},
        )
        assert response.status_code == 200

        sync_url = os.environ["DATABASE_URL"].replace("+aiosqlite", "")
        engine = create_engine(sync_url)
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    text("SELECT actor, details FROM audit_logs WHERE action = 'user.role_changed'")
                ).fetchall()
        finally:
            engine.dispose()

        assert len(rows) == 1
        assert rows[0].actor == "auditing-admin"


# =============================================================================
# 5.6 Migration
# =============================================================================


class TestMigrationRoundTrip:
    def test_upgrade_downgrade_upgrade_round_trips_cleanly(self, tmp_path: Path) -> None:
        """Item 17: alembic upgrade head -> downgrade -1 -> upgrade head, all exit 0."""
        db_path = tmp_path / "migration_roundtrip.db"
        env = _alembic_env(f"sqlite+aiosqlite:///{db_path}")

        up1 = _run_alembic("upgrade", "head", env=env)
        assert up1.returncode == 0, up1.stderr
        down = _run_alembic("downgrade", "-1", env=env)
        assert down.returncode == 0, down.stderr
        up2 = _run_alembic("upgrade", "head", env=env)
        assert up2.returncode == 0, up2.stderr

    def test_pre_existing_row_backfills_to_clinician(self, tmp_path: Path) -> None:
        """Item 18: a `users` row inserted BEFORE migration 006 has role
        'clinician' after it — the server_default backfills existing rows
        rather than leaving them NULL (which would fail-closed-lock every
        existing account out; see the migration's own docstring)."""
        db_path = tmp_path / "migration_backfill.db"
        env = _alembic_env(f"sqlite+aiosqlite:///{db_path}")

        pre = _run_alembic("upgrade", "005_reconcile_index_drift", env=env)
        assert pre.returncode == 0, pre.stderr

        sync_engine = create_engine(f"sqlite:///{db_path}")
        try:
            with sync_engine.begin() as conn:
                conn.execute(
                    text("INSERT INTO users (username, hashed_password) VALUES (:u, :p)"),
                    {"u": "pre-migration-user", "p": "some-hash"},
                )
        finally:
            sync_engine.dispose()

        head = _run_alembic("upgrade", "head", env=env)
        assert head.returncode == 0, head.stderr

        sync_engine = create_engine(f"sqlite:///{db_path}")
        try:
            with sync_engine.connect() as conn:
                row = conn.execute(
                    text("SELECT role FROM users WHERE username = :u"),
                    {"u": "pre-migration-user"},
                ).first()
        finally:
            sync_engine.dispose()

        assert row is not None
        assert row.role == "clinician"


# =============================================================================
# 5.7 CLI
# =============================================================================


@pytest.fixture
def cli_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """An isolated on-disk DB for `pacca create-admin`.

    Builds both Bases, not just `users`: create-admin now writes a
    `user.role_changed` audit record (a Validator finding — the CLI's
    privilege grant used to leave no audit trail), which needs the
    `audit_logs` table (on DomainBase) to exist.
    """
    db_path = tmp_path / "cli_test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    get_settings.cache_clear()
    db_session_module._engine = None
    db_session_module._session_factory = None

    sync_engine = create_engine(f"sqlite:///{db_path}")
    try:
        AuthBase.metadata.create_all(sync_engine)
        DomainBase.metadata.create_all(sync_engine)
    finally:
        sync_engine.dispose()

    yield db_path

    db_session_module._engine = None
    db_session_module._session_factory = None
    get_settings.cache_clear()


class TestCreateAdminCLI:
    def test_creates_new_admin(self, cli_db: Path) -> None:
        """Item 19: create-admin creates a new admin."""
        runner = CliRunner()
        result = runner.invoke(
            pacca_cli,
            ["create-admin", "--username", "brand-new-admin"],
            input="s3cr3t-pw-123\ns3cr3t-pw-123\n",
        )
        assert result.exit_code == 0, result.output
        assert "Created new admin user 'brand-new-admin'" in result.output
        assert _role_in_db(cli_db, "brand-new-admin") == "admin"

    def test_promotes_existing_user(self, cli_db: Path) -> None:
        """Item 20: create-admin on an existing user promotes them.

        Also covers the Validator's FIX 2 finding: promotion must NOT touch
        the existing user's credential (a fuller adversarial version of
        this lives in test_rbac_adversarial.py's
        test_finding_cli_create_admin_no_longer_resets_an_existing_users_password).
        """
        sync_engine = create_engine(f"sqlite:///{cli_db}")
        try:
            with sync_engine.begin() as conn:
                conn.execute(
                    user_module.User.__table__.insert().values(
                        username="existing-clinician",
                        hashed_password=get_password_hash("old-pw"),
                        role="clinician",
                    )
                )
        finally:
            sync_engine.dispose()

        runner = CliRunner()
        result = runner.invoke(
            pacca_cli,
            ["create-admin", "--username", "existing-clinician"],
            input="new-pw-123\nnew-pw-123\n",
        )
        assert result.exit_code == 0, result.output
        assert "Promoted existing user 'existing-clinician' to admin" in result.output
        assert "Password NOT changed" in result.output
        assert _role_in_db(cli_db, "existing-clinician") == "admin"

        sync_engine = create_engine(f"sqlite:///{cli_db}")
        try:
            with sync_engine.connect() as conn:
                row = conn.execute(
                    user_module.User.__table__.select().where(
                        user_module.User.__table__.c.username == "existing-clinician"
                    )
                ).first()
        finally:
            sync_engine.dispose()
        assert row is not None
        assert verify_password("old-pw", row.hashed_password) is True, (
            "promotion must not rotate the existing user's password"
        )
        assert verify_password("new-pw-123", row.hashed_password) is False

    def test_running_twice_is_not_an_error(self, cli_db: Path) -> None:
        """Idempotency, explicitly called out in the design spec."""
        runner = CliRunner()
        first = runner.invoke(
            pacca_cli,
            ["create-admin", "--username", "repeat-admin"],
            input="pw-one-123\npw-one-123\n",
        )
        second = runner.invoke(
            pacca_cli,
            ["create-admin", "--username", "repeat-admin"],
            input="pw-two-456\npw-two-456\n",
        )
        assert first.exit_code == 0, first.output
        assert second.exit_code == 0, second.output
        assert _role_in_db(cli_db, "repeat-admin") == "admin"

    def test_password_never_appears_in_stdout(self, cli_db: Path) -> None:
        """Item 21: the password never appears in stdout."""
        secret = "sUpEr-SeCrEt-Pw-99"
        runner = CliRunner()
        result = runner.invoke(
            pacca_cli,
            ["create-admin", "--username", "silent-admin"],
            input=f"{secret}\n{secret}\n",
        )
        assert result.exit_code == 0, result.output
        assert secret not in result.output


def _role_in_db(db_path: Path, username: str) -> str | None:
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT role FROM users WHERE username = :u"), {"u": username}
            ).first()
    finally:
        engine.dispose()
    return None if row is None else row.role
