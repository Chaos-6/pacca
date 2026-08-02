"""
Tests for the 7-branch escalation tree — Week 2 implementation.

These tests verify every branch of PRD SS5.4. Each test is named after
the branch it covers so the test suite doubles as executable documentation
of the escalation policy.

Teaching note — test-as-specification:
  Every test here makes a claim about clinical safety behavior. If a future
  developer accidentally removes one of these escalation branches (say, by
  refactoring the orchestrator), the corresponding test will fail loudly.
  Tests are the only machine-enforceable form of specification.

  Notice what each test does NOT do:
  - It does not call the Claude API (too slow, costs money, flaky)
  - It does not write to a database (unnecessary for testing logic)
  - It does not test the agent's clinical reasoning (that's the agent's job)

  It ONLY tests whether the Orchestrator routes correctly given a specific
  input. This is called "behavior testing" — we test the observable outputs
  given controlled inputs.

Teaching note — test structure (AAA pattern):
  Every test follows Arrange / Act / Assert:
    Arrange: set up the inputs (build a ClinicalCase, configure mocks)
    Act:     call the function being tested
    Assert:  verify the output matches the expected behavior

  This structure makes tests readable as documentation. You can read any
  test here and understand the escalation rule it encodes.
"""

from unittest.mock import AsyncMock

import pytest

from pacca.agents.clinical_risk_detector import ClinicalRiskDetector
from pacca.agents.decision import DecisionContext
from pacca.agents.orchestrator import Orchestrator
from pacca.config.settings import apply_overrides, clear_all_overrides
from pacca.models.authorization import AuthorizationDecision
from pacca.models.clinical import ClinicalCase, EvidenceItem
from pacca.models.enums import (
    AuthorizationStatus,
    EscalationReason,
    EvidenceSourceType,
    ReviewTier,
)

# =============================================================================
# Shared fixtures
# =============================================================================


def make_case(
    procedure_code: str = "J9271",
    diagnosis_code: str = "C34.1",
    evidence_text: str = "Stage IIIA NSCLC, PD-L1 TPS >= 50%",
) -> ClinicalCase:
    """
    Build a minimal ClinicalCase for testing.

    Default values represent a routine oncology case that should NOT
    trigger any pre-flight escalation on its own.
    """
    return ClinicalCase(
        patient_id="P-TEST-001",
        primary_diagnosis_code=diagnosis_code,
        procedure_code=procedure_code,
        evidence=[
            EvidenceItem(
                id="e1",
                source_type=EvidenceSourceType.CLINICAL_NOTE,
                description=evidence_text,
                original_text=evidence_text,
                confidence=0.95,
            )
        ],
    )


def make_decision(
    status: AuthorizationStatus = AuthorizationStatus.AUTO_APPROVED,
    confidence: float = 0.97,
) -> AuthorizationDecision:
    """Build a minimal AuthorizationDecision for mock returns."""
    return AuthorizationDecision(
        decision_id="DEC-TEST-001",
        status=status,
        confidence_score=confidence,
        rationale="Test rationale.",
        review_tier_used=ReviewTier.AUTOMATED,
    )


# =============================================================================
# ClinicalRiskDetector unit tests — each detection method in isolation
# =============================================================================


