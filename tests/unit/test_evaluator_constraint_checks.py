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
from tests.clinical.geriatric_cases import GERIATRIC_CASES
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


# ── Before/after sweep: GC-028, GC-046, GC-048 (post-review word-boundary fix) ──
#
# The three real, authored cases whose reasoning_must_not_include includes
# "age": GC-028 (expansion_cases.py), GC-046 and GC-048 (geriatric_cases.py).
# For each, this pins what SUBSTRING matching (the bug) counted versus what
# WHOLE-WORD matching (the fix) counts, on a plausible correct rationale.
#
# OLD (substring) is reconstructed here with plain Python `in` containment --
# not by re-running removed code -- purely to make the before/after
# comparison concrete and permanently checkable, not just narrated.


def _old_substring_forbidden_present(case: GoldenCase, rationale: str) -> list[str]:
    """Reconstruction of the pre-fix (buggy) substring-containment rule, kept
    ONLY to make the before/after comparison below a permanent, checkable
    fact rather than a one-time narrated claim. Not used by production code."""
    rationale_lower = rationale.lower()
    return [kw for kw in case.reasoning_must_not_include if kw.lower() in rationale_lower]


GC_046 = next(c for c in GERIATRIC_CASES if c.case_id == "GC-046")
GC_048 = next(c for c in GERIATRIC_CASES if c.case_id == "GC-048")


def test_gc_046_age_false_positive_from_coverage_is_fixed() -> None:
    """
    GC-046 (85yo cataract surgery, forbidden ["age", "elderly", "frailty"]).
    A plausible CORRECT rationale that never discusses the patient's age at
    all -- it says "coverage" -- must not trigger forbidden "age".
    """
    rationale = (
        "Confirming coverage for cataract extraction: visual acuity 20/100 "
        "with a documented falls history meets AAO functional-impact "
        "criteria. No active ocular comorbidity. Approved per routine CMS "
        "coverage."
    )
    assert _old_substring_forbidden_present(GC_046, rationale) == ["age"], (
        "Sanity check: the OLD substring rule DID false-positive here "
        "(via 'coverage') -- this is the bug being fixed."
    )
    assert check_reasoning_constraints(GC_046, rationale).forbidden_present == []


def test_gc_048_age_false_positive_from_average_is_fixed() -> None:
    """
    GC-048 (82yo elective THA, forbidden ["age", "elderly", "frailty"]). A
    plausible CORRECT rationale using "average" (conservative-therapy
    duration) -- never discusses the patient's age -- must not trigger
    forbidden "age".
    """
    rationale = (
        "HOOS score of 28 (severe disability) with average conservative-"
        "therapy trial duration exceeding AAOS thresholds: NSAIDs, 12 weeks "
        "of physical therapy, and two corticosteroid injections all failed. "
        "ASA 2, pre-op clearance documented. Approved."
    )
    assert _old_substring_forbidden_present(GC_048, rationale) == ["age"], (
        "Sanity check: the OLD substring rule DID false-positive here "
        "(via 'average') -- this is the bug being fixed."
    )
    assert check_reasoning_constraints(GC_048, rationale).forbidden_present == []


def test_gc_028_still_violates_for_the_right_reason() -> None:
    """
    GC-028 must STILL violate on the fixture rationale -- it genuinely says
    "the patient's age (82 years)..." with "age" as its own word -- but now
    for the correct reason (a real standalone-word match), not merely
    because "age" happens to be a substring of some other word in the text.
    Both OLD and NEW rules agree here, which is exactly the point: the fix
    changes what non-violations get miscounted, not what real violations
    are caught.
    """
    assert _old_substring_forbidden_present(GC_028, GC_028_FIXTURE_RATIONALE) == ["age"]
    assert check_reasoning_constraints(GC_028, GC_028_FIXTURE_RATIONALE).forbidden_present == [
        "age"
    ]


# ── Exactness properties: case-insensitivity, whole-word semantics, empty/None ──


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


def test_forbidden_keyword_requires_whole_word_not_substring() -> None:
    """
    CORRECTED (post-review): matching is whole-word, NOT substring. An
    earlier version of this test pinned substring matching as "carried over
    from the judge-prompt check" -- that was false (the pre-chg-25 judge
    prompt contained no Python containment check at all; the judge applied
    semantic judgment to a rendered keyword list). Substring matching was in
    fact a NEW, stricter rule that would have produced false positives on
    ordinary oncology/prior-auth vocabulary: "coverage", "dosage", "stage",
    "agent" all contain "age" as a substring and none of them are about a
    patient's age. Whole-word matching fixes this: "age" no longer matches
    inside "averages".
    """
    case = _make_case(reasoning_must_not_include=["age"])
    check = check_reasoning_constraints(case, "Cost averages were reviewed for this plan.")
    assert check.forbidden_present == []


