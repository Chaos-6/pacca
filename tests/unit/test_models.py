"""
Unit tests for PACCA v2.2 domain models.

Tests the actual models in src/pacca/models/:
  - enums.py: AuthorizationStatus, EscalationReason, ReviewTier, EvidenceSourceType
  - clinical.py: ClinicalCase, EvidenceItem
  - authorization.py: AuthorizationDecision, AuthorizationRequest, AuditLogEntry

Note: The original test_models.py tested a richer domain model layer
(PatientDemographics, Diagnosis, TreatmentCategory, etc.) that was part
of the pre-Level 5 architecture. Those models were replaced by the
simplified ClinicalCase/EvidenceItem layer during the upgrade_to_level5.sh
sprint. The original tests are preserved in tests/archive/test_models_v1.py.
"""

from datetime import datetime

from pacca.models.authorization import (
    AuditLogEntry,
    AuthorizationDecision,
)
from pacca.models.clinical import ClinicalCase, EvidenceItem
from pacca.models.enums import (
    AuthorizationStatus,
    EscalationReason,
    EvidenceSourceType,
    ReviewTier,
)

# =============================================================================
# Enum tests
# =============================================================================


class TestAuthorizationStatus:
    """Verify all expected status values exist and are string-compatible."""

    def test_all_statuses_are_strings(self):
        """AuthorizationStatus values must be usable as strings (str, Enum)."""
        for status in AuthorizationStatus:
            assert isinstance(status.value, str)

    def test_expected_statuses_exist(self):
        """The statuses used throughout the v2.2 codebase must be present."""
        assert AuthorizationStatus.IN_REVIEW
        assert AuthorizationStatus.AUTO_APPROVED
        assert AuthorizationStatus.PENDING
        assert AuthorizationStatus.DENIED

    def test_status_value_matches_string(self):
        """Status enum values must equal their string representation."""
        assert AuthorizationStatus.AUTO_APPROVED.value == "AUTO_APPROVED"
        assert AuthorizationStatus.IN_REVIEW.value == "IN_REVIEW"


class TestEscalationReason:
    """Verify all 7 PRD SS5.4 escalation branches have enum values."""

    def test_all_seven_branches_represented(self):
        """
        Every escalation branch from the 7-branch tree must have an enum value.
        If a branch is added or removed, this test catches the discrepancy.
        """
        required_reasons = [
            EscalationReason.CONFIDENCE_BELOW_THRESHOLD,  # Branch 3
            EscalationReason.MEDICAL_DIRECTOR_REQUIRED,  # Branch 2
            EscalationReason.EXPERIMENTAL_TREATMENT,  # Branch 4
            EscalationReason.RARE_CONDITION,  # Branch 5
            EscalationReason.CONFLICTING_GUIDELINES,  # Branch 6
            EscalationReason.PRIOR_DENIAL_SAME_SERVICE,  # Branch 7
            EscalationReason.HIGH_COST,
            EscalationReason.PEDIATRIC_COMPLEX,
        ]
        for reason in required_reasons:
            assert isinstance(reason.value, str), (
                f"EscalationReason.{reason.name} must have a string value."
            )

    def test_reason_values_are_snake_case(self):
        """
        Escalation reason values must be snake_case — they appear in audit
        log JSONB fields and are queried by compliance tools.
        """
        for reason in EscalationReason:
            assert " " not in reason.value, (
                f"EscalationReason.{reason.name} value '{reason.value}' "
                f"contains spaces. Values must be snake_case for audit queries."
            )


class TestReviewTier:
    """Verify review tier enum matches agent names used in orchestrator."""

    def test_all_tiers_exist(self):
        assert ReviewTier.AUTOMATED
        assert ReviewTier.MEDICAL_DIRECTOR_AGENT
        assert ReviewTier.HUMAN

    def test_tier_values_are_strings(self):
        for tier in ReviewTier:
            assert isinstance(tier.value, str)


