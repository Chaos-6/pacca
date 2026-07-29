"""
Pediatric golden cases — iter-5 chg-2 (data prerequisite for chg-3
complexity-score model).

WHY THESE EXIST
---------------
Before iter-5, the entire 22-case dataset (20 GOLDEN + 2 NEAR_MISS) contained
exactly ONE pediatric case: GC-012 (14-year-old with severe persistent asthma,
expected outcome IN_REVIEW). One data point can defend a keyword heuristic
("if pediatric AND severity language matches 'severe' / 'moderate-to-severe'
/ 'complex' / 'critical' → escalate") but cannot found a score-based
discriminator. A complexity score model needs contrastive examples on both
sides of its threshold.

iter-5 chg-2 adds three pediatric cases that, together with GC-012, give the
chg-3 score model four data points across the discriminator's input space:

  GC-012  14yo  severe asthma         IN_REVIEW   (existing)
  GC-023  10yo  mild well-controlled  AUTO_APPROVED   (new — below threshold)
  GC-024  16yo  moderate Crohn's,     IN_REVIEW   (new — ambiguous; multiple
                  1 immunomodulator                    weights push it over
                  failure                              the threshold)
  GC-025   9yo  severe AD,            IN_REVIEW   (new — high score in a
                  failed topicals +                    different disease,
                  cyclosporine                         confirms model
                                                       generalizes)

PEDIATRIC_CASES is kept as a separate list from GOLDEN_CASES (mirroring the
iter-2 chg-3 NEAR_MISS_CASES precedent) so:
  - GOLDEN_CASES stays at 20 and test_dataset_has_twenty_cases still passes
  - The cases run as a discrete "pediatric discrimination suite" through
    the clinical-gate loop
  - The chg-3 complexity-score model has a named set to validate against
"""

from __future__ import annotations

from tests.clinical.golden_cases import (
    EscalationBranch,
    ExpectedOutcome,
    GoldenCase,
)

# =============================================================================
# PEDIATRIC CASES — contrastive examples for the chg-3 complexity-score model.
# Together with GC-012 (in GOLDEN_CASES), these give 4 pediatric data points
# spanning the discriminator's input space.
# =============================================================================

