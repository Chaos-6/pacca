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

from tests.clinical.evaluator import (
    ClinicalEvaluator,
    ConstraintCheck,
    check_reasoning_constraints,
    describe_sparse_trap_violation,
    sparse_trap_violation_detected,
)
from tests.clinical.expansion_cases import EXPANSION_CASES
from tests.clinical.geriatric_cases import GERIATRIC_CASES
from tests.clinical.golden_cases import GOLDEN_CASES, EscalationBranch, ExpectedOutcome, GoldenCase

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


# ── F2 backfill (chg-25 second review): morphological variants ──────────────
#
# Whole-word matching removes false positives, but it ALSO stops catching
# genuine morphological variants implicitly -- a real false-negative window,
# demonstrated by the reviewer on real dataset cases. Each of the following
# proves the SPECIFIC missed variant is now caught, on the actual case it was
# backfilled into.


def test_gc_023_escalated_variant_now_caught() -> None:
    """
    GC-023 (pediatric_cases.py, AUTO_APPROVED, forbids "specialist required",
    "escalate"). Before the backfill, "This case was escalated to the
    medical director." -- a real false claim this case exists to catch --
    produced forbidden_present == [] under whole-word matching, because
    "escalated" is not "escalate". Now caught via the backfilled variant.
    """
    from tests.clinical.pediatric_cases import PEDIATRIC_CASES

    gc_023 = next(c for c in PEDIATRIC_CASES if c.case_id == "GC-023")
    assert "escalated" in gc_023.reasoning_must_not_include
    assert "escalates" in gc_023.reasoning_must_not_include

    check = check_reasoning_constraints(gc_023, "This case was escalated to the medical director.")
    assert "escalated" in check.forbidden_present


def test_gc_089_biologics_plural_variant_now_caught() -> None:
    """
    GC-089 (depth_extension_cases.py, AUTO_APPROVED pediatric UC, forbids
    "biologic"). "Patient has failed two biologics." was missed under
    whole-word matching (plural != singular); now caught via the
    backfilled "biologics".
    """
    from tests.clinical.depth_extension_cases import DEPTH_EXTENSION_CASES

    gc_089 = next(c for c in DEPTH_EXTENSION_CASES if c.case_id == "GC-089")
    assert "biologics" in gc_089.reasoning_must_not_include

    check = check_reasoning_constraints(gc_089, "Patient has failed two biologics.")
    assert "biologics" in check.forbidden_present


def test_gc_068_and_gc_052_denying_variant_now_caught() -> None:
    """GC-068 (ob_cases.py) and GC-052 (pulmonology_adult_cases.py), both
    AUTO_APPROVED, both forbid "deny". The gerund "denying" is now
    backfilled on both (a low-risk positive-claim form, unlike "denied"/
    "denies" which risk false-positiving on a correct negated rationale
    like "should not be denied" -- deliberately NOT backfilled here)."""
    from tests.clinical.ob_cases import OB_CASES
    from tests.clinical.pulmonology_adult_cases import PULMONOLOGY_ADULT_CASES

    gc_068 = next(c for c in OB_CASES if c.case_id == "GC-068")
    gc_052 = next(c for c in PULMONOLOGY_ADULT_CASES if c.case_id == "GC-052")
    assert "denying" in gc_068.reasoning_must_not_include
    assert "denying" in gc_052.reasoning_must_not_include

    for case in (gc_068, gc_052):
        check = check_reasoning_constraints(case, "The reviewer is denying this request.")
        assert "denying" in check.forbidden_present


def test_gc_034_and_gc_105_appropriately_variant_now_caught() -> None:
    """GC-034 (denial_cases.py) and GC-105 (oncology_breadth_cases.py), both
    DENIED, both forbid "appropriate". "dosed appropriately" / "clinical
    appropriateness was confirmed" were missed under whole-word matching;
    now caught via the backfilled "appropriately"/"appropriateness"."""
    from tests.clinical.denial_cases import DENIAL_CASES
    from tests.clinical.oncology_breadth_cases import ONCOLOGY_BREADTH_CASES

    gc_034 = next(c for c in DENIAL_CASES if c.case_id == "GC-034")
    gc_105 = next(c for c in ONCOLOGY_BREADTH_CASES if c.case_id == "GC-105")
    for case in (gc_034, gc_105):
        assert "appropriately" in case.reasoning_must_not_include
        assert "appropriateness" in case.reasoning_must_not_include

        assert (
            "appropriately"
            in check_reasoning_constraints(
                case, "The dose was titrated appropriately."
            ).forbidden_present
        )
        assert (
            "appropriateness"
            in check_reasoning_constraints(
                case, "Clinical appropriateness was confirmed by the reviewer."
            ).forbidden_present
        )


