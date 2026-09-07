"""
Capture a per-case baseline scoreboard from a live clinical evaluation run.

Run this ONCE at the commit you want to baseline (e.g. harness-iter-1 HEAD),
with ANTHROPIC_API_KEY set. It runs all golden cases through the real PACCA
pipeline + judge, then writes {case_id: score} to a baseline file that the
per-case regression gate (regression_gate.py) compares future runs against:

    ANTHROPIC_API_KEY=... python -m tests.clinical.capture_baseline \
        --tag harness-iter-1 \
        --out tests/clinical/baselines/iter-1-baseline.json

iter-3 chg-3 adds --rollouts N (default 1). When set greater than 1, each
case runs N times and the saved file includes:
  - scores: per-case median across the N rollouts (the canonical value the
            regression gate compares future runs against)
  - distributions: per-case list of all N scores (visible variance)

Use --rollouts 2 for production baselines once a cycle has observed any
run-to-run non-determinism. The iter-2 GC-017 2->4 swing + iter-3 chg-1
GC-005 5->2 swing make 2 the recommended minimum.

Attribution note, twice revised. Those swings were first recorded as
LLM-as-judge variance. A measurement at 20 cases x 3 rollouts (2026-09-06,
run 34050063869) found ZERO judge disagreement against 3 of 20 cases moving
end-to-end, and was read as: the movement is the AGENT, not the judge.

Run 34070093157 (2026-09-07) retires that reading. It is the first capture
over the 42 cases the gate actually scores. Judge-only: 7.1% disagreement,
7.1% band-crossing, max spread 2. End-to-end: 6 of 42 moved, max spread 3,
GC-021 scoring [5, 5, 5, 2, 5]. Both the judge and the agent vary, and both
vary more than the 20-case sample suggested.

The caveat written into the first note named its own failure: three draws
cannot prove a judge deterministic. Neither can twenty cases chosen without
regard to what the gate scores. Those 20 were whichever ones capture_baseline
happened to iterate, and they are the stable ones -- so the sample that
understated coverage understated variance by the same act.

Rollouts remain the right instrument either way. What the baseline stores is
the median of N draws, and that is far steadier than any single draw. Rollouts remain the right instrument
either way; they capture whichever it is.

WHY A SCRIPT, NOT A FIXTURE
---------------------------
Capturing a baseline is a deliberate, occasional act tied to a known-good
commit — not something that should happen implicitly on every test run. Keeping
it an explicit command prevents accidentally "baselining" a regression (which
would silence the very gate it feeds).

The pipeline loop below mirrors `test_full_pipeline_meets_accuracy_threshold`
in test_clinical_accuracy.py exactly, so the captured scores match what the CI
gate sees. The heavy imports (agents, Anthropic SDK) are deferred into
run_golden_dataset() so this module stays importable — and the save path stays
unit-testable — without an API key.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
from collections import defaultdict
from typing import TYPE_CHECKING, Any

from tests.clinical.regression_gate import save_baseline, scores_from_verdicts

if TYPE_CHECKING:
    from pathlib import Path


async def run_pipeline_for_case(
    golden: Any,
    detector: Any,
    agent: Any,
) -> tuple[str, str, float]:
    """
    Run ONE golden case through pre-flight + the decision agent.

    Returns the (status, rationale, confidence) tuple that the judge is asked
    to score. Extracted from run_golden_dataset() so that measure_judge_noise
    can reuse the exact production path instead of restating it: this loop has
    to mirror `test_full_pipeline_meets_accuracy_threshold` for captured scores
    to mean anything, and a second hand-copied version of it would drift from
    the first the moment either is edited.

    Types are `Any` for the same reason the imports in run_golden_dataset are
    function-local -- this module stays importable, and unit-testable, without
    the Anthropic SDK installed.
    """
    from pacca.agents.decision import DecisionContext
    from pacca.models.clinical import ClinicalCase, EvidenceItem
    from pacca.models.enums import AuthorizationStatus, EvidenceSourceType

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

    flags = detector.evaluate(
        case=clinical_case,
        guidelines_context=golden.guidelines_context,
        prior_denial_codes=golden.prior_denial_codes,
    )

    if flags.should_pre_escalate:
        return (
            AuthorizationStatus.IN_REVIEW.value,
            (
                f"Pre-flight escalation triggered. "
                f"Reasons: {[r.value for r in flags.reasons]}. "
                f"Details: {flags.details}"
            ),
            0.0,
        )

    try:
        ctx = DecisionContext(case=clinical_case, relevant_guidelines=golden.guidelines_context)
        decision = await agent.run(ctx)
    except Exception as exc:
        return "ERROR", f"Agent failed: {exc!s}", 0.0
    return decision.status.value, decision.rationale, decision.confidence_score


async def run_golden_dataset() -> list[Any]:
    """
    Run every in-sample case through the real pipeline + judge; return verdicts.

    Faithful to the live CI test, and that fidelity is the whole point: a
    captured score is only comparable to a gate run if it describes the same
    cases. This iterated GOLDEN_CASES (20) while the gate evaluates
    IN_SAMPLE_CASES (42), so 22 of the gate's cases had no baseline and every
    baseline ever captured covered less than half of what it was compared
    against. Both now read one definition (holdout.IN_SAMPLE_CASES).

    Heavy imports are local to this function on purpose (see module docstring).
    """
    from pacca.agents.clinical_risk_detector import ClinicalRiskDetector
    from pacca.agents.decision import DecisionAgent
    from tests.clinical.evaluator import ClinicalEvaluator
    from tests.clinical.holdout import IN_SAMPLE_CASES

    detector = ClinicalRiskDetector()
    agent = DecisionAgent()
    evaluator = ClinicalEvaluator()
    verdicts: list[Any] = []

    for golden in IN_SAMPLE_CASES:
        status, rationale, confidence = await run_pipeline_for_case(golden, detector, agent)

        verdict = await evaluator.evaluate_case(
            case=golden,
            system_decision_status=status,
            system_rationale=rationale,
            system_confidence=confidence,
        )
        verdicts.append(verdict)
        print(f"  {'PASS' if verdict.passed else 'FAIL'} {golden.case_id}: score={verdict.score}")

    return verdicts


def write_baseline_from_verdicts(
    verdicts: list[Any],
    out_path: str | Path,
    iteration_tag: str,
    distributions: dict[str, list[int]] | None = None,
) -> Path:
    """
    Pure, testable tail of the flow: turn verdicts into a saved scoreboard.

    Separated from run_golden_dataset() so it can be unit-tested with synthetic
    verdicts and no API key.

    iter-3 chg-3: optional `distributions` arg is forwarded to save_baseline
    when multi-rollout data is available.
    """
    return save_baseline(
        scores_from_verdicts(verdicts),
        out_path,
        iteration_tag=iteration_tag,
        distributions=distributions,
    )


def _median_score(scores: list[int]) -> int:
    """Median of a list of scores. Half-points round down (integer rubric)."""
    return int(statistics.median(scores))


async def _amain(out_path: str, tag: str, rollouts: int) -> None:
    if rollouts < 1:
        raise SystemExit(f"--rollouts must be >= 1; got {rollouts}")

    if rollouts == 1:
        # Single-pass path: unchanged from the iter-2 implementation.
        verdicts = await run_golden_dataset()
        path = write_baseline_from_verdicts(verdicts, out_path, tag)
        print(f"\nWrote baseline ({len(verdicts)} cases) -> {path}")
        return

    # iter-3 chg-3 multi-rollout path: run the dataset N times, aggregate by
    # case_id, store the median + the full distribution per case.
    print(f"Running {rollouts} rollouts of the full golden dataset...")
    per_case_scores: dict[str, list[int]] = defaultdict(list)
    final_verdicts: list[Any] = []
    for i in range(1, rollouts + 1):
        print(f"\n--- Rollout {i}/{rollouts} ---")
        verdicts = await run_golden_dataset()
        for v in verdicts:
            per_case_scores[v.case_id].append(int(v.score))
        # Keep the last rollout's verdicts as the seed for save_baseline;
        # their scores will be overwritten with medians below.
        final_verdicts = verdicts

    # Overwrite each verdict's score with the median across rollouts.
    distributions = dict(per_case_scores)
    for v in final_verdicts:
        v.score = _median_score(per_case_scores[v.case_id])
        v.passed = v.score >= 3  # MINIMUM_PASSING_SCORE

    print(f"\n--- Aggregate (median of {rollouts} rollouts) ---")
    for cid, scores in sorted(distributions.items()):
        median = _median_score(scores)
        print(f"  {cid}: scores={scores} median={median}")

    path = write_baseline_from_verdicts(final_verdicts, out_path, tag, distributions=distributions)
    print(f"\nWrote baseline ({len(final_verdicts)} cases, {rollouts} rollouts) -> {path}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Capture a per-case baseline scoreboard.")
    ap.add_argument("--tag", required=True, help="harness iteration tag, e.g. harness-iter-1")
    ap.add_argument("--out", required=True, help="output path for the baseline JSON")
    ap.add_argument(
        "--rollouts",
        type=int,
        default=1,
        help="number of independent rollouts per case (iter-3 chg-3; default 1; "
        "recommended 2+ for production baselines to expose LLM-as-judge variance)",
    )
    args = ap.parse_args()
    asyncio.run(_amain(args.out, args.tag, args.rollouts))


if __name__ == "__main__":
    main()
