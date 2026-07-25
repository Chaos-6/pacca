"""
Clinical guideline models for the PACCA system.

These models represent clinical guidelines and their criteria
used for evidence-based decision support via RAG.
"""

from datetime import UTC, date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# The `uuid7` distribution installs its module as `uuid_extensions`, not `uuid7`.
# `from uuid7 import uuid7` raised ModuleNotFoundError at import time, which made
# this module — and everything downstream of it, including pacca.rag.pipeline —
# permanently unimportable. The rest of the codebase already imports it correctly.
from uuid_extensions import uuid7

from pacca.models.enums import ClinicalSpecialty, TreatmentCategory


class GuidelineCriterion(BaseModel):
    """
    A single criterion from a clinical guideline.

    Represents a specific requirement or condition that must be
    met for treatment authorization.
    """

    model_config = ConfigDict(frozen=True)

    criterion_id: str = Field(default_factory=lambda: str(uuid7())[:8])
    description: str = Field(..., description="Criterion description")
    criterion_type: str = Field(
        ..., description="Type: inclusion, exclusion, step_therapy, documentation"
    )

    # Requirements
    required: bool = Field(True, description="Whether criterion is required")
    alternatives: list[str] = Field(
        default_factory=list, description="Alternative criteria that satisfy this"
    )

    # Evidence expectations
    evidence_requirements: list[str] = Field(
        default_factory=list, description="Evidence needed to verify"
    )

    # Structured data for automated matching
    diagnosis_codes: list[str] = Field(default_factory=list, description="Applicable ICD-10 codes")
    treatment_codes: list[str] = Field(
        default_factory=list, description="Applicable CPT/HCPCS codes"
    )
    lab_requirements: list[dict[str, Any]] = Field(
        default_factory=list, description="Lab value requirements"
    )
    age_range: tuple[int, int] | None = Field(None, description="Age range (min, max)")


class StepTherapyRequirement(BaseModel):
    """
    Step therapy (prior treatment) requirements.

    Defines what treatments must be tried before the requested
    treatment can be authorized.
    """

    model_config = ConfigDict(frozen=True)

    step_number: int = Field(..., description="Step in the therapy sequence")
    required_treatments: list[str] = Field(..., description="Treatments for this step")
    minimum_duration_days: int | None = Field(None, description="Minimum trial duration")
    failure_criteria: str | None = Field(None, description="How failure is defined")
    documentation_required: list[str] = Field(
        default_factory=list, description="Required documentation"
    )
    exceptions: list[str] = Field(default_factory=list, description="Exceptions to this step")


class ClinicalGuideline(BaseModel):
    """
    A clinical guideline for prior authorization decisions.

    Represents a complete guideline with all criteria needed
    for automated and human decision-making.
    """

    model_config = ConfigDict(frozen=True)

    # Identification
    guideline_id: str = Field(..., description="Unique guideline identifier")
    name: str = Field(..., description="Guideline name")
    version: str = Field(..., description="Version string")
    effective_date: date = Field(..., description="When guideline became effective")
    expiration_date: date | None = Field(None, description="When guideline expires")

    # Source and authority
    source: str = Field(..., description="Source organization (NCCN, AHA, etc.)")
    source_url: str | None = Field(None, description="URL to source document")
    evidence_level: str | None = Field(None, description="Evidence level (A, B, C, etc.)")

    # Scope
    specialties: list[ClinicalSpecialty] = Field(
        default_factory=list, description="Applicable specialties"
    )
    treatment_categories: list[TreatmentCategory] = Field(
        default_factory=list, description="Applicable treatment categories"
    )
    applicable_diagnoses: list[str] = Field(
        default_factory=list, description="Applicable diagnosis codes"
    )
    applicable_treatments: list[str] = Field(
        default_factory=list, description="Applicable treatment codes"
    )

    # Content
    summary: str = Field(..., description="Brief guideline summary")
    full_text: str = Field(..., description="Full guideline text for RAG")

    # Criteria
    inclusion_criteria: list[GuidelineCriterion] = Field(
        default_factory=list, description="Inclusion criteria"
    )
    exclusion_criteria: list[GuidelineCriterion] = Field(
        default_factory=list, description="Exclusion criteria"
    )
    documentation_requirements: list[str] = Field(
        default_factory=list, description="Required documentation"
    )

    # Step therapy
    step_therapy: list[StepTherapyRequirement] = Field(
        default_factory=list, description="Step therapy requirements"
    )
    step_therapy_exceptions: list[str] = Field(
        default_factory=list, description="Exceptions to step therapy"
    )

    # Special considerations
    age_restrictions: str | None = Field(None, description="Age-based restrictions")
    pregnancy_considerations: str | None = Field(None, description="Pregnancy notes")
    contraindications: list[str] = Field(default_factory=list, description="Contraindications")
    warnings: list[str] = Field(default_factory=list, description="Clinical warnings")

    # Metadata
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    tags: list[str] = Field(default_factory=list, description="Search tags")

    @property
    def is_active(self) -> bool:
        """Check if guideline is currently active."""
        today = date.today()
        if self.effective_date > today:
            return False
        return not (self.expiration_date and self.expiration_date < today)

    @property
    def requires_step_therapy(self) -> bool:
        """Check if guideline has step therapy requirements."""
        return len(self.step_therapy) > 0


class GuidelineChunk(BaseModel):
    """
    A chunk of guideline text for vector storage.

    Used for RAG retrieval - guidelines are chunked for
    efficient similarity search.
    """

    model_config = ConfigDict(frozen=True)

    chunk_id: str = Field(default_factory=lambda: str(uuid7()))
    guideline_id: str = Field(..., description="Parent guideline ID")
    guideline_name: str = Field(..., description="Parent guideline name")
    source: str = Field(..., description="Guideline source")

    # Chunk content
    content: str = Field(..., description="Chunk text content")
    chunk_index: int = Field(..., description="Position in original document")
    section_name: str | None = Field(None, description="Section this came from")

    # Metadata for filtering
    specialties: list[str] = Field(default_factory=list)
    diagnosis_codes: list[str] = Field(default_factory=list)
    treatment_codes: list[str] = Field(default_factory=list)

    # Embedding (populated by vector store)
    embedding: list[float] | None = Field(None, description="Vector embedding")


class GuidelineSearchResult(BaseModel):
    """Result from searching guidelines via RAG."""

    model_config = ConfigDict(frozen=True)

    chunk: GuidelineChunk
    similarity_score: float = Field(..., ge=0.0, le=1.0)
    rank: int = Field(..., ge=1)

    # Highlighting
    highlighted_content: str | None = Field(None, description="Content with highlights")
    matched_terms: list[str] = Field(default_factory=list)