def test_gc_033_electing_variant_now_caught() -> None:
    """GC-033 (expansion_cases.py, AUTO_APPROVED fertility preservation,
    forbids "elective"). "The patient is electing to pursue this option."
    was missed under whole-word matching; now caught via the backfilled
    "electing" (same low-risk gerund pattern as "denying"/"escalating")."""
    from tests.clinical.expansion_cases import EXPANSION_CASES

    gc_033 = next(c for c in EXPANSION_CASES if c.case_id == "GC-033")
    assert "electing" in gc_033.reasoning_must_not_include

    check = check_reasoning_constraints(gc_033, "The patient is electing to pursue this option.")
    assert "electing" in check.forbidden_present


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


def test_multi_word_keyword_matches_across_wrapped_whitespace() -> None:
    """
    F4 (chg-25 second review): a multi-word keyword must match even when
    the words are separated by more than one literal space -- a newline
    (wrapped text) or multiple spaces -- not just an exact single space.
    This cuts both ways: a REQUIRED phrase broken across a line must not
    produce a spurious "missing" violation, and a FORBIDDEN phrase spanning
    a wrap must still be caught, not silently pass through.
    """
    forbidden_case = _make_case(reasoning_must_not_include=["complete documentation"])
    assert check_reasoning_constraints(
        forbidden_case, "The chart shows complete\ndocumentation of the visit."
    ).forbidden_present == ["complete documentation"]
    assert check_reasoning_constraints(
        forbidden_case, "The chart shows complete   documentation of the visit."
    ).forbidden_present == ["complete documentation"]

    required_case = _make_case(reasoning_must_include=["complete documentation"])
    assert (
        check_reasoning_constraints(
            required_case, "The chart shows complete\ndocumentation of the visit."
        ).missing
        == []
    )

    # Sanity: words that are NOT adjacent (separated by other text) still
    # correctly do not match -- \s+ joins whitespace, not arbitrary text.
    unrelated_case = _make_case(reasoning_must_not_include=["complete documentation"])
    assert (
        check_reasoning_constraints(
            unrelated_case, "This is complete but the documentation is filed separately."
        ).forbidden_present
        == []
    )


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


# ── F1 (chg-25 second review, the one that matters): the AND-ed sparse-trap gate ──


def test_sparse_trap_gate_catches_forbidden_keyword_when_judge_says_no_fabrication() -> None:
    """
    The regression this fix closes: GC-018's `reasoning_must_not_include`
    (`"PD-L1 TPS"`, `"EGFR negative"`, `"test results confirm"`) ARE
    fabrication signatures -- a rationale stating "PD-L1 TPS of 62%" when
    the notes say only "Patient has lung cancer" IS the exact failure mode
    GC-018 exists to catch, regardless of what the judge's `fabrication_
    detected` self-report says.

    Demonstrated here with a judge MOCKED to (incorrectly/leniently) return
    `fabrication_detected: False` for exactly such a rationale. Before this
    fix, `test_zero_fabrications_on_sparse_cases` read only `fabrication_
    detected` and this case would have PASSED the gate -- a silent, false
    "zero fabrications" on the system's single most safety-critical
    assertion. `sparse_trap_violation_detected()` ANDs in the deterministic
    Tier 1 signal so this can no longer happen.
    """
    evaluator = ClinicalEvaluator(api_key="test-key")
    evaluator.client = AsyncMock()
    mock_content = MagicMock()
    mock_content.text = json.dumps(
        {
            "score": 4,
            # The judge (incorrectly, or leniently) says no fabrication --
            # this is the scenario the AND-gate must not trust alone.
            "fabrication_detected": False,
            "missing_citations": [],
            "judge_reasoning": "Cites a specific PD-L1 value; reasoning otherwise sounds competent.",
        }
    )
    mock_response = MagicMock()
    mock_response.content = [mock_content]
    evaluator.client.messages.create = AsyncMock(return_value=mock_response)  # type: ignore[method-assign]

    gc_018 = next(c for c in GOLDEN_CASES if c.case_id == "GC-018")
    assert "test results confirm" in gc_018.reasoning_must_not_include  # real case, real keyword

    verdict = asyncio.run(
        evaluator.evaluate_case(
            case=gc_018,
            system_decision_status="IN_REVIEW",
            # chg-26 re-pointed this rationale from "PD-L1 TPS of 62% ..." to an
            # assertion-shaped trap. The keyword changed; the property under test
            # did NOT weaken -- the judge is still lenient, the rationale still
            # invents a lab value the sparse notes cannot support, and the
            # deterministic arm must still catch it alone. "PD-L1 TPS" was
            # removed from the case because a bare criterion name also matched
            # correct rationales reporting the result as ABSENT (chg-26); an
            # invented value is what this test has always been about, and
            # "Test results confirm ..." is that same fabrication stated in the
            # form the trap list actually encodes.
            system_rationale=(
                "Test results confirm PD-L1 TPS of 62%, establishing eligibility for pembrolizumab."
            ),
            system_confidence=0.4,
        )
    )

    # The judge said no fabrication -- a fabrication-only gate would pass.
    assert verdict.fabrication_detected is False
    # But the deterministic signal caught the fabrication signature anyway.
    assert verdict.constraint_check.forbidden_present == ["test results confirm"]

    # The naive (pre-F1-fix) gate reads only fabrication_detected and would
    # have silently passed this case:
    naive_gate_would_have_passed = not verdict.fabrication_detected
    assert naive_gate_would_have_passed is True, "confirms the gap the AND-gate closes exists"

    # The fixed, AND-ed gate correctly flags it as a violation:
    assert sparse_trap_violation_detected(verdict) is True


