"""
Enumeration types for the PACCA domain model.

Teaching note — why use enums instead of plain strings?
  Strings like "IN_REVIEW" or "experimental_treatment" can be mistyped
  anywhere in the codebase and the error only appears at runtime. Enums
  make typos a Python import error — the mistake is caught immediately.
  They also make the audit log self-documenting: when you see
  EscalationReason.EXPERIMENTAL_TREATMENT in a record, you know exactly
  what it means and where to find its definition.
"""

from enum import StrEnum


class AuthorizationStatus(StrEnum):
    """The lifecycle status of a prior authorization request."""

    PENDING = "PENDING"
    INFORMATION_NEEDED = "INFORMATION_NEEDED"
    IN_REVIEW = "IN_REVIEW"
    AUTO_APPROVED = "AUTO_APPROVED"
    DENIED = "DENIED"
    CANCELLED = "CANCELLED"


class EvidenceSourceType(StrEnum):
    """The origin type of a clinical evidence item."""

    LAB_RESULT = "LAB_RESULT"
    MEDICATION_HISTORY = "MEDICATION"
    CLINICAL_NOTE = "CLINICAL_NOTE"
    PATIENT_REPORTED = "PATIENT_REPORTED"


class ComplexityLevel(StrEnum):
    """Clinical complexity of an authorization case (1-5 scale in PRD)."""

    ROUTINE = "ROUTINE"
    INTERMEDIATE = "INTERMEDIATE"
    COMPLEX = "COMPLEX"
    CRITICAL = "CRITICAL"


class UrgencyLevel(StrEnum):
    """Clinical urgency of a request, assessed by the classification agent."""

    ROUTINE = "ROUTINE"
    EXPEDITED = "EXPEDITED"
    URGENT = "URGENT"


class ReviewTier(StrEnum):
    """Which agent or human tier produced the final decision."""

    AUTOMATED = "AUTOMATED"
    MEDICAL_DIRECTOR_AGENT = "MEDICAL_DIRECTOR_AGENT"
    HUMAN = "HUMAN"


class ClinicalSpecialty(StrEnum):
    """
    Clinical specialty a guideline applies to.

    Scope note: the members below are the specialties actually covered by the
    guideline corpus in `pacca.rag.sample_guidelines`. Add a member when a
    guideline that needs it is added — an unused member is an unverified claim
    about coverage.
    """

    CARDIOLOGY = "cardiology"
    NEUROLOGY = "neurology"
    ONCOLOGY = "oncology"
    ORTHOPEDICS = "orthopedics"
    PULMONOLOGY = "pulmonology"
    RADIOLOGY = "radiology"
    RHEUMATOLOGY = "rheumatology"


class TreatmentCategory(StrEnum):
    """
    Category of a requested treatment, used as a RAG metadata filter.

    Same scope note as ClinicalSpecialty: only categories represented in the
    guideline corpus are listed.
    """

    IMAGING = "imaging"
    MEDICATION = "medication"


