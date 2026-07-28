"""
Clinical accuracy test suite — Week 4 evaluation framework.

This is the CI gate. If clinical accuracy drops below 80%, this suite
fails and the pipeline stops. This is the machine-enforceable contract
that PACCA reasons correctly, not just that it runs.

Teaching note — two kinds of tests in this file:

  FAST TESTS (no API calls, no mocks, pure Python):
    - Dataset integrity: golden_cases.py is structured correctly
    - Coverage verification: all escalation branches are represented
    - Case metadata validation: required fields are populated
    These run in milliseconds and are part of the standard unit suite.

  SLOW TESTS (@pytest.mark.clinical, real API calls):
    - Full pipeline evaluation against golden dataset
    - LLM-as-judge scoring of agent reasoning quality
    - CI gate assertion: accuracy >= MINIMUM_ACCEPTABLE_ACCURACY
    These take 2-5 minutes and run in the nightly CI pipeline.

  Separation by marker lets developers run fast tests locally and
  expensive tests in CI without waiting 5 minutes on every commit.

  Run fast tests:   pytest tests/clinical/test_clinical_accuracy.py -m "not clinical"
  Run full suite:   pytest tests/clinical/ -m clinical
  Run everything:   pytest tests/

Teaching note — why the CI gate threshold is 80% and not 100%:

  LLM outputs are inherently probabilistic. Even a correct, well-designed
  system will occasionally produce suboptimal reasoning on ambiguous cases.
  Setting the bar at 100% would cause the CI to fail on noise — random
  variation in LLM outputs that doesn't reflect actual system quality.

  80% means: "at least 16 of 20 cases must be handled correctly." This
  threshold was chosen to be high enough to catch real degradation while
  tolerating natural LLM variance on edge cases. In a production system,
  you would tune this threshold based on observed baseline performance.
"""

import os
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.clinical.adult_complexity_cases import ADULT_COMPLEXITY_CASES
from tests.clinical.ambiguous_completeness_cases import AMBIGUOUS_COMPLETENESS_CASES
from tests.clinical.cardiology_cases import CARDIOLOGY_CASES
from tests.clinical.denial_cases import DENIAL_CASES
from tests.clinical.depth_extension_cases import DEPTH_EXTENSION_CASES
from tests.clinical.endocrinology_cases import ENDOCRINOLOGY_CASES
from tests.clinical.evaluator import (
    MINIMUM_ACCEPTABLE_ACCURACY,
    ClinicalEvaluator,
    JudgeVerdict,
)
from tests.clinical.expansion_cases import EXPANSION_CASES
from tests.clinical.geriatric_cases import GERIATRIC_CASES
from tests.clinical.golden_cases import (
    GOLDEN_CASES,
    EscalationBranch,
    ExpectedOutcome,
    GoldenCase,
    get_cases_by_branch,
    get_dataset_summary,
    get_hallucination_trap_cases,
)
from tests.clinical.hematology_cases import HEMATOLOGY_CASES
from tests.clinical.holdout import HELD_OUT_CASE_IDS, held_out_cases
from tests.clinical.mental_health_cases import MENTAL_HEALTH_CASES
from tests.clinical.near_miss_cases import NEAR_MISS_CASES
from tests.clinical.neurology_cases import NEUROLOGY_CASES
from tests.clinical.ob_cases import OB_CASES
from tests.clinical.oncology_breadth_cases import ONCOLOGY_BREADTH_CASES
from tests.clinical.oncology_depth_cases import ONCOLOGY_DEPTH_CASES
from tests.clinical.pediatric_cases import PEDIATRIC_CASES
from tests.clinical.pulmonology_adult_cases import PULMONOLOGY_ADULT_CASES
from tests.clinical.transplant_cases import TRANSPLANT_CASES

# Aggregate of ALL supplementary case lists — used by the cross-list integrity
# check and full-pipeline evaluation. Add new list imports above and a
# corresponding entry below when a new case file lands.
ALL_SUPPLEMENTARY_LISTS: tuple[list[GoldenCase], ...] = (
    NEAR_MISS_CASES,
    PEDIATRIC_CASES,
    EXPANSION_CASES,
    DENIAL_CASES,
    CARDIOLOGY_CASES,
    MENTAL_HEALTH_CASES,
    GERIATRIC_CASES,
    PULMONOLOGY_ADULT_CASES,
    AMBIGUOUS_COMPLETENESS_CASES,
    TRANSPLANT_CASES,
    NEUROLOGY_CASES,
    OB_CASES,
    HEMATOLOGY_CASES,
    ENDOCRINOLOGY_CASES,
    ONCOLOGY_DEPTH_CASES,
    ONCOLOGY_BREADTH_CASES,
    DEPTH_EXTENSION_CASES,
    ADULT_COMPLEXITY_CASES,
)

