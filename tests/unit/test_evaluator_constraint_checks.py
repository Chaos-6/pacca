"""
Tier 1 (deterministic, exact) constraint-check tests for chg-25 / D5.

Why this file exists
---------------------
Before chg-25, `reasoning_must_include` / `reasoning_must_not_include` were
answered by asking the LLM judge — even though they are plain string
containment, which a function answers exactly, every time. Worse, showing the
judge the forbidden-keyword list under the header "Keywords that must NOT
appear (hallucination markers)" (the pre-chg-25 `evaluator.py:361-362`) taught
it to conflate an exact keyword hit with a genuine fabrication. GC-028 is the
real, recorded instance: the same system rationale (mentioning the patient's
age to say it does NOT exclude treatment) drew a score-2 "anti-pattern" at one
clinical-gate run and a score-1 "hallucination" at the next — same behavior,
different self-reported label, `hallucinations` count moved 0 -> 1 with no
system change (see docs/EVALUATION.md and the chg-25 manifest evidence).

These tests exercise `check_reasoning_constraints()` — the Tier 1 function
that now answers this deterministically, entirely outside the judge prompt —
and pin the GC-028 regression as a permanent, mutation-provable guard (P-010).
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from tests.clinical.evaluator import ClinicalEvaluator, ConstraintCheck, check_reasoning_constraints
from tests.clinical.expansion_cases import EXPANSION_CASES
from tests.clinical.golden_cases import EscalationBranch, ExpectedOutcome, GoldenCase

# ── The real GC-028 case, exactly as authored in expansion_cases.py ──────────

GC_028 = next(c for c in EXPANSION_CASES if c.case_id == "GC-028")

# FIXTURE NOTE: this rationale is NOT the system's actual captured output from
# either clinical-gate run — the full rationale was never logged verbatim,
# only the judge's QUOTATIONS of fragments of it were. This is a faithful
# fixture built around the verbatim fragment the iter-17 judge quoted
# ("The patient's age (82 years) does not exclude ICD therapy"), embedded in
# surrounding text consistent with GC-028's own judge_scoring_criteria (cites
# LVEF, NYHA, GDMT duration). Treat it as a reconstruction for testing
# purposes, not a recording.
GC_028_FIXTURE_RATIONALE = (
    "The patient meets all CMS NCD 20.4 criteria for primary-prevention ICD "
    "implantation: LVEF 28% (threshold <= 35%), NYHA Class II symptoms, "
    "optimal medical therapy for more than six months, and adequate time "
    "since the myocardial infarction and revascularization. The patient's "
    "age (82 years) does not exclude ICD therapy under CMS or ACC/AHA "
    "guidance."
)


def test_gc_028_is_the_real_authored_case_with_its_real_keyword_lists() -> None:
    """
    Sanity check that this test targets the actual production case, not a
    look-alike copy — GC-028's `reasoning_must_not_include` is `["age",
    "elderly"]` as authored in expansion_cases.py.
    """
    assert GC_028.reasoning_must_not_include == ["age", "elderly"]
    assert GC_028.expected_outcome.value == "AUTO_APPROVED"


def test_gc_028_forbidden_keyword_produces_constraint_violation() -> None:
    """
    P-010 regression: GC-028's real forbidden-keyword conflation.

    A rationale that correctly reaches AUTO_APPROVED, correctly cites every
    required CMS NCD 20.4 criterion, and merely discusses the patient's
    (real, submitted) age to explain that it is NOT exclusionary must
    produce a Tier 1 constraint violation on "age" — deterministically,
    every time, regardless of what any judge says.
    """
    check = check_reasoning_constraints(GC_028, GC_028_FIXTURE_RATIONALE)

    assert check.forbidden_present == ["age"], (
        "The exact, deterministic check must catch the forbidden keyword "
        "'age' -- this is the constraint violation GC-028 is designed to "
        "surface, independent of any judge's label for it."
    )
    assert check.has_violation is True
    # The required-content check still runs independently: GC-028's
    # required keywords ARE present in the fixture, so nothing is missing.
    assert check.missing == []


def test_gc_028_required_keywords_also_checked_independently() -> None:
    """
    A rationale missing GC-028's required content (LVEF / NYHA / optimal
    medical therapy) is caught by `missing`, independent of the forbidden-
    keyword check above — the two lists are checked independently, as
    designed.
    """
    sparse_rationale = "Approved. Age does not matter here."
    check = check_reasoning_constraints(GC_028, sparse_rationale)

    assert check.forbidden_present == ["age"]
    assert set(check.missing) == {"LVEF", "NYHA", "optimal medical therapy"}
    assert check.has_violation is True


def test_gc_028_end_to_end_produces_constraint_violation_and_not_fabrication() -> None:
    """
    The full P-010 regression, through `evaluate_case()` + `compile_report()`:
    GC-028's forbidden-keyword hit must land in `constraint_violations` and
    must NOT increment `fabrications` -- even with a judge mocked to behave
    exactly as the NEW, narrowed prompt instructs (fabrication_detected=False,
    because nothing was invented -- the patient's age IS in the submission).

    This is the concrete, opposite-direction proof to the historical
    instability: before chg-25, the SAME rationale drew `hallucinations: 0`
    at one clinical-gate run and `hallucinations: 1` at the next, with no
    system change (see docs/EVALUATION.md). After chg-25, `constraint_
    violations` is 100% reproducible (pure string containment) and
    `fabrications` no longer depends on whether the judge was ever shown a
    keyword list it might conflate with fabrication -- because it never is.
    """
    evaluator = ClinicalEvaluator(api_key="test-key")
    evaluator.client = AsyncMock()
    mock_content = MagicMock()
    mock_content.text = json.dumps(
        {
            "score": 4,
            # A judge following the NEW, narrowed prompt (which explicitly
            # states that discussing a real, submitted fact like age is NOT
            # fabrication) correctly reports False here.
            "fabrication_detected": False,
            "missing_citations": [],
            "judge_reasoning": (
                "Correct decision, all required CMS NCD 20.4 criteria cited. "
                "The rationale discusses the patient's real, submitted age "
                "but does not invent any clinical fact."
            ),
        }
    )
    mock_response = MagicMock()
    mock_response.content = [mock_content]
    evaluator.client.messages.create = AsyncMock(return_value=mock_response)  # type: ignore[method-assign]

    verdict = asyncio.run(
        evaluator.evaluate_case(
            case=GC_028,
            system_decision_status="AUTO_APPROVED",
            system_rationale=GC_028_FIXTURE_RATIONALE,
            system_confidence=0.95,
        )
    )

    assert verdict.constraint_check.forbidden_present == ["age"]
    assert verdict.fabrication_detected is False
    assert verdict.correct_outcome is True  # AUTO_APPROVED matches GC-028's expected outcome

    report = evaluator.compile_report([verdict])
    assert [cv.case_id for cv in report.constraint_violations] == ["GC-028"]
    assert report.fabrications == []


# ── Exactness properties: case-insensitivity, substring semantics, empty/None ──


def _make_case(
    reasoning_must_include: list[str] | None = None,
    reasoning_must_not_include: list[str] | None = None,
) -> GoldenCase:
    """Minimal synthetic GoldenCase for pinning check_reasoning_constraints()
    properties in isolation from any real authored case."""
    return GoldenCase(
        case_id="GC-TEST",
        title="Synthetic test case",
        diagnosis_code="Z00.00",
        diagnosis_description="test",
        procedure_code="00000",
        procedure_description="test",
        clinical_notes="test notes",
        guidelines_context="test guidelines",
        expected_outcome=ExpectedOutcome.AUTO_APPROVED,
        expected_branch=EscalationBranch.BRANCH_1_AUTO_APPROVE,
        reasoning_must_include=reasoning_must_include or [],
        reasoning_must_not_include=reasoning_must_not_include or [],
    )


def test_match_is_case_insensitive_for_required_keywords() -> None:
    case = _make_case(reasoning_must_include=["PD-L1"])
    check = check_reasoning_constraints(case, "the pd-l1 score was reviewed")
    assert check.missing == []


def test_match_is_case_insensitive_for_forbidden_keywords() -> None:
    case = _make_case(reasoning_must_not_include=["ELDERLY"])
    check = check_reasoning_constraints(case, "This elderly patient qualifies.")
    assert check.forbidden_present == ["ELDERLY"]


def test_forbidden_keyword_matches_as_substring_not_whole_word() -> None:
    """
    Documents and PINS the substring (not word-boundary) semantics carried
    over unchanged from the removed judge-prompt keyword check (plain Python
    `in` containment on lowercased text). A short forbidden keyword matches
    inside a longer word too -- "age" inside "averages" -- which is exactly
    why case authors must choose specific keywords (see GC-028 above, where
    this same property fires on a real, intentional case). This is a
    deliberate, documented trade-off, not a bug: moving the check out of the
    judge changes WHO decides it, not WHAT the decision is.
    """
    case = _make_case(reasoning_must_not_include=["age"])
    check = check_reasoning_constraints(case, "Cost averages were reviewed for this plan.")
    assert check.forbidden_present == ["age"]


def test_required_keyword_matches_as_substring_not_whole_word() -> None:
    """Same substring semantics apply symmetrically to reasoning_must_include."""
    case = _make_case(reasoning_must_include=["NCCN"])
    check = check_reasoning_constraints(case, "Per NCCNv4.2025 guidance, approved.")
    assert check.missing == []


def test_empty_keyword_lists_produce_no_violation() -> None:
    """A case with no keyword constraints at all (both lists empty, the
    GoldenCase default) never raises and never flags a violation."""
    case = _make_case()
    check = check_reasoning_constraints(case, "Any rationale text at all.")
    assert check == ConstraintCheck(missing=[], forbidden_present=[])
    assert check.has_violation is False


def test_none_keyword_lists_are_handled_defensively() -> None:
    """
    `GoldenCase.reasoning_must_include` / `reasoning_must_not_include` are
    typed `list[str]` and default to `[]`, never `None` -- but
    `check_reasoning_constraints()` treats an explicitly-passed `None` the
    same as `[]` (via `or ()`), so a defensively-constructed case can never
    raise `TypeError: 'NoneType' object is not iterable` here.
    """
    case = _make_case()
    case.reasoning_must_include = None  # type: ignore[assignment]
    case.reasoning_must_not_include = None  # type: ignore[assignment]

    check = check_reasoning_constraints(case, "Any rationale text at all.")
    assert check.missing == []
    assert check.forbidden_present == []
    assert check.has_violation is False


def test_multiple_forbidden_keywords_all_reported() -> None:
    case = _make_case(reasoning_must_not_include=["age", "elderly"])
    check = check_reasoning_constraints(case, "This elderly patient's age is not a factor.")
    assert check.forbidden_present == ["age", "elderly"]
