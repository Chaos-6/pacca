"""
Level 3-5 end-to-end flow: rule-based approval, the learning loop, policy evolution.

Marked `clinical` because every test here POSTs to the real submit/admin routes,
which call Claude. `-m "not clinical"` (the deterministic suite and CI) deselects
the module; it runs alongside the other billable gates.

This file previously subclassed GuidelineRetriever to redirect its ChromaDB path,
reimplementing __init__ with public attribute names (`self.guidelines`,
`self.client`) that the real class never had. Every method it inherited then
raised AttributeError on `self._guidelines`. The subclass is gone: the retriever
takes `db_path` directly, so the test uses the supported seam.

The diagnosis codes below are real ICD-10 codes (Z12.2 screening for respiratory
malignancy, M54.50 low back pain) and must stay that way. They were previously
free-text placeholders ("Lung", "BackPain"), which the pre-flight shape check
correctly escalates as malformed: a value that is not a well-formed code has not
been screened by any of the curated-list branches, whatever those branches
returned. Substituting a placeholder here does not simplify the fixture, it
changes which decision path the case takes.
"""

import os
import shutil
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import create_engine, inspect, text

# Registers User on AuthBase's metadata — never referenced directly, but the
# import's side effect (populating AuthBase.metadata.tables) is required
# before create_all(AuthBase.metadata) below, or `users` silently isn't built.
import pacca.api.models.user  # noqa: F401

# Imported as a module (not `from ... import`) so the fixture can reset the
# process-global engine/session-factory singletons after repointing DATABASE_URL.
import pacca.db.session as db_session_module
from pacca.api.auth import ALGORITHM, SECRET_KEY
from pacca.api.database import Base as AuthBase
from pacca.api.main import app

# Import the module where the global 'rag_engine' lives so we can overwrite it
from pacca.api.routes import authorizations
from pacca.config.settings import get_settings
from pacca.db.models import Base as DomainBase
from pacca.integrations.vector_store import GuidelineRetriever

pytestmark = pytest.mark.clinical

# We create a specific test database path
TEST_DB_PATH = os.path.join(os.getcwd(), "test_pacca_db")

# authorization_requests.request_id is UNIQUE and the SQLite file outlives the
# run, so fixed ids ("test_1") collide on the second execution. The learning-loop
# test also submits the *same clinical case* twice, which is the point — only the
# request id has to differ.
RUN = uuid.uuid4().hex[:8]


@pytest.fixture(scope="module")
def test_rag():
    """A single RAG engine for the module, pointed at a throwaway directory."""
    if os.path.exists(TEST_DB_PATH):
        shutil.rmtree(TEST_DB_PATH)

    rag = GuidelineRetriever(db_path=TEST_DB_PATH)

    # Seed it immediately
    rag.add_guideline(
        """CRITERIA FOR LUNG CANCER SCREENING (71250):
           Age 50-80 AND 20 pack-year history.""",
        "NCCN-LUNG-001",
        {"specialty": "Oncology"},
    )
    rag.add_guideline(
        """CRITERIA FOR MRI LUMBAR SPINE (72148):
           Indicated only after 6 weeks of conservative therapy fails.""",
        "CMS-SPINE-002",
        {"specialty": "Orthopedics"},
    )

    yield rag

    # 3. Teardown
    if os.path.exists(TEST_DB_PATH):
        shutil.rmtree(TEST_DB_PATH)


@pytest.fixture(autouse=True)
def inject_rag(test_rag, monkeypatch):
    """
    Point the route's module-level rag_engine at the throwaway store.

    Precedents are emptied between tests so the learning-loop test cannot be
    satisfied by a precedent an earlier test wrote. Rows are deleted rather than
    the collection dropped: dropping it would leave the retriever (and the
    pipeline built over it) holding a handle to a deleted collection.
    """
    monkeypatch.setattr(authorizations, "rag_engine", test_rag)

    existing = test_rag._precedents.get(include=[])["ids"]
    if existing:
        test_rag._precedents.delete(ids=existing)