# =============================================================================
# Dataset integrity tests — fast, no API calls
# These always run as part of the standard unit suite.
# =============================================================================


class TestGoldenDatasetIntegrity:
    """
    Verify the golden dataset is well-formed before running any evaluations.

    These tests catch errors in the golden_cases.py file itself — missing
    fields, duplicate IDs, empty rationales — that would make the
    evaluation results meaningless.
    """

    def test_dataset_has_twenty_cases(self) -> None:
        """The golden dataset must have exactly 20 cases."""
        assert len(GOLDEN_CASES) == 20, (
            f"Expected 20 golden cases, found {len(GOLDEN_CASES)}. "
            "The dataset size is part of the Week 4 specification."
        )

    def test_all_case_ids_are_unique(self) -> None:
        """No two cases may share a case_id."""
        ids = [c.case_id for c in GOLDEN_CASES]
        assert len(ids) == len(set(ids)), (
            f"Duplicate case IDs found: {[x for x in ids if ids.count(x) > 1]}"
        )

    def test_no_case_id_collisions_across_lists(self) -> None:
        """
        Case IDs (GC-NNN) are monotonically allocated across the entire
        dataset. NEAR_MISS, PEDIATRIC, and EXPANSION suites must not collide
        with GOLDEN or each other. This is the real invariant — per-list
        uniqueness is necessary but not sufficient.
        """
        all_ids = [
            c.case_id
            for c in (
                GOLDEN_CASES
                + NEAR_MISS_CASES
                + PEDIATRIC_CASES
                + EXPANSION_CASES
                + ADULT_COMPLEXITY_CASES
            )
        ]
        duplicates = sorted({x for x in all_ids if all_ids.count(x) > 1})
        assert not duplicates, (
            f"Cross-list duplicate case IDs found: {duplicates}. "
            "case_id is monotonic across all case files — see "
            "docs/CASE_AUTHORING_GUIDE.md § 8."
        )

    def test_expansion_dataset_has_eleven_cases(self) -> None:
        """
        EXPANSION_CASES = 8 iter-6 gap-closure cases + 3 cost-boundary cases
        (GC-073/074/075, iter-6 Batch F). Size is encoded as part of the spec
        so that drift (a case silently dropped or duplicated) is caught by
        integrity.
        """
        assert len(EXPANSION_CASES) == 11, (
            f"Expected 11 expansion cases, found {len(EXPANSION_CASES)}. "
            "See docs/CASE_PROVENANCE.md and docs/EVALUATION_COVERAGE.md."
        )

    def test_adult_complexity_dataset_has_three_cases(self) -> None:
        """
        ADULT_COMPLEXITY_CASES = 3 iter-6 chg-3 cases (GC-101 escalating,
        GC-102 must-not-escalate boundary, GC-103 geriatric escalating). Size is
        encoded so drift is caught by integrity.
        """
        assert len(ADULT_COMPLEXITY_CASES) == 3, (
            f"Expected 3 adult-complexity cases, found {len(ADULT_COMPLEXITY_CASES)}."
        )

    def test_all_cases_have_required_fields(self) -> None:
        """Every case must have non-empty required text fields."""
        for case in GOLDEN_CASES:
            assert case.title, f"{case.case_id}: title is empty"
            assert case.diagnosis_code, f"{case.case_id}: diagnosis_code is empty"
            assert case.procedure_code, f"{case.case_id}: procedure_code is empty"
            assert case.clinical_notes, f"{case.case_id}: clinical_notes is empty"
            assert case.guidelines_context, f"{case.case_id}: guidelines_context is empty"
            assert case.clinical_rationale, f"{case.case_id}: clinical_rationale is empty"
            assert case.judge_scoring_criteria, f"{case.case_id}: judge_scoring_criteria is empty"

    def test_all_cases_have_expected_outcome(self) -> None:
        """Every case must specify an expected outcome."""
        for case in GOLDEN_CASES:
            assert isinstance(case.expected_outcome, ExpectedOutcome), (
                f"{case.case_id}: expected_outcome must be an ExpectedOutcome enum"
            )

    def test_all_cases_have_reasoning_must_include(self) -> None:
        """
        Every case must specify at least one keyword the rationale must include.
        Cases without reasoning requirements cannot detect shallow reasoning.
        """
        for case in GOLDEN_CASES:
            assert len(case.reasoning_must_include) >= 1, (
                f"{case.case_id}: reasoning_must_include is empty. "
                "Every case must specify at least one required reasoning keyword."
            )

    def test_escalation_branch_coverage(self) -> None:
        """
        The dataset must cover all 7 escalation branches.
        Missing a branch means we have no evaluation coverage for it.
        """
        covered_branches = {c.expected_branch for c in GOLDEN_CASES}

        required_branches = {
            EscalationBranch.BRANCH_1_AUTO_APPROVE,
            EscalationBranch.BRANCH_3_LOW_CONFIDENCE,
            EscalationBranch.BRANCH_4_EXPERIMENTAL,
            EscalationBranch.BRANCH_5_RARE,
            EscalationBranch.BRANCH_6_CONFLICTING,
            EscalationBranch.BRANCH_7_PRIOR_DENIAL,
        }

        missing = required_branches - covered_branches
        assert not missing, (
            f"Golden dataset missing coverage for branches: "
            f"{[b.value for b in missing]}. "
            f"Every escalation branch must have at least one test case."
        )

    def test_outcome_distribution_is_balanced(self) -> None:
        """
        The dataset should not be dominated by one outcome type.
        A dataset that's 90% AUTO_APPROVED doesn't test denial or escalation.
        """
        summary = get_dataset_summary()
        outcomes = summary["by_outcome"]

        # No single outcome should represent more than 60% of cases
        for outcome, count in outcomes.items():
            percentage = count / summary["total_cases"]
            assert percentage <= 0.60, (
                f"Outcome '{outcome}' represents {percentage:.0%} of cases "
                f"({count}/{summary['total_cases']}). "
                f"Dataset is unbalanced — no single outcome should exceed 60%."
            )

    def test_hallucination_trap_cases_exist(self) -> None:
        """
        The dataset must include cases specifically designed to catch hallucination.
        These are the most important safety tests in the suite.
        """
        traps = get_hallucination_trap_cases()
        assert len(traps) >= 2, (
            f"Found only {len(traps)} hallucination trap cases. "
            "At least 2 cases should be designed to catch hallucination."
        )

    def test_prior_denial_cases_have_prior_denial_codes(self) -> None:
        """
        Cases expecting Branch 7 (prior denial) must have prior_denial_codes set.
        Without this, the pre-flight check cannot fire.
        """
        branch7_cases = get_cases_by_branch(EscalationBranch.BRANCH_7_PRIOR_DENIAL)
        for case in branch7_cases:
            assert len(case.prior_denial_codes) > 0, (
                f"{case.case_id}: Branch 7 case must have prior_denial_codes set. "
                "The pre-flight check requires prior denial codes to match against."
            )

    def test_pre_flight_cases_use_correct_codes(self) -> None:
        """
        Cases expecting Branch 4 (experimental) should use codes from
        EXPERIMENTAL_PROCEDURE_CODES or have experimental keywords in notes.
        """
        from pacca.agents.clinical_risk_detector import EXPERIMENTAL_PROCEDURE_CODES

        branch4_cases = get_cases_by_branch(EscalationBranch.BRANCH_4_EXPERIMENTAL)
        for case in branch4_cases:
            code_match = case.procedure_code.upper() in EXPERIMENTAL_PROCEDURE_CODES
            keyword_match = any(
                kw in case.clinical_notes.lower()
                for kw in ["clinical trial", "investigational", "phase", "experimental"]
            )
            assert code_match or keyword_match, (
                f"{case.case_id}: Branch 4 case has neither an experimental procedure "
                f"code ({case.procedure_code}) nor experimental keywords in notes. "
                "The pre-flight check cannot trigger without one of these."
            )