class TestClinicalRiskDetector:
    """
    Tests for each detection method in ClinicalRiskDetector.

    These test the detector in complete isolation — no Orchestrator,
    no agents, no database. Pure input-output verification.
    """

    def setup_method(self) -> None:
        self.detector = ClinicalRiskDetector()

    # ── Branch 4: Experimental treatment ─────────────────────────────────────

    def test_detects_experimental_procedure_code(self) -> None:
        """
        Branch 4: A known experimental procedure code triggers escalation.

        Real-world meaning: CAR-T cell therapies (Q2041, Q2042) are only
        FDA-approved for specific indications. Any other use is experimental
        and must not be autonomously approved.
        """
        case = make_case(procedure_code="Q2041")  # Axicabtagene (Yescarta) CAR-T
        flags = self.detector.evaluate(case)

        assert flags.should_pre_escalate, (
            "CAR-T therapy Q2041 is on the experimental procedure list "
            "and must always trigger pre-flight escalation."
        )
        assert EscalationReason.EXPERIMENTAL_TREATMENT in flags.reasons

    def test_detects_experimental_keyword_in_evidence(self) -> None:
        """
        Branch 4: Evidence text mentioning 'clinical trial' triggers escalation.

        Real-world meaning: a provider might submit a standard procedure code
        but note in clinical text that the treatment is being used in a trial.
        The keyword scan catches this case.
        """
        case = make_case(
            procedure_code="J9271",  # Standard code — not on experimental list
            evidence_text="Patient enrolled in Phase II clinical trial for combination therapy.",
        )
        flags = self.detector.evaluate(case)

        assert flags.should_pre_escalate
        assert EscalationReason.EXPERIMENTAL_TREATMENT in flags.reasons

    def test_routine_procedure_does_not_trigger_experimental(self) -> None:
        """
        Branch 4 negative test: a standard oncology procedure is not flagged.

        J9271 is Pembrolizumab (Keytruda) — FDA-approved for NSCLC.
        It should not trigger experimental treatment escalation.
        """
        case = make_case(
            procedure_code="J9271",
            evidence_text="Standard first-line pembrolizumab therapy for PD-L1 >= 50% NSCLC.",
        )
        flags = self.detector.evaluate(case)

        assert EscalationReason.EXPERIMENTAL_TREATMENT not in flags.reasons

    # ── Branch 5: Rare condition ──────────────────────────────────────────────

    def test_detects_rare_condition_by_icd10_prefix(self) -> None:
        """
        Branch 5: A Gaucher disease diagnosis code triggers escalation.

        E75.22 (Gaucher disease) starts with 'E75' which is in
        RARE_CONDITION_ICD10_PREFIXES. This is a lysosomal storage disorder
        affecting roughly 1 in 40,000 people.
        """
        case = make_case(diagnosis_code="E75.22")
        flags = self.detector.evaluate(case)

        assert flags.should_pre_escalate
        assert EscalationReason.RARE_CONDITION in flags.reasons

    def test_detects_huntington_disease(self) -> None:
        """
        Branch 5: Huntington disease (G10) is correctly flagged as rare.

        G10 is in RARE_CONDITION_ICD10_PREFIXES. Huntington affects
        roughly 1 in 10,000 people and has limited treatment options.
        """
        case = make_case(diagnosis_code="G10")
        flags = self.detector.evaluate(case)

        assert EscalationReason.RARE_CONDITION in flags.reasons

    def test_common_diagnosis_does_not_trigger_rare_condition(self) -> None:
        """
        Branch 5 negative test: common lung cancer code is not flagged.

        C34.1 (NSCLC upper lobe) is not in RARE_CONDITION_ICD10_PREFIXES.
        This is one of the most common cancer diagnoses and should not
        trigger rare condition escalation.
        """
        case = make_case(diagnosis_code="C34.1")
        flags = self.detector.evaluate(case)

        assert EscalationReason.RARE_CONDITION not in flags.reasons

    # ── Branch 6: Conflicting guidelines ─────────────────────────────────────

    def test_detects_conflicting_guidelines(self) -> None:
        """
        Branch 6: Guidelines containing both approval and conflict markers trigger escalation.

        Real-world meaning: NCCN might say 'recommended Category 1' for
        one patient profile, while CMS coverage says 'not recommended for
        patients with prior platinum failure'. Both phrases appear in the
        RAG context. A human must resolve which applies.
        """
        conflicting_context = (
            "NCCN Guideline: Pembrolizumab is recommended as Category 1 "
            "for PD-L1 >= 50% NSCLC.\n"
            "CMS Coverage Determination: Treatment is not recommended "
            "for patients with prior platinum-based chemotherapy failure "
            "without documented PD-L1 testing."
        )
        case = make_case()
        flags = self.detector.evaluate(case, guidelines_context=conflicting_context)

        assert flags.should_pre_escalate
        assert EscalationReason.CONFLICTING_GUIDELINES in flags.reasons

    def test_consistent_approval_guidelines_do_not_conflict(self) -> None:
        """
        Branch 6 negative test: guidelines that only support the treatment
        should not trigger the conflict check.
        """
        clear_context = (
            "NCCN: Pembrolizumab is recommended and strongly supported "
            "as standard of care for PD-L1 >= 50% NSCLC. "
            "Evidence-based Category 1 recommendation."
        )
        case = make_case()
        flags = self.detector.evaluate(case, guidelines_context=clear_context)

        assert EscalationReason.CONFLICTING_GUIDELINES not in flags.reasons

    def test_unambiguous_rejection_alone_is_not_a_conflict(self) -> None:
        """
        Branch 6 regression (chg-30): a guideline that ONLY rejects must not
        register as a conflict with itself.

        The approval marker "recommended" is a substring of the conflict marker
        "not recommended". So a single, unambiguous negative recommendation
        satisfied both `has_approval` and `has_rejection` and fabricated a
        conflict out of a guideline that contains none.

        This is not the documented, accepted heuristic false positive (a
        restriction that does not apply to this patient). It is the approval
        test matching inside the negation of itself -- the same
        substring-vs-whole-word defect chg-25 fixed in the evaluator, sitting
        here on a deterministic safety boundary.

        The consequence was systematic rather than occasional: a guideline-based
        denial necessarily says "not recommended", so EVERY such case
        pre-escalated and the DENY outcome became structurally unreachable.
        Measured on GC-026, whose context contains "recommended" exactly once,
        entirely inside "not recommended".
        """
        rejection_only = (
            "ASTRO Model Policy: proton-beam radiation is not recommended over "
            "conventional intensity-modulated radiation therapy for low-risk "
            "prostate cancer absent a documented contraindication to IMRT."
        )
        case = make_case()
        flags = self.detector.evaluate(case, guidelines_context=rejection_only)

        assert EscalationReason.CONFLICTING_GUIDELINES not in flags.reasons, (
            "an unambiguous negative recommendation was read as a conflict -- "
            "the approval marker matched inside the conflict marker"
        )

    def test_gc026_real_context_is_not_a_conflict(self) -> None:
        """The production case that exposed it, pinned verbatim.

        GC-026 expects DENIED. Branch 6 pre-escalated it, so the DecisionAgent
        was never called and the expected outcome was unreachable regardless of
        any prompt or memory change.
        """
        from tests.clinical.expansion_cases import EXPANSION_CASES

        gc_026 = next(c for c in EXPANSION_CASES if c.case_id == "GC-026")
        flags = self.detector.evaluate(make_case(), guidelines_context=gc_026.guidelines_context)

        assert EscalationReason.CONFLICTING_GUIDELINES not in flags.reasons

    def test_aligned_guideline_recommending_an_alternative_still_conflicts(self) -> None:
        """The accepted heuristic false positive is deliberately NOT fixed here.

        GC-027's guideline recommends non-invasive testing AND says invasive
        cath is not recommended -- two statements about DIFFERENT services in an
        aligned source. The detector cannot tell that from a genuine
        source-vs-source conflict, and its docstring says so, accepting the
        false positive on the grounds that unnecessary review is cheap and a
        missed conflict is not.

        chg-30 fixes only the substring defect. Narrowing this case is a change
        to a documented safety trade-off and needs its own decision, so this
        test pins the CURRENT behaviour to keep that change deliberate rather
        than accidental.
        """
        from tests.clinical.expansion_cases import EXPANSION_CASES

        gc_027 = next(c for c in EXPANSION_CASES if c.case_id == "GC-027")
        flags = self.detector.evaluate(make_case(), guidelines_context=gc_027.guidelines_context)

        assert EscalationReason.CONFLICTING_GUIDELINES in flags.reasons

    def test_gc034_escalates_but_only_via_defective_keyword_hits(self) -> None:
        """GC-034 pre-escalates for the RIGHT outcome via the WRONG reasons.

        David's call (2026-08-01): this case should route to human review. That
        is right on the merits -- off-label oncology immunotherapy after two
        lines, with no compendia support, is a human-review case.

        But every keyword that fires Branch 4 on it is a false positive:

          * "No published Phase III data" -> matches 'phase i', 'phase ii' AND
            'phase iii', because the scan is substring-based. One phrase, three
            hits, and the phrase asserts evidence is ABSENT.
          * "Patient is not enrolled in a clinical trial" -> matches
            'clinical trial'. There is no negation awareness; the sentence says
            the opposite of what the match implies.
          * "requesting nivolumab off-label" -> matches 'off-label', which sits
            in EXPERIMENTAL_DIAGNOSIS_KEYWORDS although off-label use of an
            FDA-approved drug is a COVERAGE question, not an experimental one.
            Same category error as the coverage/medical-necessity boundary one
            layer up (CLAUDE.md safety invariants).

        So GC-034's expected_outcome is COUPLED to those defects. This test
        exists to make that coupling loud: when Branch 4 is repaired -- whole-word
        matching, negation awareness, or reclassifying 'off-label' -- this goes
        red, and the right branch for GC-034 must then be DECIDED rather than
        assumed. A silent flip to no-escalation would leave the case failing for
        an unrelated-looking reason months later.

        None of the three defects is fixed here. The 'off-label' reclassification
        is a clinical-category call and is parked pending a clinician.
        """
        from pacca.agents.clinical_risk_detector import EXPERIMENTAL_DIAGNOSIS_KEYWORDS
        from tests.clinical.denial_cases import DENIAL_CASES

        gc_034 = next(c for c in DENIAL_CASES if c.case_id == "GC-034")
        case = make_case()
        case.evidence[0].original_text = gc_034.clinical_notes
        case.evidence[0].description = gc_034.clinical_notes[:200]

        flags = self.detector.evaluate(case, guidelines_context=gc_034.guidelines_context)

        assert EscalationReason.EXPERIMENTAL_TREATMENT in flags.reasons, (
            "GC-034 no longer pre-escalates via Branch 4. If Branch 4 was just "
            "repaired, that is expected -- and GC-034's expected_outcome must now "
            "be re-decided rather than left as PRE_FLIGHT_ESCALATE / "
            "BRANCH_4_EXPERIMENTAL. See the case's clinical_rationale."
        )

        text = gc_034.clinical_notes.lower()
        hits = [kw for kw in EXPERIMENTAL_DIAGNOSIS_KEYWORDS if kw in text]
        assert {"phase i", "phase ii", "phase iii"} <= set(hits), (
            "the substring triple-hit from one 'Phase III' is gone -- Branch 4's "
            "matching was narrowed; re-decide GC-034"
        )
        assert "clinical trial" in hits, (
            "the negated 'not enrolled in a clinical trial' no longer matches -- "
            "negation awareness was added; re-decide GC-034"
        )
        assert "off-label" in hits, (
            "'off-label' no longer counts as experimental -- the category was "
            "corrected; re-decide GC-034"
        )

    def test_empty_guidelines_context_does_not_error(self) -> None:
        """
        Branch 6 edge case: empty guidelines context (no RAG results) should
        not trigger the conflict check and must not raise an exception.

        Real-world meaning: ChromaDB returned no results for this query.
        The system should degrade gracefully, not crash.
        """
        case = make_case()
        flags = self.detector.evaluate(case, guidelines_context="")

        assert EscalationReason.CONFLICTING_GUIDELINES not in flags.reasons

    # ── Branch 7: Prior denial on same service ────────────────────────────────

    def test_detects_prior_denial_same_procedure(self) -> None:
        """
        Branch 7: A prior denial for the same procedure code triggers escalation.

        Real-world meaning: this patient was previously denied Pembrolizumab
        (J9271). The same code is now being resubmitted. This must go to a
        human reviewer who can see both the original denial and the new submission.
        """
        case = make_case(procedure_code="J9271")
        flags = self.detector.evaluate(
            case,
            prior_denial_codes=["J9271", "99213"],  # J9271 is the current procedure
        )

        assert flags.should_pre_escalate
        assert EscalationReason.PRIOR_DENIAL_SAME_SERVICE in flags.reasons

    def test_prior_denial_different_procedure_does_not_trigger(self) -> None:
        """
        Branch 7 negative test: a prior denial for a DIFFERENT procedure
        should not block the current request.

        Real-world meaning: patient was previously denied an MRI (72148)
        but is now requesting Pembrolizumab (J9271). These are unrelated.
        """
        case = make_case(procedure_code="J9271")
        flags = self.detector.evaluate(
            case,
            prior_denial_codes=["72148"],  # Different procedure, different denial
        )

        assert EscalationReason.PRIOR_DENIAL_SAME_SERVICE not in flags.reasons

    def test_no_prior_denials_does_not_trigger(self) -> None:
        """
        Branch 7 edge case: no prior denial history should never trigger.
        This is the common case for new patients.
        """
        case = make_case()
        flags = self.detector.evaluate(case, prior_denial_codes=[])

        assert EscalationReason.PRIOR_DENIAL_SAME_SERVICE not in flags.reasons

    # ── Multi-flag tests ──────────────────────────────────────────────────────

    def test_multiple_flags_can_fire_simultaneously(self) -> None:
        """
        A case with multiple risk factors should trigger all applicable branches.

        Real-world meaning: a pediatric patient (handled via high-cost/complexity
        in classification) with a CAR-T therapy request AND a prior denial
        should trigger both EXPERIMENTAL_TREATMENT and PRIOR_DENIAL_SAME_SERVICE.
        This test ensures flags accumulate, not that only the first match fires.
        """
        case = make_case(
            procedure_code="Q2041",  # Experimental CAR-T therapy
            diagnosis_code="C91.0",  # Acute lymphoblastic leukemia (pediatric common)
        )
        flags = self.detector.evaluate(
            case,
            prior_denial_codes=["Q2041"],  # Also has a prior denial for same procedure
        )

        assert EscalationReason.EXPERIMENTAL_TREATMENT in flags.reasons
        assert EscalationReason.PRIOR_DENIAL_SAME_SERVICE in flags.reasons
        assert len(flags.reasons) >= 2