def _ensure_users_role_column(sync_engine) -> None:
    """
    Repair a `users` table that predates migration 006 (RBAC's `role` column).

    `create_all(checkfirst=True)` — used by the `client` fixture below to
    build schema — CANNOT add a column to an ALREADY-EXISTING `users` table:
    checkfirst only skips tables that already exist wholesale, it never
    diffs columns against the current model. Left unrepaired against such a
    table, the very next `SELECT ... FROM users` (which implicitly selects
    every column) fails with "no such column: users.role" — a confusing
    crash, not a graceful skip.

    STATUS: defence in depth, not the live path. The `client` fixture now
    builds a fresh per-run temp database, where `create_all` always creates
    `role` and this function is a verified no-op. It was written when that
    fixture ran against a developer's persistent `./pacca.db` (built long
    before RBAC, never migrated — this repo's own dev DB has no
    `alembic_version` table at all, confirmed by inspection). It is kept
    because the fixture is one edit away from being repointed at a
    persistent database, and because a repair that is wrong is worse than
    a repair that is unused: the DDL below matches migration 006 exactly
    (String(30) NOT NULL DEFAULT 'clinician'), verified column-for-column
    against a migration-built table.

    This repairs exactly that column gap, matching migration 006's own DDL
    exactly (String(30) NOT NULL DEFAULT 'clinician'). Deliberately NOT
    wrapped in a try/except that swallows the failure and skips the caller:
    a schema this function cannot repair must still be a loud, actionable
    error, never a quietly-skipped anti-hallucination gate (GC-018 / GC-019
    run in this module). Idempotent — a no-op when `role` is already present
    (the fresh-DB case, where `create_all` already built it).

    Covered by a DETERMINISTIC (non-`clinical`) regression test —
    tests/unit/test_level5_flow_fixture_repair.py — against both a
    pre-006-shaped table and a fresh one, since this whole module is
    `clinical`-marked and skipped by `make test`.
    """
    existing_columns = {col["name"] for col in inspect(sync_engine).get_columns("users")}
    if "role" not in existing_columns:
        with sync_engine.begin() as conn:
            conn.execute(
                text("ALTER TABLE users ADD COLUMN role VARCHAR(30) NOT NULL DEFAULT 'clinician'")
            )


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory):
    """
    Context-managed so the app lifespan runs. A bare `TestClient(app)` never
    enters it, so `init_database()` never ran and the first audit write failed
    with "no such table: audit_logs".

    EPHEMERAL DATABASE. This fixture used to build schema against whatever
    `settings.database_url` named — in practice a developer's persistent
    `./pacca.db`. Running the clinical gate therefore performed DDL on a real
    database outside Alembic and left a seeded `test-user` row behind, with no
    prompt and no announcement. It now repoints `DATABASE_URL` at a per-run
    temp file, mirroring the `rbac_client` fixture in
    tests/unit/test_rbac.py — one isolation pattern for the whole repo rather
    than two. Consequences that make the repointing actually take effect:
      - `get_settings` is `lru_cache`d, so its cache must be cleared both on
        entry and on exit, or the new URL is ignored / leaks into later modules.
      - `pacca.db.session` memoises `_engine` / `_session_factory` at process
        level; both are reset on entry and exit so the app's own sessions bind
        to the temp DB and later tests are not left holding a disposed engine
        pointed at a deleted file.
    Nothing here writes to `./pacca.db`; `make test-clinical` is now
    side-effect-free with respect to developer state.

    Schema creation (C5): as of the Alembic-single-source-of-truth change,
    `init_database()` in the app lifespan no longer calls `create_all` — it
    only verifies the engine, on the assumption `alembic upgrade head` already
    ran. Nothing in this test module runs Alembic, so the test has to build
    its own schema, on BOTH declarative Bases (`db.models.Base` for the
    domain tables — audit_logs, authorization_requests, decisions — and
    `api.database.Base` for the legacy sync `users` table). Done with a SYNC
    engine (L-025): this fixture is plain `def`, not `async def`, so there is
    no running event loop to hand an async engine to — a sync engine pointed
    at the same sqlite file sidesteps any "attached to a different loop"
    failure.
    """
    # A module-scoped fixture cannot request the function-scoped `monkeypatch`
    # fixture, so drive MonkeyPatch directly and undo it in teardown.
    mp = pytest.MonkeyPatch()
    db_path = tmp_path_factory.mktemp("level5_flow") / "level5_flow.db"
    mp.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    get_settings.cache_clear()
    db_session_module._engine = None
    db_session_module._session_factory = None

    sync_engine = create_engine(f"sqlite:///{db_path}")
    try:
        DomainBase.metadata.create_all(sync_engine)
        AuthBase.metadata.create_all(sync_engine)
        # Defence in depth only: on this fresh temp DB `create_all` always
        # builds `role`, so the repair is a verified no-op here. It stays
        # because the fixture is one edit away from being pointed at a
        # pre-006 database again, and the failure mode it prevents is a
        # confusing "no such column" crash in the anti-hallucination gate.
        _ensure_users_role_column(sync_engine)

        # RBAC (see pacca.api.rbac): the router-wide guards on both routers
        # under test (clinician on /authorizations/*, admin on /admin/*) now
        # do a real `users` table lookup, not just a JWT signature check —
        # and this module's tests exercise /feedback (medical_director floor)
        # and /admin/optimize_policies (admin floor) too. "test-user" (the
        # identity `auth_headers` signs a token for) needs the highest rank
        # to clear every floor exercised here. A plain insert is safe now that
        # the database is created fresh per run — the upsert this replaced
        # existed only to survive re-runs against the persistent dev DB.
        users_table = AuthBase.metadata.tables["users"]
        with sync_engine.begin() as conn:
            conn.execute(
                users_table.insert().values(
                    username="test-user",
                    hashed_password="unused-hash-for-clinical-test-fixture",
                    role="admin",
                )
            )
    finally:
        sync_engine.dispose()

    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        db_session_module._engine = None
        db_session_module._session_factory = None
        mp.undo()
        get_settings.cache_clear()


