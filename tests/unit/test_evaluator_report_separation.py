"""
Report-separation and provenance tests for chg-25 / D5 §2, §3.2, §4.5, §4.7.

`EvaluationReport.hallucinations` is REMOVED, not aliased -- a field whose
meaning changed (one judge-reported boolean conflating three failure modes)
must not keep its old name. These tests prove:
  - the old name is genuinely gone (AttributeError, not a silently-renamed
    field with different semantics);
  - the three replacement counters (`fabrications`, `constraint_violations`,
    `ungrounded_citations`) are independently settable -- a verdict can set
    any one without the others moving;
  - provenance (`judge_model`, `judge_prompt_version`) is populated on every
    verdict and surfaced on the aggregate report.
"""

from __future__ import annotations

import pytest

from tests.clinical.evaluator import (
    ClinicalEvaluator,
    ConstraintCheck,
    EvaluationReport,
    JudgeVerdict,
)


def test_hallucinations_field_no_longer_exists_on_report() -> None:
    """
    `EvaluationReport.hallucinations` must be gone entirely -- code that
    still reads it fails loudly (AttributeError / TypeError at construction)
    rather than silently reading a renamed-but-reused field.
    """
    fields = set(EvaluationReport.__dataclass_fields__)
    assert "hallucinations" not in fields

    evaluator = ClinicalEvaluator(api_key="unused")
    report = evaluator.compile_report([])
    with pytest.raises(AttributeError):
        _ = report.hallucinations  # type: ignore[attr-defined]


def test_three_counters_are_independently_settable() -> None:
    """
    A verdict with ONLY a constraint violation (no fabrication, no
    ungrounded citation) must show up in `constraint_violations` and
    nowhere else -- and symmetrically for the other two counters. This is
    the concrete proof that the three failure modes are no longer
    conflated into one field.
    """
    evaluator = ClinicalEvaluator(api_key="unused")

    constraint_only = JudgeVerdict(
        case_id="GC-A",
        score=4,
        passed=True,
        judge_reasoning="ok",
        correct_outcome=True,
        fabrication_detected=False,
        missing_citations=[],
        constraint_check=ConstraintCheck(missing=[], forbidden_present=["age"]),
        ungrounded_citations=[],
    )
    fabrication_only = JudgeVerdict(
        case_id="GC-B",
        score=1,
        passed=False,
        judge_reasoning="invented a lab value",
        correct_outcome=True,
        fabrication_detected=True,
        missing_citations=[],
        constraint_check=ConstraintCheck(),
        ungrounded_citations=[],
    )
    grounding_only = JudgeVerdict(
        case_id="GC-C",
        score=3,
        passed=True,
        judge_reasoning="ok",
        correct_outcome=True,
        fabrication_detected=False,
        missing_citations=[],
        constraint_check=ConstraintCheck(),
        ungrounded_citations=["e99"],
    )
    clean = JudgeVerdict(
        case_id="GC-D",
        score=5,
        passed=True,
        judge_reasoning="ok",
        correct_outcome=True,
        fabrication_detected=False,
        missing_citations=[],
        constraint_check=ConstraintCheck(),
        ungrounded_citations=[],
    )

    report = evaluator.compile_report([constraint_only, fabrication_only, grounding_only, clean])

    assert [cv.case_id for cv in report.constraint_violations] == ["GC-A"]
    assert report.fabrications == ["GC-B"]
    assert report.ungrounded_citations == ["GC-C"]
    # None of the three lists cross-contaminate each other.
    assert "GC-A" not in report.fabrications
    assert "GC-A" not in report.ungrounded_citations
    assert "GC-B" not in [cv.case_id for cv in report.constraint_violations]
    assert "GC-B" not in report.ungrounded_citations
    assert "GC-C" not in report.fabrications
    assert "GC-C" not in [cv.case_id for cv in report.constraint_violations]


def test_outcome_mismatches_reported_independently_too() -> None:
    """A verdict with correct_outcome=False shows up in outcome_mismatches
    without affecting the other three counters."""
    evaluator = ClinicalEvaluator(api_key="unused")
    wrong_outcome = JudgeVerdict(
        case_id="GC-E",
        score=2,
        passed=False,
        judge_reasoning="wrong decision",
        correct_outcome=False,
        fabrication_detected=False,
        missing_citations=[],
    )
    report = evaluator.compile_report([wrong_outcome])
    assert report.outcome_mismatches == ["GC-E"]
    assert report.fabrications == []
    assert report.constraint_violations == []
    assert report.ungrounded_citations == []


def test_provenance_fields_populated_on_every_verdict_and_surfaced_on_report() -> None:
    """
    `judge_model` and `judge_prompt_version` must be non-empty on every
    verdict `evaluate_case()` produces, and `compile_report()` must surface
    them on the aggregate report -- a disputed score must be re-examinable
    months later.
    """
    import asyncio
    import json
    from unittest.mock import AsyncMock, MagicMock

    from tests.clinical.golden_cases import GOLDEN_CASES

    evaluator = ClinicalEvaluator(api_key="test-key")
    mock_content = MagicMock()
    mock_content.text = json.dumps(
        {
            "score": 5,
            "fabrication_detected": False,
            "missing_citations": [],
            "judge_reasoning": "fine",
        }
    )
    mock_response = MagicMock()
    mock_response.content = [mock_content]
    evaluator.client = AsyncMock()
    evaluator.client.messages.create = AsyncMock(return_value=mock_response)  # type: ignore[method-assign]

    case = GOLDEN_CASES[0]
    verdict = asyncio.run(
        evaluator.evaluate_case(
            case=case,
            system_decision_status="AUTO_APPROVED",
            system_rationale="NCCN Category 1 for PD-L1 >= 50%.",
            system_confidence=0.97,
        )
    )

    assert verdict.judge_model == "claude-haiku-4-5-20251001"
    assert verdict.judge_prompt_version  # non-empty
    assert verdict.raw_response  # full judge response retained for debugging

    report = evaluator.compile_report([verdict])
    assert report.judge_model == verdict.judge_model
    assert report.judge_prompt_version == verdict.judge_prompt_version


def test_provenance_defaults_are_empty_on_an_empty_report() -> None:
    """No verdicts -> no judge was ever called -> empty provenance strings,
    not a crash or a fabricated default model name."""
    evaluator = ClinicalEvaluator(api_key="unused")
    report = evaluator.compile_report([])
    assert report.judge_model == ""
    assert report.judge_prompt_version == ""
