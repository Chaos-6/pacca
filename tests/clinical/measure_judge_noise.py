"""
Measure the LLM-as-judge's disagreement with itself, live.

WHY THIS EXISTS
---------------
`regression_gate.py` flags a case when its score drops by
REGRESSION_DROP_THRESHOLD (1) against a stored baseline. That threshold has
never been justified against a measurement. It was chosen when the SDK still
transmitted `temperature=0.0` and the captured distributions were identical
pairs -- `[5, 5]`, `[4, 4]` in iter-6-baseline.json -- which reads like
determinism but is two draws that happened to agree.

`capture_baseline.py`'s docstring records swings of GC-017 2->4 and GC-005
5->2, observed *while* temperature=0.0 was still being sent, and attributes
them to the judge. The parameter is now gone entirely (the SDK removed it), so
nothing even nominally pins sampling.

FIRST RESULT (2026-09-06, run 34050063869), which did not go as expected: at
20 cases x 3 runs the judge disagreed with itself ZERO times, while re-running
the whole pipeline moved 3 of 20 by one point each. The variance is the
agent's. `check_regression`'s noise_threshold=1 recommendation absorbs exactly
that one point, so the setting is right -- but it is tolerating agent
instability, not judge noise, and those call for different responses.

`judge_stability.py` was built to answer exactly this and has only ever run
against a stubbed judge -- its docstring notes "this lane never constructs a
live client". This module is the live runner it was missing.

WHAT IT MEASURES, AND WHAT IT DOES NOT
--------------------------------------
The pipeline runs ONCE per case; the judge then scores that one frozen
(status, rationale, confidence) tuple N times. So this isolates *judge*
variance with the agent held fixed.

That is deliberately only half the picture. The baseline records an end-to-end
score, whose spread is judge variance *plus* agent variance. Get that from
`capture_baseline.py --rollouts N`, which re-runs the whole pipeline per
rollout and stores per-case `distributions`. The two together decompose the
total, which is what says whether a threshold should be relaxed (judge noise)
or whether the agent itself is unstable on a case (agent noise) -- different
problems with different fixes.

REPORTING, NOT A GATE. It asserts nothing. Its output is evidence for choosing
a threshold; it does not choose one.

    ANTHROPIC_API_KEY=... python -m tests.clinical.measure_judge_noise \
        --runs 5 --out judge-noise.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from tests.clinical.judge_stability import compile_stability_report, run_stability_check


async def measure(runs: int, limit: int | None) -> dict[str, Any]:
    """Run the pipeline once per case, then score each decision `runs` times."""
    from pacca.agents.clinical_risk_detector import ClinicalRiskDetector
    from pacca.agents.decision import DecisionAgent
    from tests.clinical.capture_baseline import run_pipeline_for_case
    from tests.clinical.evaluator import ClinicalEvaluator
    from tests.clinical.holdout import IN_SAMPLE_CASES

    detector = ClinicalRiskDetector()
    agent = DecisionAgent()
    evaluator = ClinicalEvaluator()

    # The same set capture_baseline uses. Subtracting judge variance from
    # end-to-end variance is only meaningful if both measured the same cases.
    cases = IN_SAMPLE_CASES[:limit] if limit else IN_SAMPLE_CASES
    results = []

    for golden in cases:
        status, rationale, confidence = await run_pipeline_for_case(golden, detector, agent)
        result = await run_stability_check(
            evaluator=evaluator,
            case=golden,
            system_decision_status=status,
            system_rationale=rationale,
            system_confidence=confidence,
            n=runs,
        )
        results.append(result)
        spread = max(result.scores) - min(result.scores)
        print(
            f"  {golden.case_id}: scores={result.scores} spread={spread} "
            f"modal={result.modal_score}"
            f"{' BAND-CROSSING' if result.band_crossing else ''}"
        )

    report = compile_stability_report(results)

    # The spread is the number that bears directly on the threshold: if any
    # case's judge-only spread reaches N, a drop-of-N gate cannot distinguish a
    # regression from the judge scoring the identical text twice.
    spreads = {r.case_id: max(r.scores) - min(r.scores) for r in results}
    max_spread = max(spreads.values()) if spreads else 0

    return {
        "runs_per_case": runs,
        "cases_measured": len(results),
        "disagreement_rate": report.disagreement_rate,
        "band_crossing_rate": report.band_crossing_rate,
        "fabrication_disagreement_rate": report.fabrication_disagreement_rate,
        "max_judge_only_spread": max_spread,
        "spreads": spreads,
        "scores": {r.case_id: r.scores for r in results},
        "fabrication_flags": {r.case_id: r.fabrication_flags for r in results},
    }


def render(summary: dict[str, Any]) -> str:
    """Human-readable summary. Pure -- unit-testable without an API key."""
    lines = [
        "",
        "=" * 62,
        f"JUDGE-ONLY NOISE — {summary['cases_measured']} cases x {summary['runs_per_case']} runs",
        "=" * 62,
        f"  disagreement rate:        {summary['disagreement_rate']:.1%}"
        "   (cases where the N runs did not all agree)",
        f"  band-crossing rate:       {summary['band_crossing_rate']:.1%}"
        "   (cases that flipped across the pass mark)",
        f"  fabrication disagreement: {summary['fabrication_disagreement_rate']:.1%}",
        f"  max judge-only spread:    {summary['max_judge_only_spread']} points",
        "",
        "Read against REGRESSION_DROP_THRESHOLD = 1: a threshold at or below",
        "the max spread cannot separate a regression from the judge rescoring",
        "identical text. This measures the judge alone -- end-to-end spread,",
        "which is what the baseline stores, also carries agent variance and is",
        "measured by capture_baseline.py --rollouts N.",
        "=" * 62,
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Measure live LLM-as-judge self-disagreement.")
    ap.add_argument("--runs", type=int, default=5, help="judge runs per case (default 5)")
    ap.add_argument("--out", help="write the summary as JSON to this path")
    ap.add_argument("--limit", type=int, help="measure only the first N golden cases")
    args = ap.parse_args()

    if args.runs < 2:
        raise SystemExit(f"--runs must be >= 2 to observe disagreement; got {args.runs}")

    summary = asyncio.run(measure(args.runs, args.limit))
    print(render(summary))

    if args.out:
        Path(args.out).write_text(json.dumps(summary, indent=2) + "\n")
        print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