# =============================================================================
# Escalation pre-flight integration tests — fast, mocked
# Verify that pre-flight checks fire correctly for the golden cases.
# =============================================================================


class TestPreFlightOnGoldenCases:
    """
    Verify that ClinicalRiskDetector correctly handles the golden dataset
    pre-flight cases. These use no LLM calls.
    """

    def test_experimental_cases_trigger_pre_flight(self) -> None:
        """Branch 4 golden cases must trigger pre-flight escalation."""
        from pacca.agents.clinical_risk_detector import ClinicalRiskDetector
        from pacca.models.clinical import ClinicalCase, EvidenceItem
        from pacca.models.enums import EscalationReason, EvidenceSourceType

        detector = ClinicalRiskDetector()
        branch4_cases = get_cases_by_branch(EscalationBranch.BRANCH_4_EXPERIMENTAL)

        for golden in branch4_cases:
            clinical_case = ClinicalCase(
                patient_id="P-TEST",
                primary_diagnosis_code=golden.diagnosis_code,
                procedure_code=golden.procedure_code,
                evidence=[
                    EvidenceItem(
                        id="e1",
                        source_type=EvidenceSourceType.CLINICAL_NOTE,
                        description=golden.clinical_notes[:200],
                        original_text=golden.clinical_notes,
                        confidence=0.9,
                    )
                ],
            )
            flags = detector.evaluate(
                case=clinical_case,
                guidelines_context=golden.guidelines_context,
            )
            assert flags.should_pre_escalate, (
                f"{golden.case_id}: Expected pre-flight escalation for "
                f"Branch 4 (experimental treatment), but no flags triggered. "
                f"Procedure code: {golden.procedure_code}"
            )
            assert EscalationReason.EXPERIMENTAL_TREATMENT in flags.reasons, (
                f"{golden.case_id}: Expected EXPERIMENTAL_TREATMENT reason."
            )

    def test_rare_condition_cases_trigger_pre_flight(self) -> None:
        """Branch 5 golden cases must trigger pre-flight escalation."""
        from pacca.agents.clinical_risk_detector import ClinicalRiskDetector
        from pacca.models.clinical import ClinicalCase
        from pacca.models.enums import EscalationReason

        detector = ClinicalRiskDetector()
        branch5_cases = get_cases_by_branch(EscalationBranch.BRANCH_5_RARE)

        for golden in branch5_cases:
            clinical_case = ClinicalCase(
                patient_id="P-TEST",
                primary_diagnosis_code=golden.diagnosis_code,
                procedure_code=golden.procedure_code,
                evidence=[],
            )
            flags = detector.evaluate(clinical_case)
            assert flags.should_pre_escalate, (
                f"{golden.case_id}: Expected rare condition pre-flight for "
                f"diagnosis {golden.diagnosis_code}"
            )
            assert EscalationReason.RARE_CONDITION in flags.reasons

    def test_prior_denial_cases_trigger_pre_flight(self) -> None:
        """Branch 7 golden cases must trigger pre-flight escalation."""
        from pacca.agents.clinical_risk_detector import ClinicalRiskDetector
        from pacca.models.clinical import ClinicalCase
        from pacca.models.enums import EscalationReason

        detector = ClinicalRiskDetector()
        branch7_cases = get_cases_by_branch(EscalationBranch.BRANCH_7_PRIOR_DENIAL)

        for golden in branch7_cases:
            clinical_case = ClinicalCase(
                patient_id="P-TEST",
                primary_diagnosis_code=golden.diagnosis_code,
                procedure_code=golden.procedure_code,
                evidence=[],
            )
            flags = detector.evaluate(
                clinical_case,
                prior_denial_codes=golden.prior_denial_codes,
            )
            assert flags.should_pre_escalate, (
                f"{golden.case_id}: Expected prior denial pre-flight for "
                f"procedure {golden.procedure_code} with prior denials "
                f"{golden.prior_denial_codes}"
            )
            assert EscalationReason.PRIOR_DENIAL_SAME_SERVICE in flags.reasons

    def test_conflicting_guidelines_cases_trigger_pre_flight(self) -> None:
        """Branch 6 golden cases must trigger pre-flight escalation."""
        from pacca.agents.clinical_risk_detector import ClinicalRiskDetector
        from pacca.models.clinical import ClinicalCase
        from pacca.models.enums import EscalationReason

        detector = ClinicalRiskDetector()
        branch6_cases = get_cases_by_branch(EscalationBranch.BRANCH_6_CONFLICTING)

        for golden in branch6_cases:
            clinical_case = ClinicalCase(
                patient_id="P-TEST",
                primary_diagnosis_code=golden.diagnosis_code,
                procedure_code=golden.procedure_code,
                evidence=[],
            )
            flags = detector.evaluate(
                clinical_case,
                guidelines_context=golden.guidelines_context,
            )
            assert flags.should_pre_escalate, (
                f"{golden.case_id}: Expected conflicting guidelines pre-flight."
            )
            assert EscalationReason.CONFLICTING_GUIDELINES in flags.reasons