class EscalationReason(StrEnum):
    """
    Structured reasons why a case was escalated.

    Using an enum here instead of free-form strings means:
      - Every escalation reason is findable by code search
      - Audit records are queryable by reason (e.g. "how many cases
        escalated due to experimental treatment this quarter?")
      - New reasons require a code change + review, not an accidental typo

    PRD SS5.4 specified the original 7 triggers; the set has since grown as
    governance layers were added (e.g. ADULT_COMPLEX, and the safety reasons
    SCOPE_VIOLATION, UNGROUNDED_EVIDENCE, and RAG_DEGRADED). Each member maps
    to a branch or a deterministic safety short-circuit in orchestrator.py
    or the submit route.
    """

    # ── Confidence-based escalation (original 3 branches) ────────────────────

    CONFIDENCE_BELOW_THRESHOLD = "confidence_below_threshold"
    """Decision Agent confidence < 0.90 — insufficient certainty for autonomous decision."""

    MEDICAL_DIRECTOR_REQUIRED = "medical_director_required"
    """Classification Agent flagged this case as requiring Medical Director review."""

    # ── Pre-flight clinical escalation triggers (Week 2 — 4 new branches) ────

    EXPERIMENTAL_TREATMENT = "experimental_treatment"
    """
    Requested treatment is investigational, not yet FDA-approved, or in active
    clinical trial phase. No autonomous AI decision is appropriate because:
      - Clinical guidelines may not yet exist for this treatment
      - The AI's training data on this treatment is minimal or analogical
      - Healthcare regulations require human review for experimental treatments
    """

    RARE_CONDITION = "rare_condition"
    """
    Primary diagnosis maps to an ICD-10 code with low population prevalence
    (defined as < 1 in 10,000 affected individuals). Escalated because:
      - Clinical guidelines for rare conditions are sparse or contradictory
      - AI confidence on rare conditions is likely overestimated — the model
        pattern-matches on similar common conditions, not on rare disease evidence
      - Rare disease specialists should evaluate appropriateness
    """

    CONFLICTING_GUIDELINES = "conflicting_guidelines"
    """
    RAG retrieval returned guidelines from multiple authoritative sources
    (e.g. NCCN vs CMS) that give different or contradictory recommendations.
    Escalated because:
      - Averaging across conflicting guidelines produces a confidently wrong answer
      - The conflict itself is clinically meaningful information requiring judgment
      - A human reviewer weighs guidelines against the specific patient context
    """

    PRIOR_DENIAL_SAME_SERVICE = "prior_denial_same_service"
    """
    This patient has a documented prior denial for the same procedure code.
    Escalated because two scenarios require human judgment to distinguish:
      (a) Prior denial was correct and this is a repeat claim — requires fraud review
      (b) Patient circumstances changed — requires a human to evaluate what changed
    The AI cannot reliably distinguish between these scenarios.
    """

    HIGH_COST = "high_cost"
    """Estimated treatment cost exceeds the configured HIGH_COST_THRESHOLD."""

    PEDIATRIC_COMPLEX = "pediatric_complex"
    """Pediatric patient (age < 18) with moderate or higher complexity score."""

    ADULT_COMPLEX = "adult_complex"
    """
    Adult patient (age >= 18) whose deterministic complexity score reaches the
    specialist-review threshold (settings.complexity_specialist_review_min, =4).
    Escalated because the same policy logic that flags complex pediatric cases
    applies to complex adults — specialist review is warranted regardless of how
    confident the clinical-eligibility check is. The classification agent's
    LLM-self-assessed complexity flag remains advisory; this pre-flight is
    authoritative and reproducible.
    """

    SCOPE_VIOLATION = "scope_violation"
    """
    The minimum-necessary scope guard (P-4 / chg-8) denied a tool/DB/RAG call
    whose target fell outside the run's declared IntentRecord scope — an action
    not in `allowed_actions`, a case identifier that did not match the run's
    intent (cross-case leak), or a RAG query against a collection the run was
    not permitted to touch. Fail-closed: the run is routed to human review
    rather than silently continuing.
    """

    UNGROUNDED_EVIDENCE = "ungrounded_evidence"
    """
    The evidence-grounding detector (P-5 / chg-10) found a decision that cited an
    evidence id which did not resolve to an EvidenceItem present in the
    submission. The decision may be relying on fabricated or misattributed
    evidence, so it is routed to human review regardless of confidence — the
    production-path equivalent of the GC-018/019 anti-hallucination gate.
    """

    RAG_DEGRADED = "rag_degraded"
    """
    ``GuidelineRetriever.query()`` (chg-19) reported a degraded
    ``RetrievalOutcome`` on either of two independent axes:
    ``RetrievalOutcome.mode != "pipeline"`` (the governed RAGPipeline was
    unavailable or raised, and guideline retrieval fell back to the legacy
    direct-ChromaDB path), or ``RetrievalOutcome.precedents_degraded`` (the
    institutional-memory precedents collection itself raised during an
    otherwise-healthy pipeline call — no guideline fallback occurred at all).
    Every degradation is audited (``action="rag.degraded"``, details carry
    both axes) regardless of this setting; this reason fires only when
    ``settings.rag_degraded_escalates`` is True, routing the case to human
    review rather than letting the DecisionAgent reason over unverified or
    incomplete retrieved context. Defaults to warn mode (audited, not
    routed) per the same warn -> enforce rollout precedent as the P-4 scope
    guard (chg-8 -> chg-9); promote once degradation rates in production are
    known (chg-20).
    """
