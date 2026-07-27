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

Teaching note — judge prompt design:

  The judge prompt is the most important prompt in the evaluation system.
  Poor judge prompts produce inconsistent scores that don't correlate
  with actual quality. Good judge prompts:
    1. Define the scoring rubric explicitly (not just "rate 1-5")
    2. Specify what GOOD looks like vs. what BAD looks like
    3. Include anti-patterns (hallucination, circular reasoning)
    4. Ask for structured output (not narrative)
    5. Remind the judge that the scored system is fallible — don't be lenient

  The judge uses the SAME golden case context (guidelines, expected outcome,
  judge_scoring_criteria) that the human expert used when designing the case.
  This grounds the evaluation in domain knowledge, not just surface features.
"""

import json
import logging
from collections.abc import Set as AbstractSet
from dataclasses import dataclass

from anthropic import AsyncAnthropic

from .golden_cases import GoldenCase
from .holdout import HELD_OUT_CASE_IDS

logger = logging.getLogger(__name__)

# Minimum acceptable accuracy rate for the CI gate
# If the system scores below this on the golden dataset, CI fails.
MINIMUM_ACCEPTABLE_ACCURACY = 0.80  # 80% of cases must score >= 3

# Minimum score on 1-5 scale to count as a "pass" for a case
MINIMUM_PASSING_SCORE = 3


@dataclass
class JudgeVerdict:
    """
    The output of one LLM-as-judge evaluation.

    Attributes:
        case_id:          Which golden case was evaluated
        score:            1-5 quality score
                            1 = Critically wrong (wrong decision, hallucination)
                            2 = Substantially wrong (right decision, wrong reasoning)
                            3 = Acceptable (correct decision, adequate reasoning)
                            4 = Good (correct decision, good reasoning, minor gaps)
                            5 = Excellent (correct decision, complete reasoning, no gaps)
        passed:           True if score >= MINIMUM_PASSING_SCORE
        judge_reasoning:  Why the judge gave this score
        correct_outcome:  Whether the system produced the expected outcome
        hallucination_detected: Whether the agent invented clinical details
        missing_citations:      Key guideline references the agent failed to cite
        raw_response:     Full judge response for debugging
    """

    case_id: str
    score: int
    passed: bool
    judge_reasoning: str
    correct_outcome: bool
    hallucination_detected: bool
    missing_citations: list[str]
    raw_response: str = ""


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
        hallucinations:  Cases where agent invented clinical details
        failed_cases:    Case IDs that scored below passing threshold
        in_sample_total:    Verdicts for cases NOT in the declared holdout
                            (tests/clinical/holdout.py::HELD_OUT_CASE_IDS)
        in_sample_passed:   In-sample cases that scored >= MINIMUM_PASSING_SCORE
        accuracy_in_sample: in_sample_passed / in_sample_total (0.0 if none evaluated)
        held_out_total:     Verdicts for cases in the declared holdout
        held_out_passed:    Held-out cases that scored >= MINIMUM_PASSING_SCORE
        accuracy_held_out:  held_out_passed / held_out_total (0.0 if none evaluated)

    The in-sample/held-out split is reporting only — it does NOT change
    `accuracy` or `passed_ci_gate`, which remain computed over every verdict
    exactly as before. See `split_verdicts_by_holdout()` for the pure
    function that computes the split, and `tests/clinical/holdout.py` for
    why the split exists and how membership was chosen.
    """

    verdicts: list[JudgeVerdict]
    total_cases: int
    passed_cases: int
    accuracy: float
    passed_ci_gate: bool
    hallucinations: list[str]
    failed_cases: list[str]
    in_sample_total: int
    in_sample_passed: int
    accuracy_in_sample: float
    held_out_total: int
    held_out_passed: int
    accuracy_held_out: float

    def summary(self) -> str:
        """Return a human-readable summary for test output."""
        status = "PASSED" if self.passed_ci_gate else "FAILED"
        return (
            f"Clinical Evaluation: {status}\n"
            f"  Accuracy: {self.accuracy:.1%} "
            f"({self.passed_cases}/{self.total_cases} cases passed)\n"
            f"  CI threshold: {MINIMUM_ACCEPTABLE_ACCURACY:.0%}\n"
            f"  Accuracy (in-sample):  {self.accuracy_in_sample:.1%} "
            f"({self.in_sample_passed}/{self.in_sample_total} cases)\n"
            f"  Accuracy (held-out):   {self.accuracy_held_out:.1%} "
            f"({self.held_out_passed}/{self.held_out_total} cases)\n"
            f"  Hallucinations detected: {len(self.hallucinations)} cases\n"
            f"  Failed cases: {', '.join(self.failed_cases) if self.failed_cases else 'none'}"
        )


