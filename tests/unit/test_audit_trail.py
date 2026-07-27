"""
Unit tests for audit trail wiring — Week 1 implementation.

These tests verify that:
  1. Every authorization submission produces at least one audit record
  2. Every successful decision produces a decision audit record
  3. Failures produce failure audit records (not silence)
  4. The feedback/learning endpoint produces a precedent_learned record
  5. All audit records for one request share the same correlation_id

Teaching note — why test audit logging specifically?
  Audit logging is invisible to end users and easy to accidentally break.
  Without tests, a refactor could silently disable audit writes and you
  would not know until a compliance audit revealed missing records.
  These tests make audit behavior a first-class, enforced contract.

Teaching note — what is a mock?
  The AI agents (DecisionAgent, MedicalDirectorAgent) make real HTTP calls
  to the Anthropic API. In tests, we never want to make real API calls —
  they are slow, cost money, and may fail due to network issues.
  A "mock" is a fake object that pretends to be the real thing.
  unittest.mock.AsyncMock creates a fake async function that returns
  whatever you tell it to return, instantly, with no network call.
  This lets us test the audit wiring independently of the AI responses.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pacca.integrations.vector_store import RetrievalOutcome
from pacca.models.authorization import AuthorizationDecision
from pacca.models.clinical import ClinicalCase, EvidenceItem
from pacca.models.enums import AuthorizationStatus, EvidenceSourceType, ReviewTier


def _healthy_outcome(text: str) -> RetrievalOutcome:
    """A non-degraded RetrievalOutcome (chg-19) — the mock fill-in for
    rag_engine.query() in tests that are not exercising RAG degradation."""
    return RetrievalOutcome(text=text, mode="pipeline", degraded=False, reason=None)


# ── Test fixtures — reusable test data ──────────────────────────────────────


@pytest.fixture
def sample_case() -> ClinicalCase:
    """A minimal but valid ClinicalCase for testing."""
    return ClinicalCase(
        patient_id="P-TEST-001",
        primary_diagnosis_code="C34.1",
        procedure_code="J9271",
        evidence=[
            EvidenceItem(
                id="e1",
                source_type=EvidenceSourceType.CLINICAL_NOTE,
                description="Stage IIIA NSCLC, PD-L1 TPS >= 50%",
                original_text="Patient presents with stage IIIA NSCLC.",
                confidence=0.95,
            )
        ],
    )


@pytest.fixture
def sample_request(sample_case) -> dict:
    """A minimal valid request payload for the submit endpoint."""
    return {
        "request_id": "AUTH-TEST-001",
        "patient_id": "P-TEST-001",
        "provider_npi": "1234567890",
        "clinical_case": {
            "patient_id": "P-TEST-001",
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


@pytest.fixture
def mock_auto_approved_decision() -> AuthorizationDecision:
    """A pre-built decision that represents an auto-approved outcome."""
    return AuthorizationDecision(
        decision_id="DEC-TEST-001",
        status=AuthorizationStatus.AUTO_APPROVED,
        confidence_score=0.97,
        rationale="NCCN guidelines support Pembrolizumab for PD-L1 >= 50% NSCLC.",
        review_tier_used=ReviewTier.AUTOMATED,
    )


@pytest.fixture
def mock_in_review_decision() -> AuthorizationDecision:
    """A pre-built decision that represents a human-review-required outcome."""
    return AuthorizationDecision(
        decision_id="DEC-TEST-002",
        status=AuthorizationStatus.IN_REVIEW,
        confidence_score=0.72,
        rationale="Insufficient documentation of prior treatment failure.",
        review_tier_used=ReviewTier.AUTOMATED,
    )


# ── Core audit wiring tests ──────────────────────────────────────────────────


class TestAuditTrailWiring:
    """
    Tests that audit records are written at the correct moments.

    Each test patches (replaces with a mock) the components we are NOT
    testing, so we can focus purely on whether audit.log() gets called.
    """

    @pytest.mark.asyncio
    async def test_submission_writes_audit_record(
        self, sample_request, mock_auto_approved_decision
    ):
        """
        Submitting an authorization must produce at least one audit record.

        This is the most basic HIPAA requirement: every request touching
        PHI must be logged. Even if the AI pipeline fails later, the
        submission itself must be recorded.
        """
        # We will count how many times audit.log() is called
        audit_log_calls = []

        async def capture_log(**kwargs):
            """Fake audit.log() that records what it was called with."""
            audit_log_calls.append(kwargs)
            # Return a minimal fake AuditLogModel
            mock_entry = MagicMock()
            mock_entry.entry_id = f"AUDIT-{len(audit_log_calls)}"
            return mock_entry

        # Patch three things:
        #   1. The Orchestrator's process_decision (avoid real LLM calls)
        #   2. The RAG engine's query (avoid ChromaDB calls)
        #   3. AuditRepository.log (capture calls instead of hitting DB)
        with (
            patch(
                "pacca.api.routes.authorizations.orchestrator.process_decision",
                new_callable=AsyncMock,
                return_value=mock_auto_approved_decision,
            ),
            patch(
                "pacca.api.routes.authorizations.rag_engine.query",
                return_value=_healthy_outcome("Mock guideline content"),
            ),
            patch(
                "pacca.db.repository.AuditRepository.log",
                side_effect=capture_log,
            ),
        ):
            from pacca.api.routes.authorizations import submit_authorization
            from pacca.models.authorization import AuthorizationRequest

            # Build the request model
            req = AuthorizationRequest(**sample_request)

            # Create a fake async session (we are not testing DB writes here,
            # just that audit.log() is called)
            mock_session = AsyncMock()

            # Call the route function directly (no HTTP overhead in unit tests)
            await submit_authorization(request=req, session=mock_session)

        # There should be at least 2 audit records: submission + decision
        assert len(audit_log_calls) >= 2, (
            f"Expected at least 2 audit records, got {len(audit_log_calls)}. "
            "Submission and decision must both be logged."
        )

    @pytest.mark.asyncio
    async def test_submission_audit_has_correct_action(
        self, sample_request, mock_auto_approved_decision
    ):
        """
        The submission record (action='authorization_submitted') is logged
        immediately after the run's intent (P-3 / chg-7 makes 'intent.declared'
        event #0), i.e. it is the SECOND audit record, still BEFORE any AI
        processing. This ensures we can query the audit log by action to find
        all submissions, a common compliance reporting need.
        """
        audit_log_calls = []

        async def capture_log(**kwargs):
            audit_log_calls.append(kwargs)
            return MagicMock()

        with (
            patch(
                "pacca.api.routes.authorizations.orchestrator.process_decision",
                new_callable=AsyncMock,
                return_value=mock_auto_approved_decision,
            ),
            patch(
                "pacca.api.routes.authorizations.rag_engine.query",
                return_value=_healthy_outcome("Mock guideline"),
            ),
            patch(
                "pacca.db.repository.AuditRepository.log",
                side_effect=capture_log,
            ),
        ):
            from pacca.api.routes.authorizations import submit_authorization
            from pacca.models.authorization import AuthorizationRequest

            req = AuthorizationRequest(**sample_request)
            mock_session = AsyncMock()
            await submit_authorization(request=req, session=mock_session)

        # intent.declared is event #0; the submission record is #1 — both logged
        # before any AI processing.
        actions = [call["action"] for call in audit_log_calls]
        assert actions[0] == "intent.declared", (
            f"First audit record should be 'intent.declared', got '{actions[0]}'."
        )
        assert actions[1] == "authorization_submitted", (
            f"Second audit record should be 'authorization_submitted', got '{actions[1]}'. "
            "Submission must be logged BEFORE processing in case of downstream failure."
        )

    @pytest.mark.asyncio
    async def test_run_sites_are_scope_guarded(self, sample_request, mock_auto_approved_decision):
        """Every guarded run site passes the minimum-necessary scope guard (P-4):
        the two DB writes (db.write_request, db.write_decision) and the RAG query
        each log a `scope.allow` for a legitimate in-scope call (enforce mode does
        not fire, since the run passes its own identifiers + allowed collection)."""
        audit_log_calls = []

        async def capture_log(**kwargs):
            audit_log_calls.append(kwargs)
            return MagicMock()

        with (
            patch(
                "pacca.api.routes.authorizations.orchestrator.process_decision",
                new_callable=AsyncMock,
                return_value=mock_auto_approved_decision,
            ),
            patch(
                "pacca.api.routes.authorizations.rag_engine.query",
                return_value=_healthy_outcome("Mock guideline"),
            ),
            patch(
                "pacca.db.repository.AuditRepository.log",
                side_effect=capture_log,
            ),
        ):
            from pacca.api.routes.authorizations import submit_authorization
            from pacca.models.authorization import AuthorizationRequest

            req = AuthorizationRequest(**sample_request)
            await submit_authorization(request=req, session=AsyncMock())

        guarded = [
            c["details"]["guarded_action"] for c in audit_log_calls if c["action"] == "scope.allow"
        ]
        # Guarded sites allowed (no scope.deny), in run order. rag.query is guarded
        # once per real collection the retriever reads (nccn_guidelines +
        # case_precedents), so it appears twice (#2).
        assert guarded == [
            "db.write_request",
            "rag.query",
            "rag.query",
            "db.write_decision",
        ]
        assert not [c for c in audit_log_calls if c["action"] == "scope.deny"]

    @pytest.mark.asyncio
    async def test_enforce_mode_denies_cross_case_leak_and_routes_to_review(
        self, sample_request, mock_auto_approved_decision, monkeypatch
    ):
        """In enforce mode (chg-9 default), a scope violation on a guarded DB
        write fail-closes to human review. Simulate a cross-case leak by forcing
        the run's IntentRecord subject_ref to not match the request's patient_id,
        so the db.write_request guard denies and raises ScopeViolation."""
        from pacca.models.intent import IntentRecord

        def _leaky(*, correlation_id, request_id, subject_ref):
            # A bug/leak: the declared subject does not match the actual request.
            return IntentRecord(
                correlation_id=correlation_id, request_id=request_id, subject_ref="OTHER-PATIENT"
            )

        monkeypatch.setattr(IntentRecord, "for_prior_auth", staticmethod(_leaky))

        audit_log_calls = []

        async def capture_log(**kwargs):
            audit_log_calls.append(kwargs)
            return MagicMock()

        with (
            patch(
                "pacca.api.routes.authorizations.orchestrator.process_decision",
                new_callable=AsyncMock,
                return_value=mock_auto_approved_decision,
            ),
            patch(
                "pacca.api.routes.authorizations.rag_engine.query",
                return_value=_healthy_outcome("Mock guideline"),
            ),
            patch(
                "pacca.db.repository.AuditRepository.log",
                side_effect=capture_log,
            ),
        ):
            from pacca.api.routes.authorizations import submit_authorization
            from pacca.models.authorization import AuthorizationRequest

            req = AuthorizationRequest(**sample_request)
            result = await submit_authorization(request=req, session=AsyncMock())

        # Fail-closed: routed to human review, not a silent continue or 500.
        assert result.status == AuthorizationStatus.IN_REVIEW
        assert result.review_tier_used == ReviewTier.HUMAN
        assert any(c["action"] == "scope.deny" for c in audit_log_calls)
        assert any(c["action"] == "escalation_human_review_required" for c in audit_log_calls)
        # The orchestrator never ran — denial happened at the first guarded write.
        assert not any(c["action"] == "authorization_decision_made" for c in audit_log_calls)

    @pytest.mark.asyncio
    async def test_first_audit_record_is_intent_declared(
        self, sample_request, mock_auto_approved_decision
    ):
        """
        Every run's audit trail BEGINS with action='intent.declared' (P-3 /
        chg-7), and the record carries the run's declared scope — retrievable by
        correlation_id — so P-4/P-5 can read and cite it. No PHI beyond the
        opaque subject_ref (patient_id) enters the record.
        """
        audit_log_calls = []

        async def capture_log(**kwargs):
            audit_log_calls.append(kwargs)
            return MagicMock()

        with (
            patch(
                "pacca.api.routes.authorizations.orchestrator.process_decision",
                new_callable=AsyncMock,
                return_value=mock_auto_approved_decision,
            ),
            patch(
                "pacca.api.routes.authorizations.rag_engine.query",
                return_value=_healthy_outcome("Mock guideline"),
            ),
            patch(
                "pacca.db.repository.AuditRepository.log",
                side_effect=capture_log,
            ),
        ):
            from pacca.api.routes.authorizations import submit_authorization
            from pacca.models.authorization import AuthorizationRequest

            req = AuthorizationRequest(**sample_request)
            mock_session = AsyncMock()
            await submit_authorization(request=req, session=mock_session)

        first = audit_log_calls[0]
        assert first["action"] == "intent.declared"
        # Shares the run's correlation_id with every other record.
        assert first["correlation_id"] == audit_log_calls[1]["correlation_id"]
        # The declared intent is captured in details, retrievable/queryable.
        details = first["details"]
        assert details["purpose"] == "prior_auth_adjudication"
        assert details["request_id"] == req.request_id
        assert details["subject_ref"] == req.patient_id
        # The real RAG collections are declared (not the old phantom), so
        # institutional-memory precedents are within the governed scope (#2).
        assert "nccn_guidelines" in details["allowed_collections"]
        assert "case_precedents" in details["allowed_collections"]
        assert "audit.append" in details["allowed_actions"]

    @pytest.mark.asyncio
    async def test_all_records_share_correlation_id(
        self, sample_request, mock_auto_approved_decision
    ):
        """
        All audit records for one request must share the same correlation_id.

        Without this, you cannot reconstruct a full request trace. You need
        to be able to ask: 'show me everything that happened for AUTH-001'
        and get back all 4-6 records from that single authorization flow.
        """
        audit_log_calls = []

        async def capture_log(**kwargs):
            audit_log_calls.append(kwargs)
            return MagicMock()

        with (
            patch(
                "pacca.api.routes.authorizations.orchestrator.process_decision",
                new_callable=AsyncMock,
                return_value=mock_auto_approved_decision,
            ),
            patch(
                "pacca.api.routes.authorizations.rag_engine.query",
                return_value=_healthy_outcome("Mock guideline"),
            ),
            patch(
                "pacca.db.repository.AuditRepository.log",
                side_effect=capture_log,
            ),
        ):
            from pacca.api.routes.authorizations import submit_authorization
            from pacca.models.authorization import AuthorizationRequest

            req = AuthorizationRequest(**sample_request)
            mock_session = AsyncMock()
            await submit_authorization(request=req, session=mock_session)

        # All records must have a correlation_id and they must all be the same
        correlation_ids = {call.get("correlation_id") for call in audit_log_calls}
        assert None not in correlation_ids, (
            "Some audit records are missing correlation_id. "
            "Every audit record must have a correlation_id for request tracing."
        )
        assert len(correlation_ids) == 1, (
            f"Found {len(correlation_ids)} different correlation_ids across "
            f"{len(audit_log_calls)} records. All records for one request "
            "must share the same correlation_id."
        )

    @pytest.mark.asyncio
    async def test_failure_writes_failure_audit_record(self, sample_request):
        """
        If the AI pipeline fails, a failure audit record must be written.

        Silence on failure is worse than logging the failure itself.
        Without this test, a bug in the LLM path could cause the route to
        raise a 500 error AND skip the audit log — leaving no trace.
        """
        audit_log_calls = []

        async def capture_log(**kwargs):
            audit_log_calls.append(kwargs)
            return MagicMock()

        with (
            patch(
                "pacca.api.routes.authorizations.orchestrator.process_decision",
                new_callable=AsyncMock,
                side_effect=RuntimeError("Simulated LLM API failure"),
            ),
            patch(
                "pacca.api.routes.authorizations.rag_engine.query",
                return_value=_healthy_outcome("Mock guideline"),
            ),
            patch(
                "pacca.db.repository.AuditRepository.log",
                side_effect=capture_log,
            ),
        ):
            from fastapi import HTTPException

            from pacca.api.routes.authorizations import submit_authorization
            from pacca.models.authorization import AuthorizationRequest

            req = AuthorizationRequest(**sample_request)
            mock_session = AsyncMock()

            # The route should raise HTTPException(500), not crash silently
            with pytest.raises(HTTPException) as exc_info:
                await submit_authorization(request=req, session=mock_session)

            assert exc_info.value.status_code == 500

        # There must be at least one failure audit record
        failure_records = [
            c for c in audit_log_calls if c.get("action") == "authorization_processing_failed"
        ]
        assert len(failure_records) >= 1, (
            "No failure audit record was written when the AI pipeline failed. "
            "Failures must always be logged — silence is not acceptable."
        )

        # The failure record must mark success=False
        assert failure_records[0].get("success") is False, (
            "The failure audit record should have success=False."
        )

    @pytest.mark.asyncio
    async def test_feedback_endpoint_writes_audit_record(self):
        """
        The /feedback endpoint (learning loop) must produce an audit record.

        Human overrides are the most sensitive events in the system —
        they directly change what the AI will decide in future cases.
        Every override must be logged with who taught what and when.
        """
        audit_log_calls = []

        async def capture_log(**kwargs):
            audit_log_calls.append(kwargs)
            return MagicMock()

        with (
            patch(
                "pacca.api.routes.authorizations.rag_engine.add_precedent",
            ),
            patch(
                "pacca.db.repository.AuditRepository.log",
                side_effect=capture_log,
            ),
        ):
            from pacca.api.routes.authorizations import (
                FeedbackRequest,
                learn_from_feedback,
            )

            feedback = FeedbackRequest(
                case_summary="MRI spine for 2-week back pain with motor weakness",
                decision="AUTO_APPROVED",
                rationale="Motor weakness constitutes neurological emergency",
            )
            mock_session = AsyncMock()
            await learn_from_feedback(feedback=feedback, session=mock_session)

        # Must have logged the learning event
        learning_records = [c for c in audit_log_calls if c.get("action") == "precedent_learned"]
        assert len(learning_records) == 1, (
            f"Expected 1 'precedent_learned' audit record, got {len(learning_records)}. "
            "Every learning loop event must be audited for model governance."
        )
