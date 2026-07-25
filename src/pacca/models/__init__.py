"""
PACCA domain models — public API.

Re-exports all domain model classes so code can import from
`pacca.models` rather than from individual submodules.

Examples:
    from pacca.models import AuthorizationDecision, AuthorizationStatus
    from pacca.models import ClinicalCase, EvidenceItem
    from pacca.models.enums import EscalationReason
"""

from pacca.models.authorization import (
    AuditLogEntry,
    AuthorizationDecision,
    AuthorizationRequest,
)
from pacca.models.clinical import (
    ClinicalCase,
    EvidenceItem,
)
from pacca.models.enums import (
    AuthorizationStatus,
    ClinicalSpecialty,
    ComplexityLevel,
    EscalationReason,
    EvidenceSourceType,
    ReviewTier,
    TreatmentCategory,
    UrgencyLevel,
)
from pacca.models.guidelines import (
    ClinicalGuideline,
    GuidelineChunk,
    GuidelineCriterion,
    GuidelineSearchResult,
    StepTherapyRequirement,
)
from pacca.models.triage import (
    ClassificationOutput,
    EvidenceOutput,
)

__all__ = [
    "AuditLogEntry",
    "AuthorizationDecision",
    "AuthorizationRequest",
    "AuthorizationStatus",
    "ClassificationOutput",
    "ClinicalCase",
    "ClinicalGuideline",
    "ClinicalSpecialty",
    "ComplexityLevel",
    "EscalationReason",
    "EvidenceItem",
    "EvidenceOutput",
    "EvidenceSourceType",
    "GuidelineChunk",
    "GuidelineCriterion",
    "GuidelineSearchResult",
    "ReviewTier",
    "StepTherapyRequirement",
    "TreatmentCategory",
    "UrgencyLevel",
]