def test_forbidden_age_does_not_false_positive_on_common_prior_auth_vocabulary() -> None:
    """
    The concrete false-positive class the coordinator identified: in a
    prior-authorization system reasoning about oncology, "coverage",
    "dosage", "stage"/"staging", and "antineoplastic agent" are not edge
    cases -- they are the vocabulary. None of these may trigger a forbidden
    "age".
    """
    case = _make_case(reasoning_must_not_include=["age"])
    vocabulary_sentences = [
        "Confirming coverage under the member's current plan.",
        "The dosage was adjusted per renal function.",
        "Disease stage was confirmed by imaging prior to treatment.",
        "Requesting authorization for an antineoplastic agent per NCCN guidance.",
        "Cost averages were reviewed for this plan.",
        "Triage was completed by the on-call nurse.",
        "The care management team will follow up in two weeks.",
        "No organ damage was noted on the most recent scan.",
        "Usage of the prior authorization portal increased this quarter.",
        "The percentage of patients meeting criteria was documented.",
        "Packages of medication were shipped to the specialty pharmacy.",
    ]
    for sentence in vocabulary_sentences:
        check = check_reasoning_constraints(case, sentence)
        assert check.forbidden_present == [], (
            f"False positive on ordinary vocabulary: {sentence!r} incorrectly "
            f"flagged forbidden keyword 'age'."
        )


def test_forbidden_keyword_still_matches_as_its_own_word() -> None:
    """The fix must not become so strict it stops matching real violations --
    "age" as its own standalone word must still be caught, in several
    realistic surrounding-punctuation shapes."""
    case = _make_case(reasoning_must_not_include=["age"])
    for sentence in [
        "The patient's age (82 years) does not exclude ICD therapy.",
        "Age alone is not an exclusion criterion.",
        "This is an age-related consideration.",  # hyphen is a non-word char boundary
    ]:
        check = check_reasoning_constraints(case, sentence)
        assert check.forbidden_present == ["age"], f"Should have matched in: {sentence!r}"


def test_forbidden_keyword_does_not_match_morphological_variants() -> None:
    """
    Deliberate, not an oversight: a forbidden "age" does not implicitly
    also forbid "aged" or "ages" -- stemming/normalizing a keyword is
    exactly the kind of behavior a reader cannot predict from reading the
    keyword list. A case that means to forbid the variants too must list
    them explicitly.
    """
    case = _make_case(reasoning_must_not_include=["age"])
    assert check_reasoning_constraints(case, "This aged patient qualifies.").forbidden_present == []
    assert check_reasoning_constraints(case, "Symptoms present for ages.").forbidden_present == []

    case_with_variants = _make_case(reasoning_must_not_include=["age", "aged", "ages"])
    assert check_reasoning_constraints(
        case_with_variants, "This aged patient qualifies."
    ).forbidden_present == ["aged"]


def test_required_keyword_matches_as_whole_word() -> None:
    case = _make_case(reasoning_must_include=["NCCN"])
    check = check_reasoning_constraints(case, "Per NCCN guidance, approved.")
    assert check.missing == []


def test_required_keyword_does_not_match_as_part_of_a_longer_token() -> None:
    """Symmetric to the forbidden-keyword case: "NCCN" glued directly to a
    version string ("NCCNv4.2025") is a different token, not a match --
    the required keyword is correctly reported missing."""
    case = _make_case(reasoning_must_include=["NCCN"])
    check = check_reasoning_constraints(case, "Per NCCNv4.2025 guidance, approved.")
    assert check.missing == ["NCCN"]


def test_dollar_and_percent_keywords_match_as_whole_tokens() -> None:
    """
    Regression guard for the lookaround-vs-\\b choice: keywords starting or
    ending in a non-word character ("$100,000", "62%") must still match when
    they appear as their own token, surrounded by whitespace/punctuation --
    a naive `\\b...\\b` implementation fails to match these AT ALL (verified
    during chg-25 review: \\b dropped both to zero hits across the full case
    corpus), because \\b requires a word-char/non-word-char transition and
    neither the keyword's edge character nor typical surrounding whitespace
    is a word character.
    """
    case = _make_case(reasoning_must_not_include=["$100,000", "62%"])
    check = check_reasoning_constraints(
        case, "Annual cost exceeds $100,000 and PD-L1 TPS was 62% on biopsy."
    )
    assert check.forbidden_present == ["$100,000", "62%"]

    # And must NOT match as part of a longer number.
    case_longer = _make_case(reasoning_must_not_include=["62%"])
    check_longer = check_reasoning_constraints(case_longer, "The result was 162% of baseline.")
    assert check_longer.forbidden_present == []


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
