"""
Tier 2 (deterministic, exact) evidence-grounding tests for chg-25 / D5 §2.

`tests/clinical/evaluator.py::check_evidence_grounding()` must REUSE, not
reimplement, `agents/evidence_grounding.py::unresolved_cited_evidence()` — the
production runtime detector that forces a decision citing fabricated/
misattributed evidence to human review (P-5 / chg-10). Reusing the identical
function means the evaluation harness and the runtime detector can never
silently drift apart on the same input; these tests prove agreement directly
by calling both on the same (decision, case) pair.
"""

from __future__ import annotations

from pacca.agents.evidence_grounding import unresolved_cited_evidence
from tests.clinical.evaluator import check_evidence_grounding
from tests.unit.test_escalation_tree import make_case, make_decision


def _decision(cited: list[str]) -> object:
    from pacca.models.enums import AuthorizationStatus

    d = make_decision(status=AuthorizationStatus.AUTO_APPROVED, confidence=0.97)
    d.cited_evidence_ids = cited
    return d


def test_eval_grounding_agrees_with_runtime_detector_on_partial_match() -> None:
    """make_case() has one EvidenceItem id="e1" (see test_escalation_tree.py)."""
    case = make_case()
    decision = _decision(["e1", "e99", "e99", "ghost"])

    runtime_result = unresolved_cited_evidence(decision, case)
    eval_result = check_evidence_grounding(decision, case)

    assert eval_result == runtime_result == ["e99", "ghost"]


def test_eval_grounding_agrees_with_runtime_detector_on_fully_grounded() -> None:
    case = make_case()
    decision = _decision(["e1"])

    assert (
        check_evidence_grounding(decision, case) == unresolved_cited_evidence(decision, case) == []
    )


def test_eval_grounding_agrees_with_runtime_detector_on_empty_citations() -> None:
    case = make_case()
    decision = _decision([])

    assert (
        check_evidence_grounding(decision, case) == unresolved_cited_evidence(decision, case) == []
    )


def test_eval_grounding_is_literally_the_same_function_not_a_reimplementation() -> None:
    """
    Belt-and-suspenders: `check_evidence_grounding` must be a thin pass-
    through, not a lookalike reimplementation that happens to agree today
    and could silently diverge tomorrow. Patch the real detector and prove
    the eval wrapper's return value is exactly whatever the patched
    detector returns.
    """
    import tests.clinical.evaluator as evaluator_module

    sentinel = ["__patched_sentinel__"]
    original = evaluator_module.unresolved_cited_evidence
    try:
        evaluator_module.unresolved_cited_evidence = lambda decision, case: sentinel  # type: ignore[assignment]
        case = make_case()
        decision = _decision(["e1"])
        assert evaluator_module.check_evidence_grounding(decision, case) is sentinel
    finally:
        evaluator_module.unresolved_cited_evidence = original  # type: ignore[assignment]