class TestEvidenceSourceType:
    """Verify evidence source types cover all expected clinical data sources."""

    def test_clinical_note_exists(self):
        """CLINICAL_NOTE is the most common evidence source in the test suite."""
        assert EvidenceSourceType.CLINICAL_NOTE.value == "CLINICAL_NOTE"

    def test_lab_result_exists(self):
        assert EvidenceSourceType.LAB_RESULT.value == "LAB_RESULT"


# =============================================================================
# ClinicalCase and EvidenceItem tests
# =============================================================================


class TestEvidenceItem:
    """Verify EvidenceItem is constructible and holds expected fields."""

    def test_basic_construction(self):
        """Build a minimal EvidenceItem — all required fields."""
        item = EvidenceItem(
            id="e1",
            source_type=EvidenceSourceType.CLINICAL_NOTE,
            description="Stage IIIA NSCLC, PD-L1 TPS >= 50%",
            original_text="Patient presents with stage IIIA non-small cell lung cancer.",
            confidence=0.95,
        )
        assert item.id == "e1"
        assert item.source_type == EvidenceSourceType.CLINICAL_NOTE
        assert item.confidence == 0.95

    def test_confidence_is_float(self):
        """Confidence must be a float (used in numeric comparisons throughout)."""
        item = EvidenceItem(
            id="e2",
            source_type=EvidenceSourceType.LAB_RESULT,
            description="PD-L1 62%",
            original_text="PD-L1 TPS: 62%",
            confidence=0.62,
        )
        assert isinstance(item.confidence, float)

    def test_timestamp_auto_populated(self):
        """EvidenceItem timestamp should be set automatically."""
        item = EvidenceItem(
            id="e3",
            source_type=EvidenceSourceType.CLINICAL_NOTE,
            description="Test",
            original_text="Test",
            confidence=0.9,
        )
        assert isinstance(item.timestamp, datetime)


class TestClinicalCase:
    """Verify ClinicalCase is the correct shape for the v2.2 agent pipeline."""

    def test_basic_construction(self):
        """Build a minimal ClinicalCase — all required fields."""
        case = ClinicalCase(
            patient_id="P-001",
            primary_diagnosis_code="C34.1",
            procedure_code="J9271",
        )
        assert case.patient_id == "P-001"
        assert case.primary_diagnosis_code == "C34.1"
        assert case.procedure_code == "J9271"
        assert case.evidence == []

    def test_evidence_list_appends(self):
        """Evidence items can be added to the case."""
        item = EvidenceItem(
            id="e1",
            source_type=EvidenceSourceType.CLINICAL_NOTE,
            description="NSCLC",
            original_text="Stage IIIA NSCLC.",
            confidence=0.9,
        )
        case = ClinicalCase(
            patient_id="P-002",
            primary_diagnosis_code="C34.1",
            procedure_code="J9271",
            evidence=[item],
        )
        assert len(case.evidence) == 1
        assert case.evidence[0].id == "e1"

    def test_model_dump_json_is_serializable(self):
        """
        ClinicalCase.model_dump_json() must produce valid JSON.

        This method is called in agent user-turn prompt construction
        (decision.py) — if it fails the agent call will crash.
        """
        case = ClinicalCase(
            patient_id="P-003",
            primary_diagnosis_code="E75.22",
            procedure_code="J0205",
        )
        json_str = case.model_dump_json()
        assert "P-003" in json_str
        assert "E75.22" in json_str


# =============================================================================
# AuthorizationDecision tests
# =============================================================================


