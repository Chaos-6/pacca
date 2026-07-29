"""
Investigate a single golden case end-to-end: run it through the live pipeline,
capture the agent decision + judge verdict, and print everything verbatim
for diagnostic review.

Usage:
    set -a; source .env; set +a
    python -m tests.clinical.investigate_case GC-010

Unlike capture_baseline.py (which runs ALL cases and writes a scoreboard),
this runs ONE case and prints the full trace to stdout for human reading.
Use this when a case persistently fails and you need to root-cause it.

WHY A SCRIPT, NOT A FIXTURE
---------------------------
Mirrors the pipeline loop in test_full_pipeline_meets_accuracy_threshold
exactly, so the scores reproduce what the CI gate sees. Heavy imports are
deferred into investigate() so this module stays importable without an
API key (lets the dataset-integrity tests reach it).
"""

from __future__ import annotations

import argparse
import asyncio
import sys

SEP = "=" * 78
SUB = "-" * 78


async def investigate(case_id: str) -> int:  # noqa: PLR0912, PLR0915
    """
    Run one case end-to-end and print the full trace.

    Returns the judge's score (1-5) so this can be chained in a shell pipeline
    or asserted in a smoke test.

    The function is intentionally print-heavy (diagnostic CLI tool, not
    library code) so PLR0912 / PLR0915 are suppressed — the branches and
    statements are sections of the verbatim trace output, not logical
    complexity.
    """
    # Heavy imports deferred so this module is importable without an API key.
    from pacca.agents.clinical_risk_detector import ClinicalRiskDetector
    from pacca.agents.decision import DecisionAgent, DecisionContext
    from pacca.models.clinical import ClinicalCase, EvidenceItem
    from pacca.models.enums import AuthorizationStatus, EvidenceSourceType
    from tests.clinical.adult_complexity_cases import ADULT_COMPLEXITY_CASES
    from tests.clinical.denial_cases import DENIAL_CASES
    from tests.clinical.evaluator import ClinicalEvaluator
    from tests.clinical.expansion_cases import EXPANSION_CASES
    from tests.clinical.golden_cases import GOLDEN_CASES
    from tests.clinical.near_miss_cases import NEAR_MISS_CASES
    from tests.clinical.pediatric_cases import PEDIATRIC_CASES

    all_cases = (
        GOLDEN_CASES
        + NEAR_MISS_CASES
        + PEDIATRIC_CASES
        + EXPANSION_CASES
        + ADULT_COMPLEXITY_CASES
        + DENIAL_CASES
    )
    golden = next((c for c in all_cases if c.case_id == case_id), None)
    if golden is None:
        known = ", ".join(c.case_id for c in all_cases)
        print(f"ERROR: case_id {case_id!r} not found.\nKnown IDs: {known}")
        sys.exit(2)

    # ── Case definition ───────────────────────────────────────────────────────
    print(SEP)
    print(f"INVESTIGATING: {golden.case_id} — {golden.title}")
    print(SEP)
    print()
    print(f"Expected outcome  : {golden.expected_outcome.value}")
    expected_branch = golden.expected_branch.value if golden.expected_branch else "(none)"
    print(f"Expected branch   : {expected_branch}")
    print(f"Diagnosis code    : {golden.diagnosis_code}")
    print(f"Procedure code    : {golden.procedure_code}")
    print()
    print(SUB)
    print("CLINICAL NOTES (input to the agent)")
    print(SUB)
    print(golden.clinical_notes)
    print()
    print(SUB)
    print("GUIDELINES CONTEXT (input to the agent)")
    print(SUB)
    print(golden.guidelines_context)
    print()
    print(SUB)
    print("REASONING MUST INCLUDE (judge gate keywords)")
    print(SUB)
    for kw in golden.reasoning_must_include:
        print(f"  • {kw!r}")
    print()
    if golden.reasoning_must_not_include:
        print(SUB)
        print("REASONING MUST NOT INCLUDE")
        print(SUB)
        for kw in golden.reasoning_must_not_include:
            print(f"  • {kw!r}")
        print()
    print(SUB)
    print("JUDGE SCORING CRITERIA (rubric)")
    print(SUB)
    print(golden.judge_scoring_criteria)
    print()
    print(SUB)
    print("EXPERT CLINICAL RATIONALE (reference answer)")
    print(SUB)
    print(golden.clinical_rationale)
    print()

    # ── Pipeline execution ────────────────────────────────────────────────────
    print(SEP)
    print("PIPELINE EXECUTION")
    print(SEP)
    print()

    clinical_case = ClinicalCase(
        patient_id=f"P-INV-{golden.case_id}",
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

    detector = ClinicalRiskDetector()
    flags = detector.evaluate(
        case=clinical_case,
        guidelines_context=golden.guidelines_context,
        prior_denial_codes=golden.prior_denial_codes,
    )

    print(f"Pre-flight should_pre_escalate: {flags.should_pre_escalate}")
    # Only a real agent-produced AuthorizationDecision has cited_evidence_ids to
    # ground-check (Tier 2). Pre-flight escalations and agent failures have no
    # such object — evidence grounding is skipped for them ("where recorded").
    decision = None
    if flags.should_pre_escalate:
        print(f"  Reasons : {[r.value for r in flags.reasons]}")
        print(f"  Details : {flags.details}")
        status = AuthorizationStatus.IN_REVIEW.value
        rationale = (
            f"Pre-flight escalation triggered. "
            f"Reasons: {[r.value for r in flags.reasons]}. "
            f"Details: {flags.details}"
        )
        confidence = 0.0
    else:
        print("  (pre-flight did not fire; calling Decision Agent…)")
        print()
        agent = DecisionAgent()
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
            print(f"  AGENT FAILED: {exc!s}")
            status = "ERROR"
            rationale = f"Agent failed: {exc!s}"
            confidence = 0.0

    print()
    print(SUB)
    print("AGENT DECISION (full output)")
    print(SUB)
    print(f"Status     : {status}")
    print(f"Confidence : {confidence}")
    print()
    print("Rationale:")
    print(rationale)
    print()

    # ── Judge ─────────────────────────────────────────────────────────────────
    print(SEP)
    print("JUDGE EVALUATION")
    print(SEP)
    print()
    evaluator = ClinicalEvaluator()
    verdict = await evaluator.evaluate_case(
        case=golden,
        system_decision_status=status,
        system_rationale=rationale,
        system_confidence=confidence,
        decision=decision,
        clinical_case=clinical_case,
    )
    print(f"Score                  : {verdict.score}")
    print(f"Passed (≥3)            : {verdict.passed}")
    print(f"Correct outcome (exact): {verdict.correct_outcome}")
    print(f"Fabrication detected   : {verdict.fabrication_detected}")
    print(f"Constraint violation   : {verdict.constraint_check.has_violation}")
    if verdict.constraint_check.has_violation:
        print(f"  Missing (must-include)  : {verdict.constraint_check.missing or '(none)'}")
        print(
            f"  Forbidden present       : {verdict.constraint_check.forbidden_present or '(none)'}"
        )
    print(f"Ungrounded citations   : {verdict.ungrounded_citations or '(none)'}")
    print(f"Missing citations      : {verdict.missing_citations or '(none)'}")
    print()
    print(SUB)
    print("JUDGE REASONING (full text)")
    print(SUB)
    print(verdict.judge_reasoning)
    print()

    # ── Diagnosis hint ────────────────────────────────────────────────────────
    print(SEP)
    print("DIAGNOSIS HINT")
    print(SEP)
    if status == "ERROR":
        print("  → PIPELINE ERROR (agent threw an exception, see above)")
    elif not verdict.correct_outcome:
        print(
            f"  → WRONG OUTCOME: agent returned {status!r}, "
            f"expected {golden.expected_outcome.value!r}"
        )
    elif verdict.fabrication_detected:
        print("  → FABRICATION: agent invented clinical detail (see judge reasoning)")
    elif verdict.score == 1:
        print(
            "  → SCORE 1: per the rubric (evaluator.py lines 70–76), "
            "this is wrong-decision, hallucination, or invented clinical detail. "
            "Read the judge reasoning above to identify which."
        )
    elif verdict.score == 2:
        print(
            "  → SCORE 2: 'needs improvement'. Decision likely correct but reasoning "
            "is incomplete or weak vs the judge_scoring_criteria. Diff the rationale "
            "against reasoning_must_include keywords above to find what's missing."
        )
    elif verdict.score >= 3:
        print(f"  → SCORE {verdict.score}: passes the absolute gate.")
    print()
    return verdict.score


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run one golden case through the live pipeline + judge "
        "and dump everything verbatim. Use to root-cause a persistent failure."
    )
    parser.add_argument(
        "case_id",
        help="Case ID to investigate (e.g. GC-010). Accepts GOLDEN_CASES, NEAR_MISS_CASES, PEDIATRIC_CASES, EXPANSION_CASES, ADULT_COMPLEXITY_CASES, or DENIAL_CASES IDs.",
    )
    args = parser.parse_args()
    score = asyncio.run(investigate(args.case_id))
    # Exit non-zero if score < passing threshold, so this can be used in CI smoke
    sys.exit(0 if score >= 3 else 1)


if __name__ == "__main__":
    main()