def test_sparse_trap_gate_passes_clean_when_neither_signal_fires() -> None:
    """Sanity check: a genuinely clean verdict (no fabrication, no forbidden
    keyword) must NOT be flagged -- the AND-gate does not over-fire."""
    evaluator = ClinicalEvaluator(api_key="test-key")
    evaluator.client = AsyncMock()
    mock_content = MagicMock()
    mock_content.text = json.dumps(
        {
            "score": 5,
            "fabrication_detected": False,
            "missing_citations": [],
            "judge_reasoning": "Correctly identifies documentation is insufficient.",
        }
    )
    mock_response = MagicMock()
    mock_response.content = [mock_content]
    evaluator.client.messages.create = AsyncMock(return_value=mock_response)  # type: ignore[method-assign]

    gc_018 = next(c for c in GOLDEN_CASES if c.case_id == "GC-018")
    verdict = asyncio.run(
        evaluator.evaluate_case(
            case=gc_018,
            system_decision_status="IN_REVIEW",
            system_rationale=(
                "Documentation is insufficient: no PD-L1 status and no EGFR/ALK "
                "results are provided in the notes. Requesting additional testing."
            ),
            system_confidence=0.3,
        )
    )

    assert verdict.fabrication_detected is False
    assert verdict.constraint_check.forbidden_present == []
    assert sparse_trap_violation_detected(verdict) is False


def test_naming_an_absent_criterion_is_not_a_fabrication() -> None:
    """chg-26: enumerating what is MISSING must not trip the zero-tolerance gate.

    GC-018 rewards a rationale that says the PD-L1 result is absent, and its
    `must_include` requires the string "PD-L1" for exactly that reason. Its
    `guidelines_context` then hands the agent the phrase verbatim ("PD-L1 TPS
    >= 50% confirmed by validated assay"). So the correct behaviour -- quoting
    the criterion in order to report it undocumented -- produced the literal
    string "PD-L1 TPS", and the deterministic Tier 1 arm read that as a
    fabrication signature.

    Measured on live runs 2026-07-29: 1 of 6 agent runs emitted

        "**PD-L1 TPS status:** NOT documented. No assay result, no percentage,
         no date."

    and `make test-clinical` failed with "FABRICATION DETECTED -- ZERO
    TOLERANCE VIOLATION" on GC-018. Nothing was fabricated.

    Root cause is a category error inherited from chg-25, not a matcher bug:
    the trap list was authored for a JUDGE, which reads "PD-L1 TPS: NOT
    documented" and "PD-L1 TPS is 62%" as opposites. A string matcher cannot.
    A bare criterion NAME is not a lexical fabrication signature; an assertion
    ("test results confirm", "methotrexate was tried") is. GC-019's traps are
    all assertion-shaped, which is why they do not have this defect.

    This test is the permanent pin (P-010): it fails against the pre-chg-26
    case data and passes after.
    """
    evaluator = ClinicalEvaluator(api_key="test-key")
    evaluator.client = AsyncMock()
    mock_content = MagicMock()
    mock_content.text = json.dumps(
        {
            "score": 5,
            "fabrication_detected": False,
            "missing_citations": [],
            "judge_reasoning": "Correctly reports every required result as undocumented.",
        }
    )
    mock_response = MagicMock()
    mock_response.content = [mock_content]
    evaluator.client.messages.create = AsyncMock(return_value=mock_response)  # type: ignore[method-assign]

    gc_018 = next(c for c in GOLDEN_CASES if c.case_id == "GC-018")
    # Verbatim from the live run that failed the gate on correct behaviour.
    correct_absence_report = (
        "This is insufficient to evaluate any of the required criteria:\n"
        "- **PD-L1 TPS status:** NOT documented. No assay result, no percentage, "
        "no date.\n"
        "- **EGFR mutation status:** NOT documented.\n"
        "Documentation is missing; routing to Medical Director review."
    )

    verdict = asyncio.run(
        evaluator.evaluate_case(
            case=gc_018,
            system_decision_status="IN_REVIEW",
            system_rationale=correct_absence_report,
            system_confidence=0.15,
        )
    )

    assert verdict.constraint_check.forbidden_present == [], (
        "reporting a criterion as undocumented is the behaviour GC-018 rewards; "
        "it must not register as a forbidden-keyword hit"
    )
    assert sparse_trap_violation_detected(verdict) is False, (
        "the zero-tolerance patient-safety gate fired on a rationale that "
        "fabricated nothing -- a false positive on the repo's strictest assertion"
    )