def split_verdicts_by_holdout(
    verdicts: list[JudgeVerdict], held_out_ids: AbstractSet[str]
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

    Returns:
        A 6-tuple: (in_sample_total, in_sample_passed, accuracy_in_sample,
        held_out_total, held_out_passed, accuracy_held_out). Accuracy is
        0.0 (not a ZeroDivisionError) for an empty subset.
    """
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

JUDGE_SYSTEM_PROMPT = """
You are a clinical AI evaluation expert reviewing the reasoning quality of an
automated prior authorization system. Your job is to score how well the system
reasoned about a clinical case.

You are EVALUATING the system's reasoning quality — you are NOT the system.
Be rigorous and objective. The system you are evaluating is known to make mistakes.

## Scoring Rubric

Score on a 1-5 integer scale:

5 — EXCELLENT
  - Correct decision (approve/deny/escalate as expected)
  - Reasoning is complete: cites specific guidelines by name and section
  - Addresses ALL relevant clinical criteria for the case type
  - No hallucinated information (all clinical details from the submission)
  - Confidence score is appropriate (high for clear cases, low for ambiguous ones)

4 — GOOD
  - Correct decision
  - Reasoning is mostly complete, minor citation or detail gaps
  - No hallucinated information
  - Confidence score is appropriate

3 — ACCEPTABLE
  - Correct decision
  - Reasoning is present but incomplete or vague
  - May lack specific guideline citations
  - No hallucinated information

2 — SUBSTANTIALLY WRONG
  - Wrong decision OR correct decision with seriously flawed reasoning
  - Example: approved a case by citing criteria the case doesn't meet
  - Example: denied a case without identifying the actual disqualifying factor
  - Hallucination MAY be present

1 — CRITICALLY WRONG
  - Wrong decision on a case with a clear correct answer
  - OR any hallucination detected (invented lab values, fabricated prior therapy,
    claimed test results not present in the submission)
  - This is the most serious failure — it indicates the system cannot be trusted

## Critical Anti-Patterns (automatic score of 1 or 2)

HALLUCINATION: If the system states a specific lab value, test result, medication
dose, or clinical finding that is NOT explicitly stated in the clinical notes —
that is hallucination and must be scored 1 (if decision is wrong) or 2 (if
decision happens to be right despite the hallucination).

CIRCULAR REASONING: "The case meets criteria because the criteria are met" —
no specific evidence cited.

GUIDELINE FABRICATION: Citing a guideline that was not in the provided context.

You MUST respond with valid JSON in exactly this schema:
{
  "score": <integer 1-5>,
  "correct_outcome": <true if system produced expected outcome, false otherwise>,
  "hallucination_detected": <true if agent invented clinical details, false otherwise>,
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

### Keywords that MUST appear in rationale for full credit:
{", ".join(case.reasoning_must_include)}

### Keywords that must NOT appear (hallucination markers):
{", ".join(case.reasoning_must_not_include) if case.reasoning_must_not_include else "none specified"}

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
        # Use a smaller, faster model for evaluation to reduce cost.
        # The judge task is structured scoring — claude-haiku is sufficient.
        self.judge_model = "claude-haiku-4-5-20251001"

    async def evaluate_case(
        self,
        case: GoldenCase,
        system_decision_status: str,
        system_rationale: str,
        system_confidence: float,
    ) -> JudgeVerdict:
        """
        Ask the LLM judge to evaluate one system decision.

        Args:
            case:                    The golden case with expected outcome
            system_decision_status:  What the system actually decided
            system_rationale:        The system's reasoning
            system_confidence:       The system's confidence score

        Returns:
            JudgeVerdict with score, reasoning, and failure analysis
        """
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
                correct_outcome=bool(verdict_data.get("correct_outcome", False)),
                hallucination_detected=bool(verdict_data.get("hallucination_detected", False)),
                missing_citations=verdict_data.get("missing_citations", []),
                raw_response=raw_text,
            )

        except json.JSONDecodeError as e:
            logger.error(
                "judge_json_parse_failed: case=%s error=%s",
                case.case_id,
                str(e),
            )
            # Return a failing verdict when the judge response is unparseable
            return JudgeVerdict(
                case_id=case.case_id,
                score=1,
                passed=False,
                judge_reasoning=f"Judge response could not be parsed: {e!s}",
                correct_outcome=False,
                hallucination_detected=False,
                missing_citations=[],
                raw_response=raw_text if "raw_text" in dir() else "",
            )

        except Exception as e:
            logger.error(
                "judge_evaluation_failed: case=%s error=%s",
                case.case_id,
                str(e),
            )
            return JudgeVerdict(
                case_id=case.case_id,
                score=1,
                passed=False,
                judge_reasoning=f"Evaluation failed: {e!s}",
                correct_outcome=False,
                hallucination_detected=False,
                missing_citations=[],
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

        hallucination_cases = [v.case_id for v in verdicts if v.hallucination_detected]
        failed_cases = [v.case_id for v in verdicts if not v.passed]

        (
            in_sample_total,
            in_sample_passed,
            accuracy_in_sample,
            held_out_total,
            held_out_passed,
            accuracy_held_out,
        ) = split_verdicts_by_holdout(verdicts, HELD_OUT_CASE_IDS)

        return EvaluationReport(
            verdicts=verdicts,
            total_cases=total,
            passed_cases=passed,
            accuracy=accuracy,
            passed_ci_gate=accuracy >= MINIMUM_ACCEPTABLE_ACCURACY,
            hallucinations=hallucination_cases,
            failed_cases=failed_cases,
            in_sample_total=in_sample_total,
            in_sample_passed=in_sample_passed,
            accuracy_in_sample=accuracy_in_sample,
            held_out_total=held_out_total,
            held_out_passed=held_out_passed,
            accuracy_held_out=accuracy_held_out,
        )
