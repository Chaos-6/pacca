"""
Shared fixtures for API-layer unit tests.

- `client` — FastAPI TestClient with the full app; auth is NOT bypassed via a
  dependency override. RBAC (`pacca.api.rbac`) makes the `users` table the
  source of truth for role, so this fixture also builds an isolated, per-test
  SQLite `users` table and seeds an `admin`-role row for TEST_USERNAME —
  every route guarded by `require_min_role`/`get_current_user` now does a
  real DB lookup, not just a JWT signature check.
- `auth_headers` — JWT Authorization header for TEST_USERNAME, matching the
  row `client` seeds.
- `seed_user` — factory to insert an ADDITIONAL user (any role) into the same
  isolated DB `client` set up, for tests that need a role below admin.
- `tmp_session_dir` — isolated session storage for SME-authoring tests.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import create_engine

import pacca.api.models.user as user_module
import pacca.db.session as db_session_module
from pacca.api.auth import ALGORITHM, SECRET_KEY, get_password_hash
from pacca.api.database import Base as AuthBase
from pacca.api.main import app
from pacca.config.settings import get_settings

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

# Matches the identity `auth_headers` signs a token for. Kept as one constant
# so the two fixtures can never drift apart.
TEST_USERNAME = "test-user"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """
    TestClient with the full app, pointed at an isolated per-test SQLite DB.

    Before RBAC, SME-authoring routes needed only `Depends(verify_token)` — a
    pure JWT signature/claims check, no database access. RBAC's
    `require_min_role` resolves `get_current_user`, which does a real `users`
    table lookup, so a test JWT alone is no longer sufficient: the account
    has to exist, with a role that meets the endpoint's floor. This fixture
    builds just the `users` table (not the full domain schema — these tests
    don't touch authorization_requests/audit_logs) on a throwaway file per
    test and seeds an `admin` row for TEST_USERNAME, which is the superset
    role for every endpoint exercised in this package (clinician <
    medical_director < admin).

    The real dev `./pacca.db` is never touched: DATABASE_URL is monkeypatched
    to a `tmp_path` file, and the module-level engine/session-factory
    singletons in `pacca.db.session` are reset so the new URL actually takes
    effect (they are lazily created once and cached for the process
    otherwise).
    """
    db_path = tmp_path / "rbac_test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    get_settings.cache_clear()
    db_session_module._engine = None
    db_session_module._session_factory = None

    sync_engine = create_engine(f"sqlite:///{db_path}")
    try:
        AuthBase.metadata.create_all(sync_engine)
        with sync_engine.begin() as conn:
            conn.execute(
                user_module.User.__table__.insert().values(
                    username=TEST_USERNAME,
                    hashed_password=get_password_hash("test-password"),
                    role="admin",
                )
            )
    finally:
        sync_engine.dispose()

    yield TestClient(app)

    db_session_module._engine = None
    db_session_module._session_factory = None
    get_settings.cache_clear()


@pytest.fixture
def auth_headers() -> dict[str, str]:
    """Valid JWT Authorization headers for TEST_USERNAME (see `client` fixture)."""
    expires = datetime.now(UTC) + timedelta(minutes=30)
    payload = {"sub": TEST_USERNAME, "exp": expires}
    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def seed_user(client: TestClient) -> Callable[[str, str], None]:
    """
    Factory: insert an additional `users` row (any role) into the SAME DB
    the `client` fixture just built.

    Depends on `client` explicitly (even though it never uses the object) so
    fixture setup order is guaranteed — the DATABASE_URL env var `client`
    monkeypatches must already be in place before this reads it. Reads the
    URL from the environment rather than being handed a path directly so it
    always targets whichever DB is currently active, never the real dev
    `./pacca.db`.
    """

    def _seed(username: str, role: str, password: str = "test-password") -> None:
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

    return _seed


@pytest.fixture
def tmp_session_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    Redirect SME-authoring session storage to a tmp dir.

    Patches DEFAULT_SESSION_DIR + session_path so the test doesn't touch
    ~/.pacca/sme_authoring_sessions/.
    """
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setattr("pacca.agents.sme_authoring.session.DEFAULT_SESSION_DIR", session_dir)
    return session_dir
