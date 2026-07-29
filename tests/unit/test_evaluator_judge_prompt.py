"""
Proof that the judge is never shown case-specific keyword lists (chg-25 / D5 §2 Tier 1, §4.3).

The defect this guards against: the pre-chg-25 judge prompt included each
case's `reasoning_must_include` / `reasoning_must_not_include` keywords under
a "hallucination markers" header, which taught the judge to conflate an exact
keyword hit with a fabricated clinical fact (see the GC-028 finding in
docs/EVALUATION.md and tests/unit/test_evaluator_constraint_checks.py). The
fix is to remove the judge's ability to see this ever again -- not to
relabel it. This test asserts on the RENDERED prompt text so a future edit
that reintroduces a keyword section (even under a different header) is
caught immediately, rather than silently reintroducing the conflation.
"""

from __future__ import annotations

from tests.clinical.evaluator import JUDGE_SYSTEM_PROMPT, _build_judge_prompt
from tests.unit.test_evaluator_keyword_sweep import ALL_CASES

# F7 (chg-25 second review, LOW): widened from GOLDEN_CASES (20) to every
# case object in the dataset (108, as of this review) -- the reviewer
# rendered all 108 and found zero leaks, so the committed guard should cover
# the same universe that was actually checked, not a 20-case sample of it.


def test_rendered_user_prompt_never_contains_case_keyword_lists() -> None:
    """
    For every case in the dataset, render the actual user-turn judge prompt
    and assert that the case's `reasoning_must_include` / `reasoning_must_
    not_include` lists are never rendered into it as a contiguous,
    comma-joined block -- the exact shape `_build_judge_prompt()` used to
    emit them in pre-chg-25 (`", ".join(case.reasoning_must_include)`).

    NOTE: this checks for the JOINED LIST, not individual keywords. An
    individual keyword (e.g. "PD-L1") legitimately appears in a case's own
    `clinical_notes` / `guidelines_context` / `judge_scoring_criteria`,
    which the judge correctly still sees -- that is real clinical content
    and rubric text, not a keyword-list instruction. A stricter,
    keyword-by-keyword "must never appear outside clinical_notes" check was
    considered and rejected as impractical: measured across all 108 cases,
    23 (case, keyword) pairs legitimately have a forbidden keyword appear in
    `guidelines_context` or `judge_scoring_criteria` but not in
    `clinical_notes` -- e.g. GC-028's forbidden "age" appears in its own
    `guidelines_context` ("Age alone is not an exclusion"), and GC-023's
    forbidden "escalates" appears in its own `judge_scoring_criteria`
    ("Penalize if rationale escalates based solely on..."). Neither is a
    leak of a keyword-list instruction; both are legitimate case content the
    judge is supposed to see. What must never reappear is the two lists
    rendered as an instruction block, which is what taught the judge to
    conflate a keyword hit with a fabrication (see GC-028 in
    docs/EVALUATION.md) -- that is exactly what this test checks.
    """
    for case in ALL_CASES:
        prompt = _build_judge_prompt(
            case=case,
            system_decision_status="AUTO_APPROVED",
            system_rationale="A rationale that does not itself leak any keyword.",
            system_confidence=0.9,
        )

        if len(case.reasoning_must_include) >= 2:
            joined = ", ".join(case.reasoning_must_include)
            assert joined not in prompt, (
                f"{case.case_id}: reasoning_must_include rendered as a joined "
                f"list {joined!r} -- the judge must never be shown case "
                f"keyword lists (chg-25)."
            )
        if len(case.reasoning_must_not_include) >= 2:
            joined = ", ".join(case.reasoning_must_not_include)
            assert joined not in prompt, (
                f"{case.case_id}: reasoning_must_not_include rendered as a "
                f"joined list {joined!r} -- this is exactly the conflation "
                f"chg-25 removes."
            )


def test_rendered_prompt_has_no_keyword_section_headers_at_all() -> None:
    """
    Belt-and-suspenders: the section HEADERS that carried the keyword lists
    pre-chg-25 must not appear in the rendered prompt for ANY of the 108
    cases, even one whose keywords happen to be substrings that could
    appear coincidentally in clinical text. This is what makes the guard
    robust to a future edit silently reintroducing a keyword section under
    the same or similar wording, not just to accidental keyword collisions.
    """
    for case in ALL_CASES:
        prompt = _build_judge_prompt(
            case=case,
            system_decision_status="AUTO_APPROVED",
            system_rationale="rationale",
            system_confidence=0.9,
        )
        assert "must NOT appear" not in prompt
        assert "hallucination markers" not in prompt
        assert "Keywords that MUST appear" not in prompt
        assert "reasoning_must_include" not in prompt
        assert "reasoning_must_not_include" not in prompt


def test_judge_system_prompt_defines_fabrication_narrowly_with_counter_example() -> None:
    """
    The system prompt must state the narrow fabrication definition AND give
    a concrete counter-example of what does NOT count (D5 §2 Tier 3) -- the
    GC-028 shape specifically (discussing a real, submitted fact is not
    fabrication) so the judge is taught the correct boundary, not just told
    a rule without an example to anchor it.
    """
    assert "FABRICATION" in JUDGE_SYSTEM_PROMPT
    assert "does NOT count as fabrication" in JUDGE_SYSTEM_PROMPT
    assert "82 years old" in JUDGE_SYSTEM_PROMPT  # the GC-028-shaped counter-example
    # The old conflated JSON key and "hallucination" framing must be gone.
    assert "hallucination_detected" not in JUDGE_SYSTEM_PROMPT
    assert "fabrication_detected" in JUDGE_SYSTEM_PROMPT