# =============================================================================
# LLM-as-judge unit tests — fast, mocked judge
# Verify evaluator logic without real API calls.
# =============================================================================


class TestEvaluatorLogic:
    """
    Unit tests for the ClinicalEvaluator class.
    All API calls are mocked — these run instantly.
    """

    def make_mock_evaluator(self) -> ClinicalEvaluator:
        """Create an evaluator with a mocked Anthropic client."""
        evaluator = ClinicalEvaluator(api_key="test-key")
        evaluator.client = AsyncMock()
        return evaluator

    def mock_judge_response(self, score: int, correct: bool, hallucination: bool) -> MagicMock:
        """Build a mock Anthropic response with a judge verdict."""
        import json

        verdict = {
            "score": score,
            "correct_outcome": correct,
            "hallucination_detected": hallucination,
            "missing_citations": [],
            "judge_reasoning": f"Test score {score} — mock verdict.",
        }
        mock_content = MagicMock()
        mock_content.text = json.dumps(verdict)

        mock_response = MagicMock()
        mock_response.content = [mock_content]
        return mock_response

    @pytest.mark.asyncio
    async def test_evaluate_case_returns_verdict(self) -> None:
        """evaluate_case() must return a JudgeVerdict for any valid case."""
        evaluator = self.make_mock_evaluator()
        evaluator.client.messages.create = AsyncMock(  # type: ignore[method-assign,unused-ignore]
            return_value=self.mock_judge_response(score=4, correct=True, hallucination=False)
        )

        case = GOLDEN_CASES[0]
        verdict = await evaluator.evaluate_case(
            case=case,
            system_decision_status="AUTO_APPROVED",
            system_rationale="NCCN Category 1 for PD-L1 >= 50%.",
            system_confidence=0.97,
        )

        assert isinstance(verdict, JudgeVerdict)
        assert verdict.case_id == case.case_id
        assert verdict.score == 4
        assert verdict.passed is True
        assert verdict.correct_outcome is True

    @pytest.mark.asyncio
    async def test_score_below_threshold_marks_failed(self) -> None:
        """A score of 2 must mark the verdict as failed."""
        evaluator = self.make_mock_evaluator()
        evaluator.client.messages.create = AsyncMock(  # type: ignore[method-assign,unused-ignore]
            return_value=self.mock_judge_response(score=2, correct=False, hallucination=True)
        )

        verdict = await evaluator.evaluate_case(
            case=GOLDEN_CASES[0],
            system_decision_status="AUTO_APPROVED",
            system_rationale="The PD-L1 TPS was 85% which exceeds the threshold.",
            system_confidence=0.97,
        )

        assert verdict.passed is False
        assert verdict.hallucination_detected is True

    @pytest.mark.asyncio
    async def test_json_parse_failure_returns_score_1(self) -> None:
        """
        If the judge returns non-JSON, the evaluator must return a score of 1
        rather than raising an exception. Evaluation errors must not crash CI.
        """
        evaluator = self.make_mock_evaluator()
        mock_content = MagicMock()
        mock_content.text = "I cannot evaluate this case."
        mock_response = MagicMock()
        mock_response.content = [mock_content]
        evaluator.client.messages.create = AsyncMock(return_value=mock_response)  # type: ignore[method-assign,unused-ignore]

        verdict = await evaluator.evaluate_case(
            case=GOLDEN_CASES[0],
            system_decision_status="AUTO_APPROVED",
            system_rationale="Some rationale.",
            system_confidence=0.97,
        )

        assert verdict.score == 1
        assert verdict.passed is False

    def test_compile_report_calculates_accuracy(self) -> None:
        """compile_report() must correctly calculate accuracy from verdicts."""
        evaluator = self.make_mock_evaluator()

        verdicts = [
            JudgeVerdict("GC-001", 5, True, "Excellent", True, False, []),
            JudgeVerdict("GC-002", 4, True, "Good", True, False, []),
            JudgeVerdict("GC-003", 3, True, "Acceptable", True, False, []),
            JudgeVerdict("GC-004", 2, False, "Wrong", False, False, []),
            JudgeVerdict("GC-005", 1, False, "Critical", False, True, []),
        ]

        report = evaluator.compile_report(verdicts)

        assert report.total_cases == 5
        assert report.passed_cases == 3  # Scores 3, 4, 5 pass
        assert abs(report.accuracy - 0.6) < 0.01
        assert report.hallucinations == ["GC-005"]
        assert "GC-004" in report.failed_cases
        assert "GC-005" in report.failed_cases

    def test_compile_report_ci_gate_passes_at_threshold(self) -> None:
        """Report must pass CI gate when accuracy == MINIMUM_ACCEPTABLE_ACCURACY."""
        evaluator = self.make_mock_evaluator()
        n = 10
        threshold_passes = int(n * MINIMUM_ACCEPTABLE_ACCURACY)

        verdicts = [
            JudgeVerdict(f"GC-{i:03}", 4, True, "Good", True, False, [])
            for i in range(threshold_passes)
        ] + [
            JudgeVerdict(f"GC-{i:03}", 1, False, "Bad", False, False, [])
            for i in range(threshold_passes, n)
        ]

        report = evaluator.compile_report(verdicts)
        assert report.passed_ci_gate is True

    def test_compile_report_ci_gate_fails_below_threshold(self) -> None:
        """Report must fail CI gate when accuracy < MINIMUM_ACCEPTABLE_ACCURACY."""
        evaluator = self.make_mock_evaluator()
        verdicts = [
            JudgeVerdict(f"GC-{i:03}", 1, False, "Bad", False, False, []) for i in range(10)
        ]
        report = evaluator.compile_report(verdicts)
        assert report.passed_ci_gate is False


