"""
Deterministic, stubbed-judge test for the judge stability harness (chg-25 / D5 §3.1, §4.6).

Zero live API calls (D5 verification bar: "Stub the judge everywhere,
including the stability harness"). This test stubs `evaluator.client.
messages.create` with a KNOWN-VARYING sequence of scores per case, then
proves `run_stability_check()` + `compile_stability_report()` compute the
disagreement rate, band-crossing rate, and fabrication-disagreement rate
correctly against that known sequence — i.e. it exercises the harness's own
arithmetic, not any real judge's behavior.

Reachable via `make test-judge-stability` (see Makefile) — that target runs
this exact file, so it never makes a live call either.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from tests.clinical.evaluator import MINIMUM_PASSING_SCORE, ClinicalEvaluator
from tests.clinical.golden_cases import GOLDEN_CASES
from tests.clinical.judge_stability import (
    CaseStabilityResult,
    compile_stability_report,
    run_stability_check,
)


def _responses(scores: list[int], fabrications: list[bool]) -> list[MagicMock]:
    """Build a sequence of mock Anthropic responses, one per (score, fabrication) pair."""
    out = []
    for score, fabrication in zip(scores, fabrications, strict=True):
        content = MagicMock()
        content.text = json.dumps(
            {
                "score": score,
                "fabrication_detected": fabrication,
                "missing_citations": [],
                "judge_reasoning": f"stubbed score {score}",
            }
        )
        response = MagicMock()
        response.content = [content]
        out.append(response)
    return out


def test_disagreement_rate_and_band_crossing_computed_correctly_over_known_sequence() -> None:
    """
    Case A: scores [2, 4, 2, 4, 2] -- disagreement (not all equal) AND
        band-crossing (2 < 3 <= 4, so some runs fail and some pass).
    Case B: scores [5, 5, 5, 5, 5] -- perfect agreement, no band-crossing.

    Expected over these 2 cases: disagreement_rate == 0.5, band_crossing_rate == 0.5.
    """
    case_a, case_b = GOLDEN_CASES[0], GOLDEN_CASES[1]

    evaluator = ClinicalEvaluator(api_key="test-key")
    evaluator.client = AsyncMock()
    evaluator.client.messages.create = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            *_responses([2, 4, 2, 4, 2], [False, False, False, False, False]),
            *_responses([5, 5, 5, 5, 5], [False, False, False, False, False]),
        ]
    )

    async def _run() -> tuple[CaseStabilityResult, CaseStabilityResult]:
        result_a = await run_stability_check(
            evaluator, case_a, "AUTO_APPROVED", "rationale A", 0.9, n=5
        )
        result_b = await run_stability_check(
            evaluator, case_b, "AUTO_APPROVED", "rationale B", 0.9, n=5
        )
        return result_a, result_b

    result_a, result_b = asyncio.run(_run())

    assert result_a.scores == [2, 4, 2, 4, 2]
    assert result_a.disagreement is True
    assert result_a.band_crossing is True
    assert result_a.modal_score == 2

    assert result_b.scores == [5, 5, 5, 5, 5]
    assert result_b.disagreement is False
    assert result_b.band_crossing is False
    assert result_b.modal_score == 5

    report = compile_stability_report([result_a, result_b])
    assert report.disagreement_rate == 0.5
    assert report.band_crossing_rate == 0.5
    assert report.fabrication_disagreement_rate == 0.0
    assert "Disagreement rate" in report.summary()


def test_within_band_disagreement_is_not_band_crossing() -> None:
    """Scores [4, 5, 4, 5, 4] disagree (not all equal) but never cross
    MINIMUM_PASSING_SCORE -- disagreement without band-crossing must be
    distinguishable, since band-crossing is the number that matters most."""
    assert MINIMUM_PASSING_SCORE == 3  # pin the threshold this test relies on
    result = CaseStabilityResult(
        case_id="GC-TEST", scores=[4, 5, 4, 5, 4], fabrication_flags=[False] * 5
    )
    assert result.disagreement is True
    assert result.band_crossing is False


def test_fabrication_flag_disagreement_detected_independently_of_score() -> None:
    """Constant score but a flipping fabrication_detected flag must be caught
    by fabrication_disagreement_rate even though score-based disagreement is
    False for that case."""
    result = CaseStabilityResult(
        case_id="GC-TEST",
        scores=[5, 5, 5, 5, 5],
        fabrication_flags=[False, True, False, False, False],
    )
    assert result.disagreement is False
    assert result.fabrication_agreement is False

    report = compile_stability_report([result])
    assert report.disagreement_rate == 0.0
    assert report.fabrication_disagreement_rate == 1.0


def test_empty_results_report_zero_rates_not_a_crash() -> None:
    report = compile_stability_report([])
    assert report.disagreement_rate == 0.0
    assert report.band_crossing_rate == 0.0
    assert report.fabrication_disagreement_rate == 0.0
