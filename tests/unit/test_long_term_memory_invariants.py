"""Safety invariants of the DecisionAgent's institutional memory.

`src/pacca/agents/decision_support/long_term_memory.md` is injected wholesale
into the DecisionAgent prompt (`_prompt_loader.py`), so its text is not
documentation — it is production behaviour, edited as prose and therefore
outside every type checker, linter and unit test the rest of `src/` enjoys.

**What these tests can and cannot do.** They assert that specific safety
instructions are still *present*. They cannot assert the model *obeys* them —
only the live clinical gate can, and even it measures a sample. A text-presence
test is a weak guard. It is worth having anyway, because the failure it catches
is the realistic one: not a model that ignores an instruction, but a future edit
that deletes or softens the instruction while chasing a failing case, with
nothing red to stop it.

The boundary these protect is recorded in CLAUDE.md's safety invariants and
docs/EVALUATION.md: **PACCA makes coverage determinations and never
medical-necessity determinations** (David, 2026-07-31).

These pins survive chg-29's rollback deliberately. chg-29 tried to add a
coverage-criteria deny pattern here and was reverted as inert (the four cases
it targeted never reach the agent -- pre-flight short-circuits them). The
guards below protect the file regardless of what any future entry does, and
they were the only part of that change worth keeping.
"""

from __future__ import annotations

from pathlib import Path

_MEMORY = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "pacca"
    / "agents"
    / "decision_support"
    / "long_term_memory.md"
)


def _memory_text() -> str:
    """The memory as one whitespace-normalised string.

    Collapsing runs of whitespace matters: this is hard-wrapped prose, so any
    phrase long enough to be worth asserting on is likely to straddle a line
    break, and a literal substring check would then fail on text that is
    plainly present. chg-25 hit exactly this in `check_reasoning_constraints`
    (finding F5) and fixed it the same way — a multi-word phrase must treat
    whitespace as a flexible boundary. Re-wrapping a paragraph is not a
    behaviour change and must not turn a guard red.

    Leading blockquote markers are stripped for the same reason. The most
    safety-critical instructions in this file are written as `>` callouts, so a
    phrase inside one straddles both a line break *and* a `> ` marker; joining
    lines alone leaves the marker sitting mid-sentence. These guards assert on
    the instruction, not on the markup that renders it.
    """
    lines = [
        line.lstrip().removeprefix(">").lstrip()
        for line in _MEMORY.read_text(encoding="utf-8").splitlines()
    ]
    return " ".join(" ".join(lines).split())


def test_medical_necessity_denial_is_still_excluded() -> None:
    """The carve-out separating coverage from medical necessity must survive.

    This is the single instruction standing between PACCA and an adverse
    medical-necessity determination — a call reserved to a licensed clinician in
    utilization review, and one the system is not permitted to make. The edit most
    likely to erode it by accident is a new deny pattern added alongside it, which
    is exactly what chg-29 attempted before being rolled back.
    """
    text = _memory_text().lower()

    assert "not medically necessary" in text, (
        "the medical-necessity carve-out is gone from long_term_memory.md — "
        "without it nothing in the prompt distinguishes a coverage determination "
        "(permitted) from a medical-necessity determination (never permitted)"
    )
    assert "do not conflate" in text, (
        "the explicit instruction not to conflate benefit-design limits with "
        "medical-necessity determinations has been removed or reworded"
    )


def test_uncertainty_still_routes_to_review_not_denial() -> None:
    """`Any uncertainty -> IN_REVIEW` and the absence-of-evidence rule.

    Over-denial is the failure mode that harms patients, and these two sentences
    are what bound it. A deny pattern that keeps its required criteria but loses
    this governing rule is strictly more dangerous than no deny pattern at all.
    """
    text = _memory_text().lower()

    assert "absence of evidence is not evidence of ineligibility" in text, (
        "the absence-of-evidence rule is gone — a gap in the record would no "
        "longer be explicitly a reason to review rather than to deny"
    )
    assert "any uncertainty" in text and "in_review" in text, (
        "the uncertainty-routes-to-review governing rule is no longer stated"
    )


def test_every_deny_pattern_demands_a_cited_basis_and_an_appeal_pathway() -> None:
    """A denial the agent cannot justify in writing is not a denial.

    Every deny pattern must require the rationale to name the specific unmet
    criterion and to point at the appeal / exception route. That requirement is what
    makes a denial auditable and what gives the member a next step; it is also a
    practical brake, since a model that cannot write the citation must escalate
    instead. There is one deny pattern today (benefit-cap exhaustion); the
    assertion is written to hold as more are added.
    """
    text = _memory_text().lower()

    assert text.count("appeal") >= 2, (
        "fewer than two deny patterns reference an appeal pathway — a deny "
        "pattern that does not redirect to appeal leaves the member with no route"
    )
    assert "if you cannot write that cited rationale" in text, (
        "the 'no citation, no denial' brake has been removed"
    )
