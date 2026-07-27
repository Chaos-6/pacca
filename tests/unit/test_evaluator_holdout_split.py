"""
Unit tests for the in-sample / held-out accuracy split added to
`tests/clinical/evaluator.py`.

Why this file exists
---------------------
`tests/clinical/holdout.py` declares a 32-case holdout for PACCA's clinical
golden dataset. `split_verdicts_by_holdout()` and
`ClinicalEvaluator.compile_report()` are the reporting layer that surfaces
in-sample vs held-out accuracy from a completed evaluation run.

These tests use synthetic `JudgeVerdict`s and never call the Anthropic API
— `split_verdicts_by_holdout()` is a pure function, and `compile_report()`
does no I/O, so both are testable without `ANTHROPIC_API_KEY` and without
the `clinical` marker. They exercise:
  - the split arithmetic (counts + accuracy per subset)
  - the empty-subset edge case (must not raise ZeroDivisionError)
  - that `compile_report()` wires the split into `EvaluationReport` without
    disturbing the existing `accuracy` / `passed_ci_gate` semantics
"""

from __future__ import annotations

from tests.clinical.evaluator import ClinicalEvaluator, JudgeVerdict, split_verdicts_by_holdout


def _verdict(case_id: str, passed: bool) -> JudgeVerdict:
    """Build a minimal synthetic JudgeVerdict for split-arithmetic tests."""
    return JudgeVerdict(
        case_id=case_id,
        score=4 if passed else 1,
        passed=passed,
        judge_reasoning="synthetic test verdict",
        correct_outcome=passed,
        hallucination_detected=False,
        missing_citations=[],
    )


def test_split_computes_independent_accuracy_per_subset() -> None:
    """3 in-sample (2 pass) and 2 held-out (1 pass) -> two independent accuracies."""
    verdicts = [
        _verdict("GC-001", passed=True),
        _verdict("GC-002", passed=True),
        _verdict("GC-003", passed=False),
        _verdict("GC-100", passed=True),
        _verdict("GC-101", passed=False),
    ]
    held_out_ids = frozenset({"GC-100", "GC-101"})

    (
        in_sample_total,
        in_sample_passed,
        accuracy_in_sample,
        held_out_total,
        held_out_passed,
        accuracy_held_out,
    ) = split_verdicts_by_holdout(verdicts, held_out_ids)

    assert in_sample_total == 3
    assert in_sample_passed == 2
    assert accuracy_in_sample == 2 / 3

    assert held_out_total == 2
    assert held_out_passed == 1
    assert accuracy_held_out == 0.5


def test_split_with_empty_held_out_subset_does_not_raise() -> None:
    """No verdict falls in the held-out set -> accuracy_held_out is 0.0, not a ZeroDivisionError."""
    verdicts = [_verdict("GC-001", passed=True), _verdict("GC-002", passed=False)]
    held_out_ids: frozenset[str] = frozenset()

    (
        in_sample_total,
        in_sample_passed,
        accuracy_in_sample,
        held_out_total,
        held_out_passed,
        accuracy_held_out,
    ) = split_verdicts_by_holdout(verdicts, held_out_ids)

    assert in_sample_total == 2
    assert in_sample_passed == 1
    assert accuracy_in_sample == 0.5
    assert held_out_total == 0
    assert held_out_passed == 0
    assert accuracy_held_out == 0.0


def test_split_with_empty_in_sample_subset_does_not_raise() -> None:
    """Every verdict is held-out -> accuracy_in_sample is 0.0, not a ZeroDivisionError."""
    verdicts = [_verdict("GC-100", passed=True)]
    held_out_ids = frozenset({"GC-100"})

    (
        in_sample_total,
        in_sample_passed,
        accuracy_in_sample,
        held_out_total,
        held_out_passed,
        accuracy_held_out,
    ) = split_verdicts_by_holdout(verdicts, held_out_ids)

    assert in_sample_total == 0
    assert in_sample_passed == 0
    assert accuracy_in_sample == 0.0
    assert held_out_total == 1
    assert held_out_passed == 1
    assert accuracy_held_out == 1.0


def test_split_with_no_verdicts_at_all_does_not_raise() -> None:
    """Empty verdicts list -> both subsets report 0.0 accuracy, not a ZeroDivisionError."""
    result = split_verdicts_by_holdout([], frozenset({"GC-100"}))
    assert result == (0, 0, 0.0, 0, 0, 0.0)


def test_compile_report_wires_split_without_changing_existing_accuracy() -> None:
    """
    compile_report() must populate the new split fields using the real
    HELD_OUT_CASE_IDS, while leaving `accuracy` / `passed_ci_gate` computed
    over ALL verdicts exactly as before this feature existed.
    """
    from tests.clinical.holdout import HELD_OUT_CASE_IDS

    held_out_id = next(iter(HELD_OUT_CASE_IDS))
    in_sample_id = "GC-002"  # part of GOLDEN_CASES; never in HELD_OUT_CASE_IDS
    assert in_sample_id not in HELD_OUT_CASE_IDS

    verdicts = [
        _verdict(in_sample_id, passed=True),
        _verdict(held_out_id, passed=False),
    ]

    evaluator = ClinicalEvaluator(api_key="unused-for-compile-report")
    report = evaluator.compile_report(verdicts)

    # Existing, pre-holdout-feature behavior is untouched.
    assert report.total_cases == 2
    assert report.passed_cases == 1
    assert report.accuracy == 0.5

    # New split fields reflect the real HELD_OUT_CASE_IDS membership.
    assert report.in_sample_total == 1
    assert report.in_sample_passed == 1
    assert report.accuracy_in_sample == 1.0
    assert report.held_out_total == 1
    assert report.held_out_passed == 0
    assert report.accuracy_held_out == 0.0

    summary = report.summary()
    assert "in-sample" in summary.lower()
    assert "held-out" in summary.lower()