def auth_headers() -> dict[str, str]:
    """
    Both routers under test are mounted with a `require_min_role` router-wide
    dependency (RBAC — see pacca.api.rbac), so every request here needs a
    Bearer token for an account the `client` fixture seeds as `admin` (the
    superset role for every floor this module's tests exercise). Mirrors
    tests/unit/api/conftest.py.
    """
    payload = {"sub": "test-user", "exp": datetime.now(UTC) + timedelta(minutes=30)}
    return {"Authorization": f"Bearer {jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)}"}


def test_happy_path_lung_cancer(client):
    """Level 3 Test: Auto-Approval based on Rules"""
    payload = {
        "request_id": f"test_1_{RUN}",
        "patient_id": "p1",
        "provider_npi": "123",
        "clinical_case": {
            "patient_id": "p1",
            "primary_diagnosis_code": "Z12.2",
            "procedure_code": "71250",
            "evidence": [
                {
                    "id": "e1",
                    "source_type": "CLINICAL_NOTE",
                    "description": "55yo male, 30 pack year history",
                    "original_text": "...",
                    "confidence": 1.0,
                }
            ],
        },
    }
    response = client.post("/api/v1/authorizations/", json=payload, headers=auth_headers())
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "AUTO_APPROVED"


def _thin_spine_case(request_id: str) -> dict:
    """The weak case: 2 weeks of back pain, no motor-weakness finding documented."""
    return {
        "request_id": request_id,
        "patient_id": "p2",
        "provider_npi": "123",
        "clinical_case": {
            "patient_id": "p2",
            "primary_diagnosis_code": "M54.50",
            "procedure_code": "72148",
            "evidence": [
                {
                    "id": "e2",
                    "source_type": "CLINICAL_NOTE",
                    "description": "Patient has had back pain for 2 weeks. Requesting MRI.",
                    "original_text": "...",
                    "confidence": 1.0,
                }
            ],
        },
    }


