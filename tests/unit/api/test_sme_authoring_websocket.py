"""
Tests for src/pacca/api/websockets/draft_stream.py.

Uses FastAPI TestClient's WebSocket support. Mocks the LLM agent so
tests are hermetic + fast.

Covers:
- Auth-first-message protocol
- Session-not-found close (4404)
- Successful draft round-trip (`done` event)
- LLM failure (`error` event + close)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from jose import jwt
from starlette.testclient import WebSocketTestSession  # noqa: F401 — type hint
from starlette.websockets import WebSocketDisconnect

from pacca.agents.sme_authoring.models import CaseDraftResponse
from pacca.api.auth import ALGORITHM, SECRET_KEY

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi.testclient import TestClient


def _make_token(sub: str = "test-user") -> str:
    """Build a valid JWT for the auth-first-message."""
    payload = {
        "sub": sub,
        "exp": datetime.now(UTC) + timedelta(minutes=5),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _valid_draft() -> CaseDraftResponse:
    return CaseDraftResponse(
        case_id="GC-101",
        title="Mocked draft from WebSocket test fixture for assertion purposes",
        diagnosis_code="C34.1",
        diagnosis_description="Malignant neoplasm of lung",
        procedure_code="J9271",
        procedure_description="Pembrolizumab injection",
        clinical_notes=(
            "65-year-old male with stage IV NSCLC, PD-L1 70%, no EGFR/ALK. "
            "Oncology recommending first-line pembrolizumab per NCCN."
        ),
        guidelines_context=(
            "NCCN NSCLC Guidelines: pembrolizumab monotherapy is Category 1 "
            "first-line for metastatic NSCLC with PD-L1 >= 50%."
        ),
        expected_outcome="AUTO_APPROVED",
        expected_branch="BRANCH_1_AUTO_APPROVE",
        reasoning_must_include=["NCCN", "PD-L1"],
        clinical_rationale=("Metastatic NSCLC with high PD-L1, NCCN Category 1. Clean approve."),
        judge_scoring_criteria=("Score highly if rationale cites PD-L1 + NCCN Category 1."),
    )


@pytest.fixture
def session_id(client: TestClient, auth_headers, tmp_session_dir) -> str:
    """Create a session via the REST API + return its ID."""
    resp = client.post(
        "/api/v1/sme-authoring/sessions",
        headers=auth_headers,
        json={
            "scenario": {
                "description": (
                    "65yo male with stage IV NSCLC requesting first-line pembrolizumab per NCCN."
                ),
            },
            "mode": "sandbox",
        },
    )
    return resp.json()["session"]["session_id"]


# =============================================================================
# Auth protocol
# =============================================================================


class TestAuthProtocol:
    def test_missing_first_message_closes_4401(
        self,
        client: TestClient,
        session_id: str,
    ) -> None:
        url = f"/api/v1/sme-authoring/sessions/{session_id}/draft-stream"
        # Connect + immediately close without sending auth
        with client.websocket_connect(url) as ws:
            # Server should emit an error event then close
            event = ws.receive_json()
            assert event["type"] == "error"
            assert "auth" in event["message"].lower()
            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_json()
            assert exc_info.value.code == 4401

    def test_invalid_token_closes_4401(
        self,
        client: TestClient,
        session_id: str,
    ) -> None:
        url = f"/api/v1/sme-authoring/sessions/{session_id}/draft-stream"
        with client.websocket_connect(url) as ws:
            ws.send_json({"type": "auth", "token": "not-a-real-jwt"})
            event = ws.receive_json()
            assert event["type"] == "error"
            assert "invalid" in event["message"].lower() or "jwt" in event["message"].lower()
            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_json()
            assert exc_info.value.code == 4401


# =============================================================================
# RBAC role gate (design spec §5.4 item 12) — clinician token below the
# medical_director floor closes 4403; distinguishable from the 4401s above.
# =============================================================================


class TestRoleGate:
    def test_clinician_token_closes_4403(
        self,
        client: TestClient,
        session_id: str,
        seed_user: Callable[[str, str], None],
    ) -> None:
        """A valid, authenticated token below medical_director is 4403, not 4401."""
        seed_user("ws-clinician", "clinician")
        url = f"/api/v1/sme-authoring/sessions/{session_id}/draft-stream"
        with client.websocket_connect(url) as ws:
            ws.send_json({"type": "auth", "token": _make_token(sub="ws-clinician")})
            event = ws.receive_json()
            assert event["type"] == "error"
            assert "insufficient role" in event["message"].lower()
            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_json()
            assert exc_info.value.code == 4403

    def test_medical_director_token_is_admitted(
        self,
        client: TestClient,
        session_id: str,
        seed_user: Callable[[str, str], None],
    ) -> None:
        """The boundary role (medical_director exactly) passes the gate."""
        seed_user("ws-medical-director", "medical_director")
        url = f"/api/v1/sme-authoring/sessions/{session_id}/draft-stream"

        async def _mock_run(self, request):
            return _valid_draft().model_copy(update={"case_id": request.allocated_case_id})

        with (
            patch(
                "pacca.api.websockets.draft_stream.SMECaseAuthoringAgent.run",
                new=_mock_run,
            ),
            patch(
                "pacca.api.websockets.draft_stream.next_id",
                return_value="GC-201",
            ),
            client.websocket_connect(url) as ws,
        ):
            ws.send_json({"type": "auth", "token": _make_token(sub="ws-medical-director")})
            event = ws.receive_json()
            # Getting to a `done`/heartbeat event at all (not an "error" event
            # for insufficient role) proves the gate admitted this role.
            assert event["type"] == "done"

    def test_nonexistent_account_closes_4401_not_4403(
        self,
        client: TestClient,
        session_id: str,
    ) -> None:
        """A syntactically valid token for a deleted/never-existed account is
        401-shaped (4401), not 403-shaped — there is no account to authorize,
        so this is an authentication failure, not an authorization one."""
        url = f"/api/v1/sme-authoring/sessions/{session_id}/draft-stream"
        with client.websocket_connect(url) as ws:
            ws.send_json({"type": "auth", "token": _make_token(sub="never-registered")})
            event = ws.receive_json()
            assert event["type"] == "error"
            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_json()
            assert exc_info.value.code == 4401


# =============================================================================
# Session-not-found
# =============================================================================


class TestSessionLookup:
    def test_nonexistent_session_closes_4404(
        self,
        client: TestClient,
        tmp_session_dir,
    ) -> None:
        url = "/api/v1/sme-authoring/sessions/does-not-exist/draft-stream"
        with client.websocket_connect(url) as ws:
            ws.send_json({"type": "auth", "token": _make_token()})
            event = ws.receive_json()
            assert event["type"] == "error"
            assert "not found" in event["message"].lower()


# =============================================================================
# Successful draft round-trip
# =============================================================================


class TestSuccessfulDraft:
    def test_draft_emits_done_event(
        self,
        client: TestClient,
        session_id: str,
    ) -> None:
        url = f"/api/v1/sme-authoring/sessions/{session_id}/draft-stream"

        async def _mock_run(self, request):
            return _valid_draft().model_copy(update={"case_id": request.allocated_case_id})

        with (
            patch(
                "pacca.api.websockets.draft_stream.SMECaseAuthoringAgent.run",
                new=_mock_run,
            ),
            patch(
                "pacca.api.websockets.draft_stream.next_id",
                return_value="GC-200",
            ),
            client.websocket_connect(url) as ws,
        ):
            ws.send_json({"type": "auth", "token": _make_token()})
            event = ws.receive_json()
            assert event["type"] == "done"
            assert event["allocated_case_id"] == "GC-200"
            assert event["draft"]["case_id"] == "GC-200"


# =============================================================================
# LLM failure path
# =============================================================================


class TestLLMFailure:
    def test_llm_error_emits_error_event(
        self,
        client: TestClient,
        session_id: str,
    ) -> None:
        url = f"/api/v1/sme-authoring/sessions/{session_id}/draft-stream"

        async def _failing_run(self, request):
            raise RuntimeError("simulated LLM API failure")

        with (
            patch(
                "pacca.api.websockets.draft_stream.SMECaseAuthoringAgent.run",
                new=_failing_run,
            ),
            patch(
                "pacca.api.websockets.draft_stream.next_id",
                return_value="GC-300",
            ),
            client.websocket_connect(url) as ws,
        ):
            ws.send_json({"type": "auth", "token": _make_token()})
            event = ws.receive_json()
            assert event["type"] == "error"
            assert "simulated LLM API failure" in event["message"]
            assert event["recoverable"] is True