# =============================================================================
# Full clinical evaluation — @pytest.mark.clinical
# These make REAL API calls and take 2-5 minutes.
# Run with: pytest tests/clinical/ -m clinical
# ─────────────────────────────────────────────────────────────────────────────
# CI pipeline configuration:
#   Nightly: pytest tests/ -m clinical
#   Pre-commit: pytest tests/ -m "not clinical"
# =============================================================================

if TYPE_CHECKING:
    from pacca.agents.clinical_risk_detector import ClinicalRiskDetector
    from pacca.agents.decision import DecisionAgent


async def _run_cases_through_pipeline(
    cases: list[GoldenCase],
    detector: "ClinicalRiskDetector",
    agent: "DecisionAgent",
    evaluator: ClinicalEvaluator,
) -> list[JudgeVerdict]:
    """
    Run each case through pre-flight -> Decision Agent -> LLM judge — exactly
    what THE CI GATE does per-case. Shared by
    `test_full_pipeline_meets_accuracy_threshold` (the golden-set gate) and
    `test_held_out_accuracy_report` (the held-out accuracy report) so the two
    evaluation paths run identical pipeline logic and cannot silently drift
    apart from each other.
    """
    from pacca.agents.decision import DecisionContext
    from pacca.models.clinical import ClinicalCase, EvidenceItem
    from pacca.models.enums import AuthorizationStatus, EvidenceSourceType

    verdicts: list[JudgeVerdict] = []
    for golden in cases:
        clinical_case = ClinicalCase(
            patient_id=f"P-EVAL-{golden.case_id}",
            primary_diagnosis_code=golden.diagnosis_code,
            procedure_code=golden.procedure_code,
            evidence=[
                EvidenceItem(
                    id="e1",
                    source_type=EvidenceSourceType.CLINICAL_NOTE,
                    description=golden.clinical_notes[:200],
                    original_text=golden.clinical_notes,
                    confidence=0.9,
                )
            ],
        )

        # Run pre-flight checks
        flags = detector.evaluate(
            case=clinical_case,
            guidelines_context=golden.guidelines_context,
            prior_denial_codes=golden.prior_denial_codes,
        )

        if flags.should_pre_escalate:
            # Pre-flight fired — build the pre-flight decision
            status = AuthorizationStatus.IN_REVIEW.value
            rationale = (
                f"Pre-flight escalation triggered. "
                f"Reasons: {[r.value for r in flags.reasons]}. "
                f"Details: {flags.details}"
            )
            confidence = 0.0
        else:
            # Run through Decision Agent
            try:
                ctx = DecisionContext(
                    case=clinical_case,
                    relevant_guidelines=golden.guidelines_context,
                )
                decision = await agent.run(ctx)
                status = decision.status.value
                rationale = decision.rationale
                confidence = decision.confidence_score
            except Exception as exc:
                status = "ERROR"
                rationale = f"Agent failed: {exc!s}"
                confidence = 0.0

        # Judge the decision
        verdict = await evaluator.evaluate_case(
            case=golden,
            system_decision_status=status,
            system_rationale=rationale,
            system_confidence=confidence,
        )
        verdicts.append(verdict)

        # Print per-case result for CI log visibility
        result_icon = "✓" if verdict.passed else "✗"
        print(
            f"  {result_icon} {golden.case_id}: score={verdict.score} "
            f"status={status} correct={verdict.correct_outcome}"
        )

    return verdicts