PEDIATRIC_CASES: list[GoldenCase] = [
    # ─────────────────────────────────────────────────────────────────────────
    # GC-023 — MILD pediatric asthma, well-controlled. The "definitely
    # don't escalate" negative class for the complexity-score model.
    # ─────────────────────────────────────────────────────────────────────────
    GoldenCase(
        case_id="GC-023",
        title="Pediatric mild asthma — well-controlled, routine ICS refill",
        diagnosis_code="J45.20",
        diagnosis_description="Mild intermittent asthma",
        procedure_code="J7613",
        procedure_description="Albuterol sulfate inhalation solution",
        clinical_notes=(
            "10-year-old female with mild intermittent asthma, well-controlled "
            "on low-dose fluticasone (88 mcg BID) for 18 months. Peak expiratory "
            "flow consistently within 90-100% of personal best. No emergency "
            "department visits in past year. Eosinophil count 180/uL (normal). "
            "FeNO 18 ppb (normal). Pulmonologist requesting refill of current "
            "regimen plus albuterol rescue inhaler."
        ),
        guidelines_context=(
            "GINA 2024 Step 2 management: low-dose ICS + as-needed SABA is "
            "first-line for mild persistent asthma. Continued use of the same "
            "controller regimen in a well-controlled patient is routine prior "
            "authorization with no additional review required. Pediatric "
            "patients on stable mild regimens do not require specialist "
            "escalation by policy."
        ),
        expected_outcome=ExpectedOutcome.AUTO_APPROVED,
        expected_branch=EscalationBranch.BRANCH_1_AUTO_APPROVE,
        reasoning_must_include=["mild", "well-controlled", "GINA"],
        # chg-25 review (F2): whole-word matching no longer catches
        # morphological variants implicitly ("escalated", "escalates") --
        # backfilled explicitly per docs/CASE_AUTHORING_GUIDE.md § 6.
        reasoning_must_not_include=["specialist required", "escalate", "escalated", "escalates"],
        clinical_rationale=(
            "Routine pediatric ICS refill in a well-controlled mild "
            "intermittent asthma case. No complexity drivers: mild severity, "
            "no prior failures, no comorbidities, single stable regimen. "
            "Complexity-score model should compute a low score (2: pediatric "
            "age +2, mild +0, no failures, no comorbidities). Below the "
            "pediatric escalation threshold of 3. Auto-approval is correct."
        ),
        judge_scoring_criteria=(
            "Score highly if rationale explicitly notes the case is mild and "
            "well-controlled, cites GINA-style step therapy, and concludes "
            "auto-approval is appropriate. Penalize if rationale escalates "
            "based solely on pediatric age without considering severity."
        ),
    ),
    # ─────────────────────────────────────────────────────────────────────────
    # GC-024 — MODERATE pediatric Crohn's, ambiguous case. The borderline
    # data point: complexity score should compute exactly at the threshold,
    # pushed over by the prior-failure weight. Tests that the model's
    # multi-feature aggregation works.
    # ─────────────────────────────────────────────────────────────────────────
    GoldenCase(
        case_id="GC-024",
        title="Pediatric moderate Crohn's — biologic after immunomodulator failure",
        diagnosis_code="K50.90",
        diagnosis_description="Crohn's disease, unspecified, without complications",
        procedure_code="J1745",
        procedure_description="Infliximab (Remicade) injection",
        clinical_notes=(
            "16-year-old male with moderate Crohn's disease (CDAI 240), "
            "ileocolonic involvement confirmed on colonoscopy. Prior failure "
            "of azathioprine after 6 months (inadequate clinical response; "
            "ongoing diarrhea and weight loss despite therapeutic dose). "
            "Prior failure of methotrexate trial. Pediatric "
            "gastroenterologist requesting infliximab as next-line biologic. "
            "Growth velocity below expected for age (comorbid growth delay)."
        ),
        guidelines_context=(
            "ECCO / ESPGHAN Pediatric Crohn's Guidelines 2024: anti-TNF "
            "biologics are appropriate after immunomodulator failure in "
            "moderate-to-severe pediatric Crohn's. Pediatric patients with "
            "growth delay may warrant earlier biologic introduction. However, "
            "pediatric IBD with active disease + multiple prior therapy "
            "failures + comorbid growth delay meets institutional criteria "
            "for specialist review prior to first biologic."
        ),
        expected_outcome=ExpectedOutcome.IN_REVIEW,
        expected_branch=EscalationBranch.BRANCH_2_MEDICAL_DIRECTOR,
        reasoning_must_include=["pediatric", "moderate", "prior failure"],
        reasoning_must_not_include=["auto-approve", "no review needed"],
        clinical_rationale=(
            "Ambiguous pediatric case — clinical criteria for biologic are "
            "met (prior immunomodulator failure documented) but multiple "
            "complexity factors (moderate severity, 2 prior failures, "
            "comorbid growth delay) push complexity-score model to >= "
            "pediatric threshold. Expected score 4: pediatric +2, moderate "
            "+1, 2+ failures +1, comorbidity hint +1 = 5 (capped). "
            "Above pediatric escalation threshold of 3. Specialist review "
            "appropriate before first biologic in a growing pediatric patient."
        ),
        judge_scoring_criteria=(
            "Score highly if rationale notes pediatric age, moderate "
            "severity, prior failures, and concludes specialist review is "
            "warranted. Penalize for auto-approval (single failure does "
            "establish biologic eligibility in adults but pediatric "
            "complexity drivers warrant review) or for denial (criteria "
            "are not unmet)."
        ),
    ),
    # ─────────────────────────────────────────────────────────────────────────
    # GC-025 — SEVERE pediatric atopic dermatitis on biologic. The "high
    # score in a different disease" case — confirms the complexity-score
    # model generalizes beyond asthma (GC-012).
    # ─────────────────────────────────────────────────────────────────────────
    GoldenCase(
        case_id="GC-025",
        title="Pediatric severe atopic dermatitis — dupilumab after multiple failures",
        diagnosis_code="L20.9",
        diagnosis_description="Atopic dermatitis, unspecified",
        procedure_code="J0517",
        procedure_description="Dupilumab (Dupixent) injection",
        clinical_notes=(
            "9-year-old female with severe refractory atopic dermatitis. "
            "EASI score 35 (severe). Body surface area involvement 45%. "
            "Failed topical corticosteroids x 6 months (inadequate response). "
            "Failed topical calcineurin inhibitors x 3 months (intolerance). "
            "Failed cyclosporine systemic trial x 4 months (inadequate "
            "response with concerning renal labs). History of concurrent "
            "asthma and food allergies (atopic march comorbidities). "
            "Pediatric dermatologist requesting dupilumab."
        ),
        guidelines_context=(
            "AAD Atopic Dermatitis Guidelines + EMA dupilumab label: "
            "dupilumab indicated for moderate-to-severe atopic dermatitis "
            "in patients age >= 6 years not adequately controlled with "
            "topical therapy or when topical therapy is medically "
            "inadvisable. Multiple prior systemic and topical failures "
            "establish biologic eligibility. Pediatric biologic initiation "
            "in patients with multiple atopic comorbidities requires "
            "specialist review per institutional policy."
        ),
        expected_outcome=ExpectedOutcome.IN_REVIEW,
        expected_branch=EscalationBranch.BRANCH_2_MEDICAL_DIRECTOR,
        reasoning_must_include=["severe", "pediatric", "prior failure"],
        reasoning_must_not_include=["criteria not met"],
        clinical_rationale=(
            "Severe pediatric AD with multiple prior failures in a child "
            "with atopic comorbidities. Complexity-score model should "
            "compute 5 (capped): pediatric +2, severe +2, 2+ prior "
            "failures +1, comorbidity hint +1. Above pediatric escalation "
            "threshold of 3. Specialist review confirms biologic "
            "appropriateness in a complex pediatric atopic case. Tests "
            "that the complexity-score model generalizes beyond asthma."
        ),
        judge_scoring_criteria=(
            "Score highly if rationale explicitly cites severe pediatric "
            "AD with multiple prior failures and concludes specialist "
            "review is warranted. Penalize for auto-approval (pediatric "
            "complexity warrants review) or for denial (biologic eligibility "
            "is established)."
        ),
    ),
]