# =============================================================================
# Orchestrator integration tests — full 7-branch routing
# =============================================================================


class TestOrchestratorEscalationTree:
    """
    Integration tests for the full Orchestrator escalation logic.

    These tests mock the Decision Agent and Medical Director Agent to
    isolate the Orchestrator's routing logic from actual LLM calls.
    They verify that the correct status is returned for each branch.
    """

    def make_orchestrator_with_mocks(
        self,
        tier1_confidence: float = 0.97,
        tier1_status: AuthorizationStatus = AuthorizationStatus.AUTO_APPROVED,
        tier2_confidence: float = 0.97,
    ) -> Orchestrator:
        """
        Create an Orchestrator with mocked agents.

        Returns:
            Orchestrator with decision_agent and medical_director_agent mocked.
        """
        orchestrator = Orchestrator()

        # Mock the Tier 1 agent
        orchestrator.decision_agent.run = AsyncMock(  # type: ignore[method-assign]
            return_value=make_decision(
                status=tier1_status,
                confidence=tier1_confidence,
            )
        )

        # Mock the Tier 2 agent
        orchestrator.medical_director_agent.run = AsyncMock(  # type: ignore[method-assign]
            return_value=make_decision(
                status=AuthorizationStatus.AUTO_APPROVED,
                confidence=tier2_confidence,
            )
        )

        # Mock triage agents so process_decision does not attempt real API calls
        from pacca.models import ClassificationOutput, EvidenceOutput, UrgencyLevel

        orchestrator.evidence_agent.run = AsyncMock(  # type: ignore[method-assign]
            return_value=EvidenceOutput(
                clinical_narrative="", key_findings=[], evidence_gaps=[], confidence_score=0.9
            )
        )
        orchestrator.classification_agent.run = AsyncMock(  # type: ignore[method-assign]
            return_value=ClassificationOutput(
                complexity=1,
                complexity_factors=[],
                primary_specialty="general",
                urgency=UrgencyLevel.ROUTINE,
                routing_rationale="",
                confidence_score=0.9,
            )
        )

        return orchestrator

    # ── Branch 1: Auto-approve ────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_branch1_high_confidence_auto_approves(self) -> None:
        """
        Branch 1: confidence >= 0.95 with AUTO_APPROVED status returns immediately.

        The Medical Director Agent must NOT be called — it would be wasteful
        and is unnecessary when the Frontline Agent is highly confident.
        """
        orchestrator = self.make_orchestrator_with_mocks(
            tier1_confidence=0.97,
            tier1_status=AuthorizationStatus.AUTO_APPROVED,
        )
        ctx = DecisionContext(case=make_case(), relevant_guidelines="NCCN guidelines support.")
        result = await orchestrator.process_decision(ctx)

        assert result.status == AuthorizationStatus.AUTO_APPROVED
        # Medical Director should NOT have been called
        orchestrator.medical_director_agent.run.assert_not_called()  # type: ignore[attr-defined]

    # ── Branch 2: Medical Director escalation ────────────────────────────────

    @pytest.mark.asyncio
    async def test_branch2_ambiguous_confidence_calls_medical_director(self) -> None:
        """
        Branch 2: confidence between 0.90 and 0.95 must invoke Medical Director.
        """
        orchestrator = self.make_orchestrator_with_mocks(
            tier1_confidence=0.92,  # In the 0.90-0.95 ambiguous zone
            tier1_status=AuthorizationStatus.IN_REVIEW,
            tier2_confidence=0.97,  # MD is confident → approve
        )
        ctx = DecisionContext(case=make_case(), relevant_guidelines="Some guidelines.")
        result = await orchestrator.process_decision(ctx)

        # Medical Director MUST have been called for the ambiguous case
        orchestrator.medical_director_agent.run.assert_called_once()  # type: ignore[attr-defined]
        assert result.status == AuthorizationStatus.AUTO_APPROVED

    @pytest.mark.asyncio
    async def test_branch2_md_low_confidence_routes_to_human_review(self) -> None:
        """
        Branch 2 variant: if the Medical Director is also uncertain (< 0.95),
        the case goes to human review — not auto-approved.
        """
        orchestrator = self.make_orchestrator_with_mocks(
            tier1_confidence=0.92,
            tier1_status=AuthorizationStatus.IN_REVIEW,
            tier2_confidence=0.88,  # MD also uncertain
        )
        # Override MD mock to return low confidence
        orchestrator.medical_director_agent.run = AsyncMock(  # type: ignore[method-assign]
            return_value=make_decision(
                status=AuthorizationStatus.IN_REVIEW,
                confidence=0.88,
            )
        )
        ctx = DecisionContext(case=make_case(), relevant_guidelines="Ambiguous guidelines.")
        result = await orchestrator.process_decision(ctx)

        assert result.status == AuthorizationStatus.IN_REVIEW

    # ── Branch 3: Low confidence ──────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_branch3_low_confidence_routes_to_human_review(self) -> None:
        """
        Branch 3: confidence < 0.90 routes to human review queue.

        The Medical Director Agent must NOT be called — confidence is too
        low for even a second AI opinion to be meaningful.
        """
        orchestrator = self.make_orchestrator_with_mocks(
            tier1_confidence=0.72,  # Below the 0.90 threshold
            tier1_status=AuthorizationStatus.IN_REVIEW,
        )
        ctx = DecisionContext(case=make_case(), relevant_guidelines="Insufficient guidelines.")
        result = await orchestrator.process_decision(ctx)

        assert result.status == AuthorizationStatus.IN_REVIEW
        orchestrator.medical_director_agent.run.assert_not_called()  # type: ignore[attr-defined]

    # ── Branches 4-7: Pre-flight escalation ──────────────────────────────────

    @pytest.mark.asyncio
    async def test_branch4_experimental_treatment_bypasses_llm(self) -> None:
        """
        Branch 4: experimental procedure must route to IN_REVIEW without calling
        either agent. No LLM call should happen — this is a policy decision.
        """
        orchestrator = self.make_orchestrator_with_mocks()
        experimental_case = make_case(procedure_code="Q2041")  # CAR-T therapy
        ctx = DecisionContext(
            case=experimental_case,
            relevant_guidelines="CAR-T therapy guidelines.",
        )
        result = await orchestrator.process_decision(ctx)

        assert result.status == AuthorizationStatus.IN_REVIEW
        # Neither agent should have been called — pre-flight short-circuits
        orchestrator.decision_agent.run.assert_not_called()  # type: ignore[attr-defined]
        orchestrator.medical_director_agent.run.assert_not_called()  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_branch5_rare_condition_bypasses_llm(self) -> None:
        """
        Branch 5: rare disease diagnosis routes to IN_REVIEW without LLM calls.
        Gaucher disease (E75.22) must not receive an autonomous AI decision.
        """
        orchestrator = self.make_orchestrator_with_mocks()
        rare_case = make_case(diagnosis_code="E75.22")  # Gaucher disease
        ctx = DecisionContext(case=rare_case, relevant_guidelines="Rare disease guidelines.")
        result = await orchestrator.process_decision(ctx)

        assert result.status == AuthorizationStatus.IN_REVIEW
        orchestrator.decision_agent.run.assert_not_called()  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_branch6_conflicting_guidelines_bypasses_llm(self) -> None:
        """
        Branch 6: conflicting guidelines route to IN_REVIEW without LLM calls.
        """
        orchestrator = self.make_orchestrator_with_mocks()
        ctx = DecisionContext(
            case=make_case(),
            relevant_guidelines=(
                "NCCN: Treatment is recommended and evidence-based. "
                "CMS: Treatment is not recommended for this indication."
            ),
        )
        result = await orchestrator.process_decision(ctx)

        assert result.status == AuthorizationStatus.IN_REVIEW
        orchestrator.decision_agent.run.assert_not_called()  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_branch7_prior_denial_bypasses_llm(self) -> None:
        """
        Branch 7: prior denial for same service routes to IN_REVIEW without LLM calls.
        """
        orchestrator = self.make_orchestrator_with_mocks()
        ctx = DecisionContext(case=make_case(procedure_code="J9271"), relevant_guidelines="...")
        result = await orchestrator.process_decision(
            ctx,
            prior_denial_codes=["J9271"],  # Same procedure was previously denied
        )

        assert result.status == AuthorizationStatus.IN_REVIEW
        orchestrator.decision_agent.run.assert_not_called()  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_pre_flight_rationale_is_descriptive(self) -> None:
        """
        Pre-flight escalations must include a rationale explaining exactly
        which check triggered and why.

        Real-world meaning: the human reviewer who receives this case needs
        to know immediately what to look for. 'Routed to review' is useless.
        'Pre-flight escalation: experimental treatment — Q2041 is on the
        experimental procedure list' is actionable.
        """
        orchestrator = self.make_orchestrator_with_mocks()
        experimental_case = make_case(procedure_code="Q2041")
        ctx = DecisionContext(case=experimental_case, relevant_guidelines="...")
        result = await orchestrator.process_decision(ctx)

        assert "pre-flight" in result.rationale.lower()
        assert "Q2041" in result.rationale

    @pytest.mark.asyncio
    async def test_clean_case_proceeds_to_agent_evaluation(self) -> None:
        """
        A case with no risk flags must proceed to normal agent evaluation.

        This test verifies that the pre-flight check does NOT accidentally
        over-escalate clean cases. A standard NSCLC pembrolizumab request
        with no risk factors should reach the Decision Agent normally.
        """
        orchestrator = self.make_orchestrator_with_mocks(
            tier1_confidence=0.97,
            tier1_status=AuthorizationStatus.AUTO_APPROVED,
        )
        clean_case = make_case(
            procedure_code="J9271",  # Standard, non-experimental
            diagnosis_code="C34.1",  # Common NSCLC
            evidence_text="PD-L1 >= 50%, no prior treatment, standard of care.",
        )
        ctx = DecisionContext(
            case=clean_case,
            relevant_guidelines="NCCN: pembrolizumab recommended Category 1.",
        )
        result = await orchestrator.process_decision(ctx)

        # Decision Agent must have been called — no pre-flight triggers
        orchestrator.decision_agent.run.assert_called_once()  # type: ignore[attr-defined]
        assert result.status == AuthorizationStatus.AUTO_APPROVED

    # ── Kill switch: enable_autonomous_decisions = False ─────────────────────

    @pytest.mark.asyncio
    async def test_kill_switch_forces_human_review_regardless_of_confidence(self) -> None:
        """
        Task 7 — Kill switch: when enable_autonomous_decisions is False, a case
        that would normally auto-approve (Tier-1 returns 0.99 confidence /
        AUTO_APPROVED and no pre-flight trigger fires) must end with IN_REVIEW.

        Real-world meaning: an operator sets this flag during an incident,
        regulatory audit, or system hold.  Every case that reaches the
        confidence-routing stage must be redirected to the human review queue
        regardless of how confident the Tier-1 agent was.  The Tier-1 agent
        still runs (its rationale is preserved for the human reviewer); it
        is only the autonomous approval that is blocked.

        The override is cleaned up in a try/finally so it cannot leak into
        other tests even if this test raises.
        """
        orchestrator = self.make_orchestrator_with_mocks(
            tier1_confidence=0.99,  # Would normally auto-approve (>= 0.95)
            tier1_status=AuthorizationStatus.AUTO_APPROVED,
        )
        ctx = DecisionContext(
            case=make_case(
                procedure_code="J9271",  # Standard, non-experimental — no pre-flight
                diagnosis_code="C34.1",
                evidence_text="PD-L1 >= 50%, no prior treatment, standard of care.",
            ),
            relevant_guidelines="NCCN: pembrolizumab recommended Category 1.",
        )

        try:
            apply_overrides({"enable_autonomous_decisions": False})
            result = await orchestrator.process_decision(ctx)
        finally:
            clear_all_overrides()

        # Kill switch must have redirected to human review despite high confidence.
        assert result.status == AuthorizationStatus.IN_REVIEW
        # Tier-1 agent must still have been called (rationale preserved for reviewer).
        orchestrator.decision_agent.run.assert_called_once()  # type: ignore[attr-defined]
        # Medical Director must NOT have been called — kill switch returns early.
        orchestrator.medical_director_agent.run.assert_not_called()  # type: ignore[attr-defined]