@pytest.mark.clinical
class TestFullClinicalEvaluation:
    """
    End-to-end clinical evaluation against the full golden dataset.

    IMPORTANT: These tests make real calls to the Anthropic API.
    They are marked @pytest.mark.clinical and run separately from
    the fast unit tests.

    Skip these tests locally with: pytest -m "not clinical"
    Run them in CI with: pytest -m clinical
    """

    @pytest.mark.asyncio
    async def test_dataset_summary_is_logged(self) -> None:
        """
        Log the dataset summary at the start of clinical evaluation.
        This provides context in CI logs for understanding test coverage.
        """
        summary = get_dataset_summary()
        print(f"\nGolden Dataset Summary: {summary}")
        assert summary["total_cases"] == 20

    @pytest.mark.asyncio
    async def test_full_pipeline_meets_accuracy_threshold(self) -> None:
        """
        THE CI GATE: Run all 20 golden cases through the full PACCA pipeline
        and verify the system meets the MINIMUM_ACCEPTABLE_ACCURACY threshold.

        This test:
          1. Runs each golden case through ClinicalRiskDetector (pre-flight)
          2. For cases that pass pre-flight, runs through the Decision Agent
          3. Passes the decision + rationale to the LLM judge
          4. Compiles results and asserts accuracy >= 80%

        FAILURE of this test means: the system's reasoning quality has degraded.
        It does NOT mean a unit test broke — it means the AI is producing
        worse decisions than the minimum acceptable standard.
        """
        if not os.getenv("ANTHROPIC_API_KEY"):
            pytest.skip("ANTHROPIC_API_KEY not set — skipping live evaluation")

        from pacca.agents.clinical_risk_detector import ClinicalRiskDetector
        from pacca.agents.decision import DecisionAgent

        detector = ClinicalRiskDetector()
        agent = DecisionAgent()
        evaluator = ClinicalEvaluator()

        # GOLDEN_CASES (20) + NEAR_MISS_CASES (iter-2 chg-3 memory-trap siblings)
        # + PEDIATRIC_CASES (iter-5 chg-2 — complexity-score model validation set)
        # + EXPANSION_CASES (iter-6 — gap-closure suite per EVALUATION_COVERAGE.md).
        # All supplementary lists run through the same judge but are kept separate
        # — the `len == 20` integrity assertion above still holds for GOLDEN_CASES.
        # + ADULT_COMPLEXITY_CASES (iter-6 chg-3 — adult pre-flight validation set).
        # None of these ids overlap HELD_OUT_CASE_IDS — see tests/clinical/holdout.py.
        verdicts = await _run_cases_through_pipeline(
            GOLDEN_CASES
            + NEAR_MISS_CASES
            + PEDIATRIC_CASES
            + EXPANSION_CASES
            + ADULT_COMPLEXITY_CASES,
            detector,
            agent,
            evaluator,
        )

        # Compile final report
        report = evaluator.compile_report(verdicts)
        print(f"\n{report.summary()}")

        if report.hallucinations:
            print(f"\nHallucinations detected in: {report.hallucinations}")

        if report.failed_cases:
            print(f"Failed cases: {report.failed_cases}")
            for v in report.verdicts:
                if not v.passed:
                    print(f"  {v.case_id} (score {v.score}): {v.judge_reasoning}")

        # THE CI GATE — this assertion is what makes this a gate, not just a report
        assert report.passed_ci_gate, (
            f"\n{'=' * 60}\n"
            f"CLINICAL ACCURACY CI GATE FAILED\n"
            f"{'=' * 60}\n"
            f"Accuracy: {report.accuracy:.1%} "
            f"(required: {MINIMUM_ACCEPTABLE_ACCURACY:.0%})\n"
            f"Passed: {report.passed_cases}/{report.total_cases} cases\n"
            f"Failed cases: {report.failed_cases}\n"
            f"Hallucinations: {report.hallucinations}\n"
            f"{'=' * 60}\n"
            f"This failure means the system's clinical reasoning quality has "
            f"degraded below the minimum acceptable standard. Review the failed "
            f"cases above and the agent prompts in agents/decision.py."
        )

    @pytest.mark.asyncio
    async def test_zero_hallucinations_on_sparse_cases(self) -> None:
        """
        Hallucination trap cases must produce zero hallucinations.

        This test runs ONLY the hallucination trap cases (GC-018, GC-019)
        to verify the system doesn't invent clinical details when notes
        are sparse. Hallucination in a healthcare system is a patient
        safety event — zero tolerance is the correct standard.
        """
        if not os.getenv("ANTHROPIC_API_KEY"):
            pytest.skip("ANTHROPIC_API_KEY not set — skipping live evaluation")

        from pacca.agents.decision import DecisionAgent, DecisionContext
        from pacca.models.clinical import ClinicalCase, EvidenceItem
        from pacca.models.enums import EvidenceSourceType

        agent = DecisionAgent()
        evaluator = ClinicalEvaluator()
        traps = get_hallucination_trap_cases()
        verdicts: list[JudgeVerdict] = []

        for golden in traps:
            clinical_case = ClinicalCase(
                patient_id=f"P-TRAP-{golden.case_id}",
                primary_diagnosis_code=golden.diagnosis_code,
                procedure_code=golden.procedure_code,
                evidence=[
                    EvidenceItem(
                        id="e1",
                        source_type=EvidenceSourceType.CLINICAL_NOTE,
                        description=golden.clinical_notes,
                        original_text=golden.clinical_notes,
                        confidence=0.5,
                    )
                ],
            )

            try:
                ctx = DecisionContext(
                    case=clinical_case, relevant_guidelines=golden.guidelines_context
                )
                decision = await agent.run(ctx)
                status = decision.status.value
                rationale = decision.rationale
                confidence = decision.confidence_score
            except Exception as exc:
                status = "ERROR"
                rationale = f"Agent failed: {exc!s}"
                confidence = 0.0

            verdict = await evaluator.evaluate_case(
                case=golden,
                system_decision_status=status,
                system_rationale=rationale,
                system_confidence=confidence,
            )
            verdicts.append(verdict)

        hallucinated = [v.case_id for v in verdicts if v.hallucination_detected]

        assert len(hallucinated) == 0, (
            f"\nHALLUCINATION DETECTED — ZERO TOLERANCE VIOLATION\n"
            f"Cases with hallucination: {hallucinated}\n"
            f"Hallucination in a healthcare AI system means the agent invented "
            f"clinical details not present in the submission. This is a patient "
            f"safety issue and must be fixed before any production deployment.\n"
            f"Review agents/decision.py system prompt — add explicit instruction: "
            f"'Only reference clinical information explicitly present in the notes.'"
        )


