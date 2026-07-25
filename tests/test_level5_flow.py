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
"""

import os
import shutil
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from pacca.api.auth import ALGORITHM, SECRET_KEY
from pacca.api.main import app

# Import the module where the global 'rag_engine' lives so we can overwrite it
from pacca.api.routes import authorizations
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


@pytest.fixture(scope="module")
def client():
    """
    Context-managed so the app lifespan runs. A bare `TestClient(app)` never
    enters it, so `init_database()` never ran and the first audit write failed
    with "no such table: audit_logs".
    """
    with TestClient(app) as test_client:
        yield test_client


def auth_headers() -> dict[str, str]:
    """
    Both routers under test are mounted with `dependencies=[Depends(verify_token)]`,
    so every request here needs a Bearer token. Mirrors tests/unit/api/conftest.py.
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
            "primary_diagnosis_code": "Lung",
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


def test_learning_loop_spine(client):
    """Level 4 Test: Fail -> Teach -> Succeed"""
    # 1. Submit WEAK Case (Should Fail/Review)
    case_payload = {
        "request_id": f"test_spine_{RUN}",
        "patient_id": "p2",
        "provider_npi": "123",
        "clinical_case": {
            "patient_id": "p2",
            "primary_diagnosis_code": "BackPain",
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

    resp1 = client.post("/api/v1/authorizations/", json=case_payload, headers=auth_headers())
    assert resp1.json()["status"] == "IN_REVIEW"

    # 2. Teach the System (Override)
    feedback = {
        "case_summary": "MRI Spine requested for 2 weeks pain.",
        "decision": "AUTO_APPROVED",
        "rationale": "Override: Patient actually had severe motor weakness not documented in initial NLP.",
    }
    client.post("/api/v1/authorizations/feedback", json=feedback, headers=auth_headers())

    # 3. Submit SAME Case Again (Should Pass via Memory).
    # Same clinical content, new request id — the id is a submission identifier,
    # not part of the case.
    resp2 = client.post(
        "/api/v1/authorizations/",
        json={**case_payload, "request_id": f"test_spine_{RUN}_retry"},
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
