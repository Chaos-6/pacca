"""
LLM-as-judge evaluator for PACCA clinical decision quality.

This module implements the evaluation framework specified in Week 4 of
the v2.2 development sprint. It uses Claude to evaluate the quality of
authorization decisions produced by the PACCA agent pipeline.

Teaching note — why LLM-as-judge instead of rule-based scoring?

  Rule-based scoring can check whether specific strings appear in a
  rationale. It cannot evaluate whether the REASONING is sound.

  Example:
    Rule-based check: "PD-L1" in rationale → PASS
    What actually matters: did the agent correctly interpret PD-L1 62%
    as meeting the >= 50% threshold, and did it correctly note that
    this is the specific threshold that enables Category 1 recommendation?

  An LLM judge can evaluate reasoning quality in a way that no string
  matching can. The judge reads the same clinical context as the
  Decision Agent and asks: "Given what was in front of this agent,
  did it reason correctly?"

  The judge produces a structured score (1-5) with a specific rationale
  explaining what was done well and what was done poorly. This is both
  machine-processable (for CI thresholds) and human-readable (for
  debugging and portfolio demonstration).

Teaching note — three tiers, by what the check actually is (chg-25 / D5):

  Before chg-25, this module asked the LLM judge to decide THREE
  categorically different things through one self-reported boolean
  (``hallucination_detected``): (1) whether a case-specific forbidden
  keyword appeared in the rationale (pure string containment — a regex
  answers this exactly, every time), (2) whether the decision's cited
  evidence resolves to the submission (also exact — the production
  detector ``agents/evidence_grounding.py::unresolved_cited_evidence``
  already answers this deterministically for the runtime path), and
  (3) whether the rationale fabricated a clinical fact not present in the
  submission (this one genuinely requires judgment).

  Observed instability (2026-07-28, GC-028, two adjacent clinical-gate
  runs, byte-identical system behavior — AUTO_APPROVED both times, same
  cited criteria): the judge scored the SAME rationale a 2 ("critical
  anti-pattern") at one run and a 1 ("critical hallucination") at the
  next, because the prompt showed the judge GC-028's forbidden keywords
  under the header "Keywords that must NOT appear (hallucination
  markers)" — teaching the judge to conflate a keyword hit with a
  fabrication. The aggregate ``hallucinations`` count moved 0 -> 1 with
  no system change. See docs/EVALUATION.md for the full account.

  chg-25 separates these by what each check actually is:
    Tier 1 (deterministic, exact, zero variance) — ``check_reasoning_
      constraints()``: keyword presence/absence. The judge never sees
      these keywords anymore; it cannot mislabel what it is never shown.
    Tier 2 (deterministic, exact) — ``outcome_matches_expected()`` and
      ``check_evidence_grounding()`` (a thin wrapper around the real
      runtime detector, so eval and runtime agree by construction).
    Tier 3 (judgment, genuinely irreducible) — the 1-5 reasoning-quality
      score, and ``fabrication_detected`` under a narrow, counter-example-
      bearing definition (see ``JUDGE_SYSTEM_PROMPT``).

  ``EvaluationReport.hallucinations`` is REMOVED, not aliased: a field
  whose meaning changed must not keep its old name. Three independent
  counters replace it: ``fabrications`` (judge, Tier 3),
  ``constraint_violations`` (exact, Tier 1), ``ungrounded_citations``
  (exact, Tier 2). A fourth, ``outcome_mismatches`` (exact, Tier 2), is
  also exposed for completeness, though it is not one of the three
  headline safety counters.

Teaching note — judge prompt design:

  The judge prompt is the most important prompt in the evaluation system.
  Poor judge prompts produce inconsistent scores that don't correlate
  with actual quality. Good judge prompts:
    1. Define the scoring rubric explicitly (not just "rate 1-5")
    2. Specify what GOOD looks like vs. what BAD looks like
    3. Include a narrow, counter-example-bearing definition of fabrication
    4. Ask for structured output (not narrative)
    5. Remind the judge that the scored system is fallible — don't be lenient
    6. Never hand the judge a check a deterministic function already answers

  The judge uses the SAME golden case context (guidelines, expected outcome,
  judge_scoring_criteria) that the human expert used when designing the case.
  This grounds the evaluation in domain knowledge, not just surface features.

Teaching note — self-preference posture (chg-25 / D5 §3.3):

  The judge model (``claude-haiku-4-5-20251001``) and the generator model
  (PACCA's DecisionAgent runs on ``claude-sonnet-4-5-20250929``, see
  ``settings.anthropic_model``) are deliberately DIFFERENT models. This is
  a real mitigation for the self-enhancement bias documented in LLM-as-
  judge literature (a judge rating its own family's output more favorably
  than an independent judge would). It is currently true by accident of
  the cost-driven choice to use a cheaper model for evaluation; chg-25
  records it as a choice made ON PURPOSE going forward — do not "upgrade"
  the judge to the same model family as the generator without re-reading
  this note. Caveat: a weaker judge grading a stronger generator has its
  own ceiling — it can fail to recognize reasoning sophistication it
  cannot itself produce. Different-model judging trades that ceiling for
  freedom from self-preference; both biases exist, and this module can
  only mitigate one of them at a time.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from anthropic import AsyncAnthropic

from pacca.agents.evidence_grounding import unresolved_cited_evidence
from pacca.config import get_logger

from .golden_cases import ExpectedOutcome, GoldenCase
from .holdout import HELD_OUT_CASE_IDS, IN_SAMPLE_CASE_IDS

if TYPE_CHECKING:
    from collections.abc import Set as AbstractSet

    from pacca.models.authorization import AuthorizationDecision
    from pacca.models.clinical import ClinicalCase

logger = get_logger(__name__)

# Minimum acceptable accuracy rate for the CI gate
# If the system scores below this on the golden dataset, CI fails.
MINIMUM_ACCEPTABLE_ACCURACY = 0.80  # 80% of cases must score >= 3

# Minimum score on 1-5 scale to count as a "pass" for a case
MINIMUM_PASSING_SCORE = 3


# =============================================================================
# Tier 1 — deterministic, exact, zero variance (chg-25)
# =============================================================================


@dataclass
class ConstraintCheck:
    """
    Tier 1 result: which of a case's keyword constraints the rationale
    violates, computed in plain Python — never asked of the judge.

    Matching is case-insensitive SUBSTRING containment, not word-boundary
    matching. This is a deliberate, carried-over behavior: it reproduces
    exactly what the removed judge-prompt keyword sections did (plain
    Python ``in`` containment on the lowercased text), so moving the check
    out of the judge changes WHO decides it, not WHAT the decision is. The
    consequence is real and worth stating plainly: a short forbidden
    keyword matches inside a longer word too — "age" matches inside
    "average", "coverage", "manage". Case authors should choose keywords
    specific enough to avoid this (see docs/CASE_AUTHORING_GUIDE.md).
    GC-028's forbidden ``["age", "elderly"]`` is a real, documented
    instance where this fires on a rationale that correctly discusses the
    patient's (real, submitted) age to explain why it does NOT exclude
    treatment — see ``test_gc_028_...`` for the regression this produces.
    """

    missing: list[str] = field(default_factory=list)
    forbidden_present: list[str] = field(default_factory=list)

    @property
    def has_violation(self) -> bool:
        return bool(self.missing or self.forbidden_present)


def check_reasoning_constraints(case: GoldenCase, rationale: str) -> ConstraintCheck:
    """
    Tier 1 exact check: does ``rationale`` contain every keyword in
    ``case.reasoning_must_include`` and none of ``case.reasoning_must_not_
    include``? Case-insensitive substring containment (see ``ConstraintCheck``
    docstring for why substring, not word-boundary).

    Empty or absent keyword lists produce no violations — ``GoldenCase``
    defaults both fields to ``[]`` (never ``None``), so this never raises;
    a defensively-passed ``None`` behaves the same as ``[]`` via ``or ()``.
    """
    rationale_lower = rationale.lower()
    must_include = case.reasoning_must_include or ()
    must_not_include = case.reasoning_must_not_include or ()

    missing = [kw for kw in must_include if kw.lower() not in rationale_lower]
    forbidden_present = [kw for kw in must_not_include if kw.lower() in rationale_lower]

    return ConstraintCheck(missing=missing, forbidden_present=forbidden_present)


# =============================================================================
# Tier 2 — deterministic, exact (chg-25)
# =============================================================================


def outcome_matches_expected(expected: ExpectedOutcome, actual_status: str) -> bool:
    """
    Tier 2 exact check: does the system's actual decision status match the
    case's expected outcome? Computed in Python, never asked of the judge.

    ``ExpectedOutcome.PRE_FLIGHT_ESCALATE`` is a special case: PACCA's
    pre-flight branches (experimental treatment, rare condition, conflicting
    guidelines, prior denial) all surface as ``AuthorizationStatus.IN_REVIEW``
    at the status level (see ``_run_cases_through_pipeline`` in
    test_clinical_accuracy.py) — there is no separate "pre-flight-escalated"
    status. So a case declaring PRE_FLIGHT_ESCALATE is satisfied by an
    actual status of "IN_REVIEW", exactly like a case declaring IN_REVIEW
    directly.
    """
    if expected is ExpectedOutcome.PRE_FLIGHT_ESCALATE:
        return actual_status == "IN_REVIEW"
    return actual_status == expected.value


def check_evidence_grounding(decision: AuthorizationDecision, case: ClinicalCase) -> list[str]:
    """
    Tier 2 exact check: which of ``decision.cited_evidence_ids`` do NOT
    resolve to a submission ``EvidenceItem`` on ``case``.

    This is a thin, deliberately trivial wrapper around
    ``agents/evidence_grounding.py::unresolved_cited_evidence`` — the
    production runtime detector that forces human review on an ungrounded
    citation (P-5 / chg-10). Reusing it directly (rather than
    reimplementing the same containment check here) means the evaluation
    harness and the runtime detector can never silently drift apart on the
    same input — they call the identical function.
    """
    return unresolved_cited_evidence(decision, case)


# =============================================================================
# Report dataclasses
# =============================================================================


@dataclass
class ConstraintViolation:
    """One case whose rationale violated a Tier 1 keyword constraint."""

    case_id: str
    missing: list[str]
    forbidden_present: list[str]


@dataclass
class JudgeVerdict:
    """
    The output of one case's full evaluation: Tier 1 + Tier 2 (deterministic,
    computed in Python) plus Tier 3 (the judge's score and fabrication call).

    Attributes:
        case_id:             Which golden case was evaluated
        score:                1-5 quality score (Tier 3, judge)
                                1 = Critically wrong (wrong decision, fabrication)
                                2 = Substantially wrong reasoning
                                3 = Acceptable (adequate reasoning)
                                4 = Good (minor gaps)
                                5 = Excellent (complete reasoning, no gaps)
        passed:              True if score >= MINIMUM_PASSING_SCORE
        judge_reasoning:      Why the judge gave this score (Tier 3, judge)
        correct_outcome:      Tier 2, DETERMINISTIC — see `outcome_matches_
                              expected()`. No longer asked of the judge.
        fabrication_detected: Tier 3, judge — narrow definition, see
                              `JUDGE_SYSTEM_PROMPT`. Renamed from
                              `hallucination_detected` (chg-25): the old name
                              and the old prompt taught the judge to conflate
                              a keyword-constraint hit with a fabricated fact.
        missing_citations:    Key guideline references the agent failed to
                              cite (Tier 3, judge — a reasoning-quality
                              observation, not the same thing as evidence
                              grounding).
        raw_response:         Full judge response for debugging
        judge_model:          Provenance (chg-25 / D5 §3.2) — which model
                              produced this verdict, so a disputed score is
                              re-examinable months later.
        judge_prompt_version: Provenance — `JUDGE_PROMPT_VERSION` at the time
                              this verdict was produced.
        constraint_check:     Tier 1, DETERMINISTIC — see
                              `check_reasoning_constraints()`.
        ungrounded_citations: Tier 2, DETERMINISTIC — cited evidence ids that
                              do not resolve to a submission EvidenceItem, per
                              `check_evidence_grounding()`. Empty when no
                              decision/case pair was supplied to `evaluate_
                              case()` (e.g. a pre-flight-escalated case, which
                              has no `AuthorizationDecision` to check).
    """

    case_id: str
    score: int
    passed: bool
    judge_reasoning: str
    correct_outcome: bool
    fabrication_detected: bool
    missing_citations: list[str]
    raw_response: str = ""
    judge_model: str = ""
    judge_prompt_version: str = ""
    constraint_check: ConstraintCheck = field(default_factory=ConstraintCheck)
    ungrounded_citations: list[str] = field(default_factory=list)


@dataclass
class EvaluationReport:
    """
    Aggregate results from running the full golden dataset evaluation.

    Attributes:
        verdicts:        Individual verdict for each case
        total_cases:     Total cases evaluated
        passed_cases:    Cases that scored >= MINIMUM_PASSING_SCORE
        accuracy:        passed_cases / total_cases
        passed_ci_gate:  True if accuracy >= MINIMUM_ACCEPTABLE_ACCURACY
        fabrications:        Tier 3 (judge) — case ids where the judge found
                             a fabricated clinical fact. See module docstring
                             for why this replaces `hallucinations` under a
                             new name rather than keeping the old one.
        constraint_violations: Tier 1 (exact) — cases whose rationale
                             violated a `reasoning_must_include` /
                             `reasoning_must_not_include` keyword constraint.
        ungrounded_citations:  Tier 2 (exact) — case ids with at least one
                             cited evidence id absent from the submission.
        outcome_mismatches:    Tier 2 (exact) — case ids where the actual
                             decision status did not match the case's
                             expected outcome. Exposed for completeness;
                             not one of the three headline safety counters.
        failed_cases:    Case IDs that scored below passing threshold
        in_sample_total:    Verdicts for cases NOT in the declared holdout
                            (tests/clinical/holdout.py::HELD_OUT_CASE_IDS)
        in_sample_passed:   In-sample cases that scored >= MINIMUM_PASSING_SCORE
        accuracy_in_sample: in_sample_passed / in_sample_total (0.0 if none evaluated)
        held_out_total:     Verdicts for cases in the declared holdout
        held_out_passed:    Held-out cases that scored >= MINIMUM_PASSING_SCORE
        accuracy_held_out:  held_out_passed / held_out_total (0.0 if none evaluated)
        judge_model:         Provenance — the judge model used for this run's
                             verdicts (empty string if `verdicts` is empty).
        judge_prompt_version: Provenance — the judge prompt version used.

    The in-sample/held-out split is reporting only — it does NOT change
    `accuracy` or `passed_ci_gate`, which remain computed over every verdict
    exactly as before. See `split_verdicts_by_holdout()` for the pure
    function that computes the split, and `tests/clinical/holdout.py` for
    why the split exists and how membership was chosen.

    NOTE (chg-25): `hallucinations` is REMOVED, not aliased. A field whose
    meaning changed (from "one judge-reported boolean conflating three
    failure modes" to "nothing — it no longer exists") must not keep its
    old name. Code that read `report.hallucinations` fails loudly (
    AttributeError) rather than silently reading a renamed-but-reused field
    with different semantics; that loud failure is the point.
    """

    verdicts: list[JudgeVerdict]
    total_cases: int
    passed_cases: int
    accuracy: float
    passed_ci_gate: bool
    fabrications: list[str]
    constraint_violations: list[ConstraintViolation]
    ungrounded_citations: list[str]
    outcome_mismatches: list[str]
    failed_cases: list[str]
    in_sample_total: int
    in_sample_passed: int
    accuracy_in_sample: float
    held_out_total: int
    held_out_passed: int
    accuracy_held_out: float
    judge_model: str = ""
    judge_prompt_version: str = ""

    def summary(self) -> str:
        """Return a human-readable summary for test output."""
        status = "PASSED" if self.passed_ci_gate else "FAILED"
        constraint_ids = [cv.case_id for cv in self.constraint_violations]
        return (
            f"Clinical Evaluation: {status}\n"
            f"  Accuracy: {self.accuracy:.1%} "
            f"({self.passed_cases}/{self.total_cases} cases passed)\n"
            f"  CI threshold: {MINIMUM_ACCEPTABLE_ACCURACY:.0%}\n"
            f"  Accuracy (in-sample):  {_render_subset_accuracy(self.accuracy_in_sample, self.in_sample_passed, self.in_sample_total)}\n"
            f"  Accuracy (held-out):   {_render_subset_accuracy(self.accuracy_held_out, self.held_out_passed, self.held_out_total)}\n"
            f"  Fabrications (judge):            {len(self.fabrications)} cases{_render_case_list(self.fabrications)}\n"
            f"  Constraint violations (exact):   {len(constraint_ids)} cases{_render_case_list(constraint_ids)}\n"
            f"  Ungrounded citations (exact):    {len(self.ungrounded_citations)} cases{_render_case_list(self.ungrounded_citations)}\n"
            f"  Outcome mismatches (exact):      {len(self.outcome_mismatches)} cases{_render_case_list(self.outcome_mismatches)}\n"
            f"  Failed cases: {', '.join(self.failed_cases) if self.failed_cases else 'none'}"
        )


def _render_subset_accuracy(accuracy: float, passed: int, total: int) -> str:
    """
    Render a subset's accuracy for summary() output.

    "0.0% (0/0 cases)" reads as "evaluated and failed everything" — it is
    indistinguishable from a real 0% accuracy on a non-empty subset. Render
    the zero-cases case as "n/a" instead, since no verdicts were ever
    evaluated for that subset (e.g. accuracy_held_out before any test runs
    the pipeline over HELD_OUT_CASE_IDS).
    """
    if total == 0:
        return "n/a (0 evaluated)"
    return f"{accuracy:.1%} ({passed}/{total} cases)"


def _render_case_list(case_ids: list[str]) -> str:
    """Render an optional ' [GC-001, GC-002]' suffix; empty string when none."""
    if not case_ids:
        return ""
    return f" [{', '.join(case_ids)}]"


def split_verdicts_by_holdout(
    verdicts: list[JudgeVerdict],
    held_out_ids: AbstractSet[str],
    known_ids: AbstractSet[str] | None = None,
) -> tuple[int, int, float, int, int, float]:
    """
    Partition verdicts into in-sample vs held-out and compute each subset's
    pass count and accuracy.

    A pure function over its arguments — no dataset import, no API calls —
    so it can be unit-tested with synthetic `JudgeVerdict`s and an arbitrary
    `held_out_ids` set (see tests/unit/test_evaluator_holdout_split.py).
    `compile_report()` calls this with the real
    `tests.clinical.holdout.HELD_OUT_CASE_IDS`; the split does not affect
    `EvaluationReport.accuracy` or `passed_ci_gate`, which are still computed
    over every verdict exactly as before this feature was added.

    Args:
        verdicts:      All verdicts to partition (by `JudgeVerdict.case_id`)
        held_out_ids:  Case ids considered held-out; everything else is in-sample
        known_ids:     Optional — the full set of valid case ids (in-sample +
                       held-out combined). When provided, any verdict whose
                       case_id is in neither `held_out_ids` nor `known_ids`
                       (e.g. a typo, or a case from a list the split doesn't
                       know about) is logged as a warning. It still counts
                       toward in-sample — "not held-out" is the only
                       determination this function can make on membership
                       alone — but the mismatch is never silent.

    Returns:
        A 6-tuple: (in_sample_total, in_sample_passed, accuracy_in_sample,
        held_out_total, held_out_passed, accuracy_held_out). Accuracy is
        0.0 (not a ZeroDivisionError) for an empty subset.
    """
    if known_ids is not None:
        unrecognized = {v.case_id for v in verdicts} - held_out_ids - known_ids
        for case_id in sorted(unrecognized):
            logger.warning(
                "unrecognized_case_id_in_holdout_split",
                case_id=case_id,
                reason=(
                    "case_id is in neither HELD_OUT_CASE_IDS nor the known "
                    "in-sample/held-out dataset; counted as in-sample by "
                    "default, but this likely indicates a typo or a case "
                    "list the holdout split doesn't know about"
                ),
            )

    in_sample = [v for v in verdicts if v.case_id not in held_out_ids]
    held_out = [v for v in verdicts if v.case_id in held_out_ids]

    in_sample_total = len(in_sample)
    in_sample_passed = sum(1 for v in in_sample if v.passed)
    accuracy_in_sample = in_sample_passed / in_sample_total if in_sample_total > 0 else 0.0

    held_out_total = len(held_out)
    held_out_passed = sum(1 for v in held_out if v.passed)
    accuracy_held_out = held_out_passed / held_out_total if held_out_total > 0 else 0.0

    return (
        in_sample_total,
        in_sample_passed,
        accuracy_in_sample,
        held_out_total,
        held_out_passed,
        accuracy_held_out,
    )


# =============================================================================
# Judge prompt — the core of the evaluation system
# =============================================================================

# PROMPT_REGISTRY-style version constant for the judge prompt (D5 §3.2).
# Bump this any time JUDGE_SYSTEM_PROMPT or the shape of the judge's JSON
# contract changes, so a stored verdict's `judge_prompt_version` tells a
# future reader exactly which prompt produced it.
JUDGE_PROMPT_VERSION = "v2.0"

JUDGE_SYSTEM_PROMPT = """
You are a clinical AI evaluation expert reviewing the reasoning quality of an
automated prior authorization system. Your job is to score how well the system
reasoned about a clinical case.

You are EVALUATING the system's reasoning quality — you are NOT the system.
Be rigorous and objective. The system you are evaluating is known to make mistakes.

You are being asked for exactly TWO things: a 1-5 reasoning-quality score, and
whether the rationale FABRICATED a clinical fact. Everything else about this
case — whether the decision matches the expected outcome, whether specific
phrases are required or forbidden, whether cited evidence exists in the
submission — is checked separately by exact, deterministic code that never
varies. Do not attempt to judge any of those; they are not part of your score
and you have not been shown case-specific phrase lists because they are not
your job.

## Scoring Rubric

Score on a 1-5 integer scale:

5 — EXCELLENT
  - Reasoning is complete: cites specific guidelines by name and section
  - Addresses ALL relevant clinical criteria for the case type
  - No fabricated clinical information
  - Confidence score is appropriate (high for clear cases, low for ambiguous ones)

4 — GOOD
  - Reasoning is mostly complete, minor citation or detail gaps
  - No fabricated clinical information
  - Confidence score is appropriate

3 — ACCEPTABLE
  - Reasoning is present but incomplete or vague
  - May lack specific guideline citations
  - No fabricated clinical information

2 — SUBSTANTIALLY WRONG REASONING
  - Reasoning is seriously flawed even where the bottom-line decision looks
    right
  - Example: reaches a decision by citing criteria the case doesn't actually
    meet
  - Example: fails to identify the actual disqualifying (or qualifying) factor
  - Circular reasoning: "the criteria are met because the criteria are met" —
    no specific evidence cited
  - Citing a guideline that was not in the provided context

1 — FABRICATION
  - The rationale asserts a clinical fact that does not appear anywhere in
    the submission: a lab value, a prior therapy, a test result, a
    diagnostic finding — presented as if it came from the notes when it did
    not.
  - This is the most serious failure — it indicates the system cannot be
    trusted.

## What counts as FABRICATION — narrow definition, read carefully

FABRICATION means: the rationale states a SPECIFIC clinical detail (a number,
a named prior treatment, a named test result, a named finding) that is NOT
present anywhere in the clinical notes given to you above, and presents it as
if it came from the submission.

Example of fabrication: the notes say only "patient has lung cancer" and the
rationale states "PD-L1 TPS of 62%" — that number was invented; it is not in
the notes.

## What does NOT count as fabrication (read this before scoring a 1)

- Restating or discussing a fact that IS present in the clinical notes —
  even a fact the reasoning arguably should not have dwelt on — is NOT
  fabrication. Example: the notes state the patient is 82 years old. A
  rationale that says "the patient's age does not exclude this therapy" is
  discussing a real, submitted fact, not inventing one. Whether the
  rationale SHOULD have mentioned it at all may be worth reflecting in your
  1-5 score, but it is never FABRICATION — nothing was invented.
- Drawing a correct clinical inference from data that IS present (e.g.
  concluding "eGFR is adequate" from a stated creatinine and a stated
  formula) is not fabrication.
- Citing a guideline that IS present in the guidelines context above.

If you are unsure whether a detail is fabricated, check the clinical notes
above for it verbatim before answering. If it is there in substance, it is
not fabrication, regardless of anything else about the rationale.

You MUST respond with valid JSON in exactly this schema:
{
  "score": <integer 1-5>,
  "fabrication_detected": <true ONLY under the narrow definition above, false otherwise>,
  "missing_citations": ["list of key guidelines the agent failed to cite"],
  "judge_reasoning": "2-4 sentences explaining the score"
}
""".strip()


def _build_judge_prompt(
    case: GoldenCase,
    system_decision_status: str,
    system_rationale: str,
    system_confidence: float,
) -> str:
    """
    Build the user-turn prompt for the judge.

    The judge receives:
      1. The clinical case (what was submitted)
      2. The guidelines (what the agent had access to)
      3. The expected outcome (what the correct answer is)
      4. The agent's actual decision and rationale
      5. Case-specific scoring criteria from the dataset designer

    chg-25: the case's `reasoning_must_include` / `reasoning_must_not_include`
    keyword lists are DELIBERATELY NOT included here anymore. Showing the
    judge a "hallucination markers" keyword list is what caused it to
    conflate an exact, deterministic keyword hit with a genuine fabrication
    (see module docstring). Those checks now run in
    `check_reasoning_constraints()`, entirely outside this prompt — the
    judge cannot mislabel what it is never shown.

    Args:
        case:                    The golden case being evaluated
        system_decision_status:  What the system decided (e.g. "AUTO_APPROVED")
        system_rationale:        The agent's full rationale text
        system_confidence:       The agent's confidence score

    Returns:
        Formatted judge prompt string
    """
    return f"""
## Case to Evaluate: {case.case_id} — {case.title}

### Clinical Case
- **Diagnosis:** {case.diagnosis_code} — {case.diagnosis_description}
- **Procedure:** {case.procedure_code} — {case.procedure_description}
- **Clinical Notes:**
{case.clinical_notes}

### Guidelines Available to the System
{case.guidelines_context}

### Expected Outcome (Ground Truth)
- **Expected Decision:** {case.expected_outcome.value}
- **Expert Rationale:** {case.clinical_rationale}

### Case-Specific Scoring Criteria
{case.judge_scoring_criteria}

### System's Actual Decision
- **Status:** {system_decision_status}
- **Confidence:** {system_confidence:.2f}
- **Rationale:**
{system_rationale}

Evaluate this decision and provide your verdict as JSON.
""".strip()


# =============================================================================
# The evaluator class
# =============================================================================


class ClinicalEvaluator:
    """
    Runs LLM-as-judge evaluation against the golden dataset.

    Usage:
        evaluator = ClinicalEvaluator()
        verdict = await evaluator.evaluate_case(case, decision_status, rationale, confidence)
        report  = await evaluator.evaluate_dataset(results)
    """

    def __init__(self, api_key: str | None = None) -> None:
        import os

        self.client = AsyncAnthropic(api_key=api_key or os.getenv("ANTHROPIC_API_KEY") or "")
        # Use a smaller, faster model for evaluation to reduce cost, AND
        # deliberately a DIFFERENT model family from the generator
        # (DecisionAgent runs claude-sonnet-4-5) — see the module docstring's
        # "self-preference posture" note (D5 §3.3).
        self.judge_model = "claude-haiku-4-5-20251001"

    async def evaluate_case(
        self,
        case: GoldenCase,
        system_decision_status: str,
        system_rationale: str,
        system_confidence: float,
        decision: AuthorizationDecision | None = None,
        clinical_case: ClinicalCase | None = None,
    ) -> JudgeVerdict:
        """
        Evaluate one system decision: Tier 1 + Tier 2 checks run here in
        plain Python (never asked of the judge); the judge is asked only for
        the Tier 3 score and fabrication call.

        Args:
            case:                    The golden case with expected outcome
            system_decision_status:  What the system actually decided
            system_rationale:        The system's reasoning
            system_confidence:       The system's confidence score
            decision:                Optional real `AuthorizationDecision` —
                                     when supplied together with
                                     `clinical_case`, enables the Tier 2
                                     evidence-grounding check. `None` for
                                     pre-flight-escalated cases, which have
                                     no `AuthorizationDecision` to check
                                     ("where recorded" — see D5 §2 Tier 2).
            clinical_case:           Optional real `ClinicalCase` — the
                                     submission `decision`'s citations are
                                     checked against.

        Returns:
            JudgeVerdict with score, reasoning, and failure analysis
        """
        constraint_check = check_reasoning_constraints(case, system_rationale)
        correct_outcome = outcome_matches_expected(case.expected_outcome, system_decision_status)
        ungrounded_citations = (
            check_evidence_grounding(decision, clinical_case)
            if decision is not None and clinical_case is not None
            else []
        )

        prompt = _build_judge_prompt(
            case=case,
            system_decision_status=system_decision_status,
            system_rationale=system_rationale,
            system_confidence=system_confidence,
        )

        try:
            response = await self.client.messages.create(
                model=self.judge_model,
                max_tokens=1024,
                temperature=0.0,  # Deterministic evaluation
                system=JUDGE_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )

            # Anthropic SDK content[0] is a union of block types; only TextBlock
            # has .text. The /v1/messages call always returns a TextBlock here
            # (no tools / thinking enabled), so the type-ignore is safe in practice.
            # iter-future: switch to explicit isinstance(block, TextBlock) check.
            raw_text = response.content[0].text if response.content else "{}"  # type: ignore[union-attr,unused-ignore]

            # Parse structured JSON response
            # Strip markdown code fences if present
            clean_text = raw_text.strip()
            if clean_text.startswith("```"):
                lines = clean_text.split("\n")
                clean_text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

            verdict_data = json.loads(clean_text)

            score = int(verdict_data.get("score", 1))
            score = max(1, min(5, score))  # Clamp to 1-5

            return JudgeVerdict(
                case_id=case.case_id,
                score=score,
                passed=score >= MINIMUM_PASSING_SCORE,
                judge_reasoning=verdict_data.get("judge_reasoning", "No reasoning provided"),
                correct_outcome=correct_outcome,
                fabrication_detected=bool(verdict_data.get("fabrication_detected", False)),
                missing_citations=verdict_data.get("missing_citations", []),
                raw_response=raw_text,
                judge_model=self.judge_model,
                judge_prompt_version=JUDGE_PROMPT_VERSION,
                constraint_check=constraint_check,
                ungrounded_citations=ungrounded_citations,
            )

        except json.JSONDecodeError as e:
            logger.error("judge_json_parse_failed", case_id=case.case_id, error=str(e))
            # Return a failing verdict when the judge response is unparseable.
            # correct_outcome / constraint_check / ungrounded_citations stay
            # deterministic even here — they never depended on the judge call
            # succeeding.
            return JudgeVerdict(
                case_id=case.case_id,
                score=1,
                passed=False,
                judge_reasoning=f"Judge response could not be parsed: {e!s}",
                correct_outcome=correct_outcome,
                fabrication_detected=False,
                missing_citations=[],
                raw_response=raw_text if "raw_text" in dir() else "",
                judge_model=self.judge_model,
                judge_prompt_version=JUDGE_PROMPT_VERSION,
                constraint_check=constraint_check,
                ungrounded_citations=ungrounded_citations,
            )

        except Exception as e:
            logger.error("judge_evaluation_failed", case_id=case.case_id, error=str(e))
            return JudgeVerdict(
                case_id=case.case_id,
                score=1,
                passed=False,
                judge_reasoning=f"Evaluation failed: {e!s}",
                correct_outcome=correct_outcome,
                fabrication_detected=False,
                missing_citations=[],
                judge_model=self.judge_model,
                judge_prompt_version=JUDGE_PROMPT_VERSION,
                constraint_check=constraint_check,
                ungrounded_citations=ungrounded_citations,
            )

    def compile_report(self, verdicts: list[JudgeVerdict]) -> EvaluationReport:
        """
        Compile individual verdicts into an aggregate evaluation report.

        Args:
            verdicts: List of JudgeVerdict from evaluate_case() calls

        Returns:
            EvaluationReport with accuracy, pass/fail, and failure analysis
        """
        total = len(verdicts)
        passed = sum(1 for v in verdicts if v.passed)
        accuracy = passed / total if total > 0 else 0.0

        fabrication_cases = [v.case_id for v in verdicts if v.fabrication_detected]
        constraint_violations = [
            ConstraintViolation(
                case_id=v.case_id,
                missing=v.constraint_check.missing,
                forbidden_present=v.constraint_check.forbidden_present,
            )
            for v in verdicts
            if v.constraint_check.has_violation
        ]
        ungrounded_citation_cases = [v.case_id for v in verdicts if v.ungrounded_citations]
        outcome_mismatch_cases = [v.case_id for v in verdicts if not v.correct_outcome]
        failed_cases = [v.case_id for v in verdicts if not v.passed]

        (
            in_sample_total,
            in_sample_passed,
            accuracy_in_sample,
            held_out_total,
            held_out_passed,
            accuracy_held_out,
        ) = split_verdicts_by_holdout(
            verdicts, HELD_OUT_CASE_IDS, known_ids=HELD_OUT_CASE_IDS | IN_SAMPLE_CASE_IDS
        )

        judge_model = verdicts[0].judge_model if verdicts else ""
        judge_prompt_version = verdicts[0].judge_prompt_version if verdicts else ""

        return EvaluationReport(
            verdicts=verdicts,
            total_cases=total,
            passed_cases=passed,
            accuracy=accuracy,
            passed_ci_gate=accuracy >= MINIMUM_ACCEPTABLE_ACCURACY,
            fabrications=fabrication_cases,
            constraint_violations=constraint_violations,
            ungrounded_citations=ungrounded_citation_cases,
            outcome_mismatches=outcome_mismatch_cases,
            failed_cases=failed_cases,
            in_sample_total=in_sample_total,
            in_sample_passed=in_sample_passed,
            accuracy_in_sample=accuracy_in_sample,
            held_out_total=held_out_total,
            held_out_passed=held_out_passed,
            accuracy_held_out=accuracy_held_out,
            judge_model=judge_model,
            judge_prompt_version=judge_prompt_version,
        )
