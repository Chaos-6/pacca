"""
Regression tests for pre-flight escalation evasion.

Pre-flight is the deterministic gate that runs before any model call and is the
only thing standing between an experimental therapy and an autonomous approval
(C-HARD-01, SC-02). It decided by matching codes against curated lists using
exact membership and `startswith`, on values the submitter controls.

A red-team pass produced an AUTO_APPROVED experimental CAR-T submission with no
human review by chaining two ordinary-looking submissions:

    procedure_code          "Q2041-59"   a real distinct-procedural-service modifier
    estimated_annual_cost   0.0          a declared figure the notes contradict

Neither is exotic. The modifier is how the code is legitimately billed, and the
cost field is simply the submitter's own number. That is what makes these
defects rather than curiosities: a correctly-formed real claim would have
slipped the gate.

These tests pin the fix and would fail if either check regresses to trusting
the raw submitted value.
"""

import pytest

from pacca.agents.clinical_risk_detector import ClinicalRiskDetector, normalize_code
from pacca.models import ClinicalCase, EvidenceItem
from pacca.models.enums import EscalationReason

CAR_T_NOTES = (
    "Relapsed B-cell ALL, third line. Tisagenlecleucel (CAR-T) requested. "
    "Billed at $475,000 annually."
)


def _case(
    procedure_code: str,
    *,
    declared_cost: float | None,
    diagnosis_code: str = "C91.00",
    notes: str = CAR_T_NOTES,
) -> ClinicalCase:
    return ClinicalCase(
        patient_id="PT-EVASION",
        primary_diagnosis_code=diagnosis_code,
        procedure_code=procedure_code,
        estimated_annual_cost=declared_cost,
        patient_age=25,
        evidence=[
            EvidenceItem(
                id="EV-1",
                source_type="CLINICAL_NOTE",
                description="Oncology note",
                original_text=notes,
                confidence=0.9,
            )
        ],
    )


class TestExperimentalCodeNormalization:
    """C-HARD-01 / SC-02: a modifier must not defeat the experimental list."""

    @pytest.mark.parametrize(
        "procedure_code",
        [
            "Q2041",  # the honest baseline
            "Q2041-59",  # distinct procedural service modifier
            "Q2041 KX",  # documentation-on-file modifier
            "Q2041:26",  # professional component
            "Q2041.",  # stray punctuation
            "q2041-59",  # lowercase
            "Ｑ" + "2041",  # fullwidth Q homoglyph
        ],
    )
    def test_experimental_code_escalates_regardless_of_modifier(self, procedure_code: str) -> None:
        flags = ClinicalRiskDetector().evaluate(_case(procedure_code, declared_cost=475000.0))
        assert EscalationReason.EXPERIMENTAL_TREATMENT in flags.reasons, (
            f"C-HARD-01: procedure {procedure_code!r} is the experimental CAR-T code "
            "Q2041 with a billing modifier or homoglyph, and did not trip the "
            "experimental gate. An experimental therapy must never receive an "
            "autonomous decision."
        )

    def test_the_chained_bypass_is_closed(self) -> None:
        """
        The full red-team attack: modifier plus an understated declared cost.

        Each evasion alone left another branch to catch the case. Together they
        cleared pre-flight entirely and the case reached the decision agent,
        which auto-approved it at 0.99 confidence.
        """
        flags = ClinicalRiskDetector().evaluate(_case("Q2041-59", declared_cost=0.0))
        assert flags.should_pre_escalate, (
            "SC-02: an experimental CAR-T submission cleared pre-flight when the "
            "procedure code carried a modifier and the declared cost was 0.0. "
            "This is the chained bypass; it must escalate."
        )
        assert EscalationReason.EXPERIMENTAL_TREATMENT in flags.reasons


class TestHighCostCannotBeSuppressedBySubmitter:
    """The cost gate is a policy control; the submitter cannot switch it off."""

    def test_declared_cost_does_not_suppress_the_parsed_cost(self) -> None:
        flags = ClinicalRiskDetector().evaluate(
            _case("99213", declared_cost=0.0, diagnosis_code="M54.5")
        )
        assert EscalationReason.HIGH_COST in flags.reasons, (
            "A declared estimated_annual_cost of 0.0 suppressed the parse of "
            "'$475,000 annually' from the clinical record. Cost escalation exists "
            "to act against the submitter's incentive and cannot take their "
            "number on trust when the record disagrees."
        )

    def test_understatement_is_surfaced_to_the_reviewer(self) -> None:
        flags = ClinicalRiskDetector().evaluate(
            _case("99213", declared_cost=1000.0, diagnosis_code="M54.5")
        )
        detail = " ".join(flags.details.values())
        assert "below the" in detail and "475,000" in detail, (
            "When the declared cost is materially below the record, the reviewer "
            f"should see both figures. Detail was: {detail!r}"
        )

    def test_absent_cost_still_parses_from_notes(self) -> None:
        flags = ClinicalRiskDetector().evaluate(
            _case("99213", declared_cost=None, diagnosis_code="M54.5")
        )
        assert EscalationReason.HIGH_COST in flags.reasons

    @pytest.mark.parametrize("bad_cost", [-1.0, float("nan")])
    def test_nonsensical_declared_cost_is_discarded_not_trusted(self, bad_cost: float) -> None:
        flags = ClinicalRiskDetector().evaluate(
            _case("99213", declared_cost=bad_cost, diagnosis_code="M54.5")
        )
        assert EscalationReason.HIGH_COST in flags.reasons, (
            f"A declared cost of {bad_cost} is not a number to compare against a "
            "threshold. It must be discarded in favour of the parsed figure, not "
            "used to pass the gate."
        )


class TestMalformedCodesEscalate:
    """A code the curated lists could not have screened is not screened."""

    @pytest.mark.parametrize(
        "diagnosis_code",
        ["0E75.0", ".E75.0", "E7", "", "NOTACODE"],
    )
    def test_malformed_diagnosis_escalates(self, diagnosis_code: str) -> None:
        flags = ClinicalRiskDetector().evaluate(
            _case("99213", declared_cost=100.0, diagnosis_code=diagnosis_code, notes="Routine.")
        )
        assert flags.should_pre_escalate, (
            f"Diagnosis {diagnosis_code!r} is not a well-formed ICD-10 code, so no "
            "prefix scan can vouch for it. It must route to a human rather than "
            "proceed as though screened."
        )

    def test_well_formed_routine_case_does_not_escalate(self) -> None:
        """The shape gate must not escalate ordinary traffic."""
        flags = ClinicalRiskDetector().evaluate(
            _case(
                "72148",
                declared_cost=1200.0,
                diagnosis_code="M54.5",
                notes="Six weeks of conservative therapy completed. MRI lumbar spine ordered.",
            )
        )
        assert not flags.should_pre_escalate, (
            f"A routine, well-formed lumbar MRI escalated: {flags.reasons}. The "
            "shape gate must be invisible to valid traffic."
        )


class TestNormalizeCode:
    """The primitive the branches now share."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Q2041", "Q2041"),
            ("q2041-59", "Q204159"),
            ("Q2041 KX", "Q2041KX"),
            ("  M54.5  ", "M545"),
            ("Ｑ" + "2041", "Q2041"),
        ],
    )
    def test_normalization(self, raw: str, expected: str) -> None:
        assert normalize_code(raw) == expected