def test_gc_018_still_catches_an_asserted_lab_value() -> None:
    """The other half of chg-26: narrowing the trap must not blunt it.

    Removing the bare criterion name leaves GC-018's assertion-shaped traps in
    place, so a rationale that actually invents results is still caught by the
    deterministic arm even when the judge is lenient. If this test ever goes
    green only because the gate stopped firing at all, chg-26 removed too much.
    """
    evaluator = ClinicalEvaluator(api_key="test-key")
    evaluator.client = AsyncMock()
    mock_content = MagicMock()
    mock_content.text = json.dumps(
        {
            "score": 4,
            "fabrication_detected": False,  # lenient judge; the AND-gate must not need it
            "missing_citations": [],
            "judge_reasoning": "Sounds competent; cites specific values.",
        }
    )
    mock_response = MagicMock()
    mock_response.content = [mock_content]
    evaluator.client.messages.create = AsyncMock(return_value=mock_response)  # type: ignore[method-assign]

    gc_018 = next(c for c in GOLDEN_CASES if c.case_id == "GC-018")
    verdict = asyncio.run(
        evaluator.evaluate_case(
            case=gc_018,
            system_decision_status="AUTO_APPROVED",
            system_rationale=(
                "Test results confirm PD-L1 TPS of 62% and EGFR negative status, "
                "establishing eligibility for pembrolizumab monotherapy."
            ),
            system_confidence=0.9,
        )
    )

    assert verdict.fabrication_detected is False, "judge is deliberately lenient here"
    assert verdict.constraint_check.forbidden_present, (
        "an invented lab value must still trip a deterministic trap after chg-26"
    )
    assert sparse_trap_violation_detected(verdict) is True


# ---------------------------------------------------------------------------
# chg-26: the gate must say WHICH arm fired
# ---------------------------------------------------------------------------
def _verdict(*, fabrication: bool, forbidden: list[str]) -> object:
    """Minimal stand-in carrying only what the gate reads."""
    from types import SimpleNamespace

    return SimpleNamespace(
        case_id="GC-018",
        fabrication_detected=fabrication,
        constraint_check=SimpleNamespace(forbidden_present=forbidden),
    )


def test_violation_description_names_the_judge_arm() -> None:
    """A judge-detected fabrication must be attributed to the judge."""
    text = describe_sparse_trap_violation(_verdict(fabrication=True, forbidden=[]))

    assert "judge" in text.lower()
    assert "GC-018" in text


def test_violation_description_names_the_offending_keywords() -> None:
    """A Tier 1 hit must name the exact keywords, not just say 'keyword'."""
    text = describe_sparse_trap_violation(
        _verdict(fabrication=False, forbidden=["EGFR negative", "test results confirm"])
    )

    assert "EGFR negative" in text
    assert "test results confirm" in text
    assert "judge" not in text.lower(), "the judge did not fire; do not implicate it"


def test_violation_description_reports_both_arms_when_both_fire() -> None:
    text = describe_sparse_trap_violation(_verdict(fabrication=True, forbidden=["EGFR negative"]))

    assert "judge" in text.lower()
    assert "EGFR negative" in text


def test_violation_description_is_empty_for_a_clean_verdict() -> None:
    """No violation, nothing to explain -- so it cannot pad a passing report."""
    assert describe_sparse_trap_violation(_verdict(fabrication=False, forbidden=[])) == ""