class TestAuthorizationDecision:
    """Verify AuthorizationDecision matches the shape agents return."""

    def test_basic_construction(self):
        """Build a minimal AuthorizationDecision."""
        decision = AuthorizationDecision(
            decision_id="DEC-001",
            status=AuthorizationStatus.AUTO_APPROVED,
            confidence_score=0.97,
            rationale="NCCN Category 1 for PD-L1 >= 50% NSCLC.",
            review_tier_used=ReviewTier.AUTOMATED,
            model_id="claude-sonnet-4-5-20250929",
            prompt_version="v2.7",
        )
        assert decision.decision_id == "DEC-001"
        assert decision.status == AuthorizationStatus.AUTO_APPROVED
        assert decision.confidence_score == 0.97
        assert decision.review_tier_used == ReviewTier.AUTOMATED

    def test_confidence_score_is_float(self):
        """Confidence score must be a float — used in numeric comparisons."""
        decision = AuthorizationDecision(
            decision_id="DEC-002",
            status=AuthorizationStatus.IN_REVIEW,
            confidence_score=0.72,
            rationale="Insufficient documentation.",
            review_tier_used=ReviewTier.AUTOMATED,
            model_id="claude-sonnet-4-5-20250929",
            prompt_version="v2.7",
        )
        assert isinstance(decision.confidence_score, float)
        assert 0.0 <= decision.confidence_score <= 1.0

    def test_audit_trail_is_empty_by_default(self):
        """audit_trail must default to an empty list (not None)."""
        decision = AuthorizationDecision(
            decision_id="DEC-003",
            status=AuthorizationStatus.AUTO_APPROVED,
            confidence_score=0.95,
            rationale="Test.",
            review_tier_used=ReviewTier.AUTOMATED,
            model_id="claude-sonnet-4-5-20250929",
            prompt_version="v2.7",
        )
        assert decision.audit_trail == []
        assert isinstance(decision.audit_trail, list)

    def test_status_update_via_assignment(self):
        """
        The orchestrator updates decision.status directly by assignment.
        Pydantic v2 models are mutable by default — verify this works.
        """
        decision = AuthorizationDecision(
            decision_id="DEC-004",
            status=AuthorizationStatus.AUTO_APPROVED,
            confidence_score=0.92,
            rationale="Test.",
            review_tier_used=ReviewTier.AUTOMATED,
            model_id="claude-sonnet-4-5-20250929",
            prompt_version="v2.7",
        )
        decision.status = AuthorizationStatus.IN_REVIEW
        assert decision.status == AuthorizationStatus.IN_REVIEW

    def test_review_tier_update_via_assignment(self):
        """
        Agents set review_tier_used after execute() returns.
        Verify this assignment works on the Pydantic model.
        """
        decision = AuthorizationDecision(
            decision_id="DEC-005",
            status=AuthorizationStatus.AUTO_APPROVED,
            confidence_score=0.97,
            rationale="Test.",
            review_tier_used=ReviewTier.AUTOMATED,
            model_id="claude-sonnet-4-5-20250929",
            prompt_version="v2.7",
        )
        decision.review_tier_used = ReviewTier.MEDICAL_DIRECTOR_AGENT
        assert decision.review_tier_used == ReviewTier.MEDICAL_DIRECTOR_AGENT


# =============================================================================
# AuditLogEntry tests
# =============================================================================


class TestAuditLogEntry:
    """Verify AuditLogEntry — used in authorization decision audit trails."""

    def test_basic_construction(self):
        """Build a minimal AuditLogEntry."""
        entry = AuditLogEntry(
            entry_type="decision_made",
            message="AUTO_APPROVED with confidence 0.97",
        )
        assert entry.entry_type == "decision_made"
        assert isinstance(entry.timestamp, datetime)

    def test_agent_id_is_optional(self):
        """agent_id is optional — system events have no agent."""
        entry_no_agent = AuditLogEntry(
            entry_type="submission_received",
            message="Authorization submitted.",
        )
        assert entry_no_agent.agent_id is None

        entry_with_agent = AuditLogEntry(
            entry_type="agent_decision",
            message="Tier 1 evaluation complete.",
            agent_id="DecisionSupportAgent",
        )
        assert entry_with_agent.agent_id == "DecisionSupportAgent"