def _teach_spine_precedent(client) -> None:
    """Teach the override precedent: MRI approved because of severe motor weakness."""
    feedback = {
        "case_summary": "MRI Spine requested for 2 weeks pain.",
        "decision": "AUTO_APPROVED",
        "rationale": "Override: Patient actually had severe motor weakness not documented in initial NLP.",
    }
    client.post("/api/v1/authorizations/feedback", json=feedback, headers=auth_headers())


def test_precedent_does_not_override_absent_evidence(client):
    """
    Level 4 Test (guard-holds): a taught precedent is weighed, not blindly applied.

    The precedent's justification for auto-approval is "severe motor weakness" —
    a clinical finding that is NOT present in this case's evidence. The P-5
    evidence-grounding safety invariant (same family as GC-018/019) means the
    DecisionAgent must not manufacture that finding just because a similar-looking
    precedent says to approve. Resubmitting the identical thin case after teaching
    the precedent must still land in human review.
    """
    case_payload = _thin_spine_case(f"test_spine_guard_{RUN}")

    # 1. Submit the thin case — no motor weakness documented → review.
    resp1 = client.post("/api/v1/authorizations/", json=case_payload, headers=auth_headers())
    assert resp1.json()["status"] == "IN_REVIEW"

    # 2. Teach the override precedent (severe motor weakness → AUTO_APPROVED).
    _teach_spine_precedent(client)

    # 3. Resubmit the SAME thin case (new request id, evidence unchanged — still
    #    no motor weakness). The precedent must not override absent evidence.
    resp2 = client.post(
        "/api/v1/authorizations/",
        json={**case_payload, "request_id": f"test_spine_guard_{RUN}_retry"},
        headers=auth_headers(),
    )
    data = resp2.json()
    assert data["status"] == "IN_REVIEW"


def test_learning_loop_spine(client):
    """Level 4 Test: Fail -> Teach -> (evidence now documented) -> Succeed.

    The learning loop only fires auto-approval once the case's OWN evidence
    documents the finding the precedent was granted on (severe motor weakness).
    This is the counterpart to test_precedent_does_not_override_absent_evidence:
    the precedent is followed when its grounding evidence is actually present,
    never when it is absent.
    """
    case_payload = _thin_spine_case(f"test_spine_{RUN}")

    # 1. Submit WEAK Case (Should Fail/Review)
    resp1 = client.post("/api/v1/authorizations/", json=case_payload, headers=auth_headers())
    assert resp1.json()["status"] == "IN_REVIEW"

    # 2. Teach the System (Override)
    _teach_spine_precedent(client)

    # 3. Resubmit with the motor-weakness finding now DOCUMENTED in the case's
    #    own evidence — same request family, new request id, plus a second
    #    evidence item.
    documented_payload = _thin_spine_case(f"test_spine_{RUN}_retry")
    documented_payload["clinical_case"]["evidence"].append(
        {
            "id": "e3",
            "source_type": "CLINICAL_NOTE",
            "description": (
                "Neuro exam: severe progressive motor weakness, 3/5 strength "
                "with foot drop in the left lower extremity."
            ),
            "original_text": "...",
            "confidence": 1.0,
        }
    )

    resp2 = client.post(
        "/api/v1/authorizations/",
        json=documented_payload,
        headers=auth_headers(),
    )
    data = resp2.json()
    assert data["status"] == "AUTO_APPROVED"

    rationale = data["rationale"].lower()
    assert any(
        x in rationale for x in ["override", "previous", "precedent", "past medical director"]
    )


def test_dark_factory_evolution(client):
    """Level 5 Test: Policy Rewriting"""
    # Trigger optimization
    resp = client.post("/api/v1/admin/optimize_policies", headers=auth_headers())
    data = resp.json()

    # The route stores a PENDING proposal and deploys nothing; it has returned
    # "proposal_pending" with a `proposed_text_preview` since iter-6. The old
    # "optimized"/"proposed" branches asserted a contract that no longer exists.
    assert data["status"] == "proposal_pending"
    assert "weakness" in data["proposed_text_preview"].lower()
