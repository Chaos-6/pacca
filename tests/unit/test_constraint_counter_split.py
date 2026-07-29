"""
Regression: the Tier-1 constraint counter must report MISSING and FORBIDDEN
separately.

Found by reading the first live clinical run under the chg-25 taxonomy
(2026-07-29): the report showed "Constraint violations (exact): 20 cases"
including GC-018 and GC-019 — which reads as though the fabrication traps
fired. They had not: fabrications were 0 and the sparse-trap gate passed, so
`forbidden_present` was empty for both and their entries came from MISSING
required keywords.

Omitting a required keyword is a completeness signal. Stating a forbidden one
is a safety/bias signal, and on the GC-018/019 traps it is a fabrication
signature. One merged count reproduces, one level down, the exact conflation
chg-25 removed from `hallucinations`.
"""

from tests.clinical.evaluator import ConstraintViolation, EvaluationReport


def _report(violations: list[ConstraintViolation]) -> EvaluationReport:
    return EvaluationReport(
        verdicts=[],
        total_cases=3,
        passed_cases=3,
        accuracy=1.0,
        passed_ci_gate=True,
        fabrications=[],
        constraint_violations=violations,
        ungrounded_citations=[],
        outcome_mismatches=[],
        failed_cases=[],
        in_sample_total=3,
        in_sample_passed=3,
        accuracy_in_sample=1.0,
        held_out_total=0,
        held_out_passed=0,
        accuracy_held_out=0.0,
    )


def test_summary_reports_missing_and_forbidden_separately() -> None:
    summary = _report(
        [
            ConstraintViolation(case_id="GC-018", missing=["PD-L1"], forbidden_present=[]),
            ConstraintViolation(case_id="GC-028", missing=[], forbidden_present=["age"]),
        ]
    ).summary()
    assert "Missing required keywords" in summary, summary
    assert "Forbidden keywords present" in summary, summary
    assert "Constraint violations (exact):" not in summary, summary


def test_each_case_appears_only_in_the_bucket_it_belongs_to() -> None:
    summary = _report(
        [
            ConstraintViolation(case_id="GC-018", missing=["PD-L1"], forbidden_present=[]),
            ConstraintViolation(case_id="GC-028", missing=[], forbidden_present=["age"]),
        ]
    ).summary()
    missing_line = next(ln for ln in summary.splitlines() if "Missing required" in ln)
    forbidden_line = next(ln for ln in summary.splitlines() if "Forbidden keywords" in ln)
    assert "GC-018" in missing_line and "GC-028" not in missing_line, missing_line
    assert "GC-028" in forbidden_line and "GC-018" not in forbidden_line, forbidden_line


def test_a_case_violating_both_appears_in_both() -> None:
    summary = _report(
        [ConstraintViolation(case_id="GC-099", missing=["x"], forbidden_present=["y"])]
    ).summary()
    assert summary.count("GC-099") == 2, summary
