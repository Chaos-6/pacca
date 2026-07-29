"""
Judge stability harness (chg-25 / D5 §3.1).

Separating fabrication from constraint violations (chg-25's main fix) does
not, by itself, tell anyone how far the remaining judgment call (the 1-5
score + `fabrication_detected`) can be trusted. This harness measures that
directly by scoring the SAME (case, decision, rationale) tuple N times and
reporting how often the judge disagrees with itself.

REPORTING, NOT A GATE. `run_stability_check()` runs N repeated
`evaluate_case()` calls for one case; `compile_stability_report()` aggregates
across cases into disagreement / band-crossing / fabrication-disagreement
rates. Nothing here asserts a threshold — see docs/EVALUATION.md for why (the
same "warn before enforce" reasoning as the held-out accuracy report).

WHY A SEPARATE MODULE, NOT A MARKER ON THE EXISTING `holdout` TESTS
--------------------------------------------------------------------
`@pytest.mark.holdout` (tests/clinical/holdout.py, test_clinical_accuracy.py)
is a specific, already-load-bearing marker: `make test-holdout` hard-errors
without `ANTHROPIC_API_KEY` (Makefile), and `pyproject.toml`'s marker
docstring documents it as "live API, ~7.5 min", explicitly excluded from
`make test`. Reusing that marker for this harness's own deterministic,
stubbed-judge regression test would incorrectly bind it to the live-key-
required semantics of an unrelated live report. This harness's deterministic
test therefore carries NO special marker — it is fast, deterministic, zero
API calls, and runs as part of the standard suite — reachable in isolation
via `make test-judge-stability`. `run_stability_check()` itself is
evaluator-agnostic: pass it a real `ClinicalEvaluator` (live Anthropic
client) for an actual judge-noise measurement, or a stubbed one (as the
deterministic test does) to exercise the harness's own arithmetic with zero
network calls. This lane (chg-25) never constructs a live client.
"""

from __future__ import annotations

from dataclasses import dataclass

from .evaluator import MINIMUM_PASSING_SCORE, ClinicalEvaluator, GoldenCase


@dataclass
class CaseStabilityResult:
    """Raw per-run results for one case scored N times."""

    case_id: str
    scores: list[int]
    fabrication_flags: list[bool]

    @property
    def modal_score(self) -> int:
        """The most frequent score across the N runs (ties broken arbitrarily
        but deterministically by `set` iteration + `max`)."""
        return max(set(self.scores), key=self.scores.count)

    @property
    def disagreement(self) -> bool:
        """True if the N runs did not all produce the identical score."""
        return len(set(self.scores)) > 1

    @property
    def band_crossing(self) -> bool:
        """
        True if some runs passed (score >= MINIMUM_PASSING_SCORE) and others
        did not -- the pass/fail flip that matters most (D5 §3.1), because
        it is where judge noise becomes a CI-gate flip rather than harmless
        score jitter within the same pass/fail band.
        """
        bands = {s >= MINIMUM_PASSING_SCORE for s in self.scores}
        return len(bands) > 1

    @property
    def fabrication_agreement(self) -> bool:
        """True if all N runs agreed on `fabrication_detected`."""
        return len(set(self.fabrication_flags)) <= 1


@dataclass
class StabilityReport:
    """Aggregate stability measurements across a set of cases."""

    results: list[CaseStabilityResult]

    @property
    def disagreement_rate(self) -> float:
        """Fraction of cases where the N runs did not all agree on score."""
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.disagreement) / len(self.results)

    @property
    def band_crossing_rate(self) -> float:
        """Fraction of cases where runs landed on both sides of
        MINIMUM_PASSING_SCORE -- the number that matters most."""
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.band_crossing) / len(self.results)

    @property
    def fabrication_disagreement_rate(self) -> float:
        """Fraction of cases where fabrication_detected did not agree across
        all N runs."""
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if not r.fabrication_agreement) / len(self.results)

    def summary(self) -> str:
        lines = [
            f"Judge stability report over {len(self.results)} case(s):",
            f"  Disagreement rate (score varies):       {self.disagreement_rate:.0%}",
            f"  Band-crossing rate (pass/fail flips):   {self.band_crossing_rate:.0%}",
            f"  Fabrication-flag disagreement rate:     {self.fabrication_disagreement_rate:.0%}",
        ]
        for r in self.results:
            lines.append(
                f"    {r.case_id}: scores={r.scores} modal={r.modal_score} "
                f"disagreement={r.disagreement} band_crossing={r.band_crossing} "
                f"fabrication_flags={r.fabrication_flags}"
            )
        return "\n".join(lines)


async def run_stability_check(
    evaluator: ClinicalEvaluator,
    case: GoldenCase,
    system_decision_status: str,
    system_rationale: str,
    system_confidence: float,
    n: int = 5,
) -> CaseStabilityResult:
    """
    Score the SAME (case, decision, rationale) tuple N times through
    `evaluator.evaluate_case()` and return the raw per-run results.

    `evaluator` is not assumed live or stubbed -- a real `ClinicalEvaluator`
    (live Anthropic client) gives an actual judge-noise measurement; one with
    a stubbed `.client` (as the deterministic regression test uses) exercises
    this harness's own math with zero network calls.
    """
    scores: list[int] = []
    fabrication_flags: list[bool] = []
    for _ in range(n):
        verdict = await evaluator.evaluate_case(
            case=case,
            system_decision_status=system_decision_status,
            system_rationale=system_rationale,
            system_confidence=system_confidence,
        )
        scores.append(verdict.score)
        fabrication_flags.append(verdict.fabrication_detected)
    return CaseStabilityResult(
        case_id=case.case_id, scores=scores, fabrication_flags=fabrication_flags
    )


def compile_stability_report(results: list[CaseStabilityResult]) -> StabilityReport:
    """Pure aggregation function — no I/O, trivially unit-testable."""
    return StabilityReport(results=results)
