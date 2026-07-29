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

from typing import TYPE_CHECKING

import tests.clinical.evaluator as evaluator_module
from tests.clinical.evaluator import ClinicalEvaluator, JudgeVerdict, split_verdicts_by_holdout

if TYPE_CHECKING:
    import pytest


def _verdict(case_id: str, passed: bool) -> JudgeVerdict:
    """Build a minimal synthetic JudgeVerdict for split-arithmetic tests."""
    return JudgeVerdict(
        case_id=case_id,
        score=4 if passed else 1,
        passed=passed,
        judge_reasoning="synthetic test verdict",
        correct_outcome=passed,
        fabrication_detected=False,
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


def test_summary_renders_zero_cases_as_not_available_not_zero_percent() -> None:
    """
    "0.0% (0/0 cases)" reads as "evaluated and failed everything" -- a human
    scanning CI logs can't tell that apart from a real 0% accuracy. When a
    subset has never been evaluated (today's live gate runs zero held-out
    cases), the summary must say so explicitly rather than print a
    misleading 0.0%.
    """
    from tests.clinical.holdout import HELD_OUT_CASE_IDS

    in_sample_id = "GC-002"
    assert in_sample_id not in HELD_OUT_CASE_IDS

    # No held-out case in this run's verdicts -> held_out_total stays 0,
    # exactly like today's live gate (test_full_pipeline_meets_accuracy_threshold
    # never touches HELD_OUT_CASE_IDS).
    verdicts = [_verdict(in_sample_id, passed=True)]

    evaluator = ClinicalEvaluator(api_key="unused-for-compile-report")
    report = evaluator.compile_report(verdicts)

    assert report.held_out_total == 0
    summary = report.summary()
    assert "n/a" in summary.lower()
    assert "0.0% (0/0 cases)" not in summary


def test_split_warns_on_case_id_not_in_held_out_or_known_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A verdict whose case_id is in neither `held_out_ids` nor `known_ids` is
    still counted in-sample (the function can't do better with membership
    alone), but the mismatch must never be silent -- it is logged as a
    warning naming the offending case_id.
    """
    calls: list[tuple[str, dict[str, object]]] = []

    class _FakeLogger:
        def warning(self, event: str, **kwargs: object) -> None:
            calls.append((event, kwargs))

    monkeypatch.setattr(evaluator_module, "logger", _FakeLogger())

    verdicts = [_verdict("GC-999", passed=True)]  # not a real dataset id
    held_out_ids: frozenset[str] = frozenset({"GC-100"})
    known_ids = frozenset({"GC-001", "GC-100"})  # GC-999 is in neither set

    result = split_verdicts_by_holdout(verdicts, held_out_ids, known_ids=known_ids)

    # Still counted in-sample (only determination possible from membership).
    assert result[0] == 1  # in_sample_total

    assert len(calls) == 1
    event, kwargs = calls[0]
    assert event == "unrecognized_case_id_in_holdout_split"
    assert kwargs["case_id"] == "GC-999"


def test_split_with_known_ids_and_no_unrecognized_ids_does_not_warn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanity check: passing known_ids does not warn when every case_id is recognized."""
    calls: list[tuple[str, dict[str, object]]] = []

    class _FakeLogger:
        def warning(self, event: str, **kwargs: object) -> None:
            calls.append((event, kwargs))

    monkeypatch.setattr(evaluator_module, "logger", _FakeLogger())

    verdicts = [_verdict("GC-001", passed=True), _verdict("GC-100", passed=True)]
    held_out_ids = frozenset({"GC-100"})
    known_ids = frozenset({"GC-001", "GC-100"})

    split_verdicts_by_holdout(verdicts, held_out_ids, known_ids=known_ids)

    assert calls == []
