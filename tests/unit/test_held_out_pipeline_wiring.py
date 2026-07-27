"""
Proof that the held-out accuracy test actually evaluates the declared holdout.

Why this file exists
---------------------
`tests/clinical/test_clinical_accuracy.py::TestFullClinicalEvaluation::
test_held_out_accuracy_report` is a `@pytest.mark.clinical` test that makes
real Anthropic API calls, so it only runs via `make test-clinical` /
`make test-holdout`. Without a fast, mocked counterpart, nothing proves that
test actually iterates all 32 `HELD_OUT_CASE_IDS` and produces a non-empty
held-out denominator — the exact gap the Validator flagged (a 0/0 report
that would read "n/a" forever if the wiring were wrong, silently).

This test calls the SAME `_run_cases_through_pipeline()` helper the live
clinical test calls, over the SAME `held_out_cases()` list, with the
pre-flight detector and LLM judge faked out so it runs in milliseconds with
zero network calls and no `ANTHROPIC_API_KEY` — as part of the standard
`make test` suite, so a future refactor that breaks the held-out wiring
fails fast instead of only being discoverable by manually running the
5-minute live gate.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from pacca.agents.clinical_risk_detector import EscalationFlags
from pacca.models.enums import EscalationReason
from tests.clinical.evaluator import ClinicalEvaluator, JudgeVerdict
from tests.clinical.holdout import HELD_OUT_CASE_IDS, held_out_cases
from tests.clinical.test_clinical_accuracy import _run_cases_through_pipeline


def test_held_out_pipeline_wiring_covers_all_32_cases_with_no_api_calls() -> None:
    """
    Fakes the pre-flight detector (always escalates, so DecisionAgent.run is
    never reached) and the LLM judge (returns a synthetic passing verdict),
    then runs the real `_run_cases_through_pipeline()` helper over
    `held_out_cases()` and asserts: all 32 declared ids were processed
    exactly once, with no duplicates or omissions, and
    `compile_report()`'s held-out denominator is non-empty (not the 0/0 the
    live gate produces today, since it never touches HELD_OUT_CASE_IDS).
    """
    cases = held_out_cases()
    assert len(cases) == len(HELD_OUT_CASE_IDS) == 32

    fake_detector = MagicMock()
    fake_detector.evaluate.return_value = EscalationFlags(
        reasons=[EscalationReason.CONFIDENCE_BELOW_THRESHOLD],
        details={"proof": "pre-flight faked to always escalate; DecisionAgent never called"},
    )

    fake_agent = MagicMock()
    fake_agent.run = AsyncMock(
        side_effect=AssertionError(
            "DecisionAgent.run should never be called in this proof — "
            "pre-flight is faked to always escalate"
        )
    )

    evaluator = ClinicalEvaluator(api_key="unused-for-this-proof")

    async def fake_evaluate_case(
        case: object,
        system_decision_status: str,
        system_rationale: str,
        system_confidence: float,
    ) -> JudgeVerdict:
        return JudgeVerdict(
            case_id=case.case_id,  # type: ignore[attr-defined]
            score=4,
            passed=True,
            judge_reasoning="synthetic verdict for wiring proof — no live API call",
            correct_outcome=True,
            hallucination_detected=False,
            missing_citations=[],
        )

    evaluator.evaluate_case = fake_evaluate_case  # type: ignore[method-assign]

    verdicts = asyncio.run(_run_cases_through_pipeline(cases, fake_detector, fake_agent, evaluator))

    processed_ids = {v.case_id for v in verdicts}
    assert len(verdicts) == 32, f"Expected 32 verdicts, got {len(verdicts)}"
    assert processed_ids == HELD_OUT_CASE_IDS, (
        f"Missing: {sorted(HELD_OUT_CASE_IDS - processed_ids)}, "
        f"Unexpected: {sorted(processed_ids - HELD_OUT_CASE_IDS)}"
    )
    fake_agent.run.assert_not_called()

    report = evaluator.compile_report(verdicts)
    assert report.held_out_total == 32
    assert report.held_out_passed == 32
    assert report.accuracy_held_out == 1.0
    assert report.in_sample_total == 0, (
        "This run only submitted held-out cases; in_sample_total must stay 0"
    )

    print(f"\nWIRING PROOF (mocked, no live API calls):\n{report.summary()}")