# =============================================================================
# Held-out (out-of-sample) evaluation — @pytest.mark.holdout, NOT clinical
# =============================================================================


@pytest.mark.holdout
class TestHeldOutEvaluation:
    """
    Out-of-sample accuracy reporting. Its own class with its own marker, and
    that separation is deliberate — three reasons, in ascending importance:

    1. It is not a gate. The only assertion below is a wiring sanity check,
       so under `-m clinical` it cost ~7.5 min of live API calls on every
       qualifying PR and could never block one. Measured 2026-07-28: the
       full clinical target ran 18m33s, of which this test was 7m25s (40%).

    2. It is the wrong instrument for regression detection. A merge gate
       answers "did this change break something" — that is per-case
       comparison against a baseline (`regression_gate.py`). Aggregate
       accuracy over 32 cases has poor power for small drops; per
       `docs/STATISTICAL_POWER.md`, n≈13 detects a *20-point* drop.

    3. THE REAL REASON: running a holdout per-merge destroys it. Every
       behavioural PR would print exactly which held-out cases failed, and
       the natural, well-intentioned response is to tune a prompt until they
       pass. That is contamination — the precise failure this holdout exists
       to measure — arriving through the one channel the contamination guard
       (`tests/unit/test_eval_holdout_guard.py`) structurally cannot see: a
       human reading CI output. A holdout's value comes from being consulted
       rarely.

    Run deliberately via `make test-holdout`, or on the nightly schedule.
    NEVER add this marker to a per-merge selector.
    """

    @pytest.mark.asyncio
    async def test_held_out_accuracy_report(self) -> None:
        """
        NOT A CI GATE. Runs PACCA's real pipeline over the 32-case declared
        holdout (`tests/clinical/holdout.py::HELD_OUT_CASE_IDS`) and reports
        `accuracy_held_out` — the live, out-of-sample accuracy number, since
        none of these 32 cases are referenced under `src/pacca/` or included
        in the golden-set gate.

        Deliberately UNTHRESHOLDED: promoting this to a blocking gate is a
        policy decision taken deliberately, not by this test. Mirrors the
        repo's warn-then-enforce precedent (P-4 scope guard: warn at iter-8,
        enforce at iter-9). See `docs/EVALUATION.md`.

        First recorded reading, 2026-07-28: 87.5% (28/32); Wilson 95% CI
        71.9%–95.0%; failures GC-037, GC-044, GC-048, GC-065; zero score-1
        (no hallucination out-of-sample). Not directly comparable to the
        golden-set figure — that set includes 5 pre-flight cases decided
        deterministically with no model call, this one has none.

        The only assertion below is a wiring sanity check (every declared
        held-out case was actually evaluated, none dropped or
        double-counted) — it is not an accuracy-quality gate.
        """
        if not os.getenv("ANTHROPIC_API_KEY"):
            pytest.skip("ANTHROPIC_API_KEY not set — skipping live evaluation")

        from pacca.agents.clinical_risk_detector import ClinicalRiskDetector
        from pacca.agents.decision import DecisionAgent

        detector = ClinicalRiskDetector()
        agent = DecisionAgent()
        evaluator = ClinicalEvaluator()

        verdicts = await _run_cases_through_pipeline(held_out_cases(), detector, agent, evaluator)
        report = evaluator.compile_report(verdicts)

        print(f"\n{report.summary()}")
        print(
            f"\nHeld-out accuracy (out-of-sample, NOT a CI gate): "
            f"{report.accuracy_held_out:.1%} "
            f"({report.held_out_passed}/{report.held_out_total} cases)"
        )

        # Wiring sanity check ONLY — confirms every declared held-out case
        # was actually evaluated. NOT an accuracy threshold; see docstring.
        assert report.held_out_total == len(HELD_OUT_CASE_IDS), (
            f"Expected all {len(HELD_OUT_CASE_IDS)} held-out cases to be "
            f"evaluated, got {report.held_out_total}. A case may have been "
            f"dropped or double-counted — check held_out_cases() in "
            f"tests/clinical/holdout.py."
        )
