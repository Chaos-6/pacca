"""
OB / reproductive / pregnancy cases — iter-6 Batch J (OB depth to 6).

WHY THESE EXIST
---------------
At iter-5 close, OB / female-specific care had ZERO cases. Iter-6
expansion_cases added GC-033 (fertility preservation pre-chemo
AUTO_APPROVED). This file adds 5 more to reach 6 — coverage across
routine prenatal, prenatal screening, postpartum, gestational complications,
and contraception/sterilization with Medicaid-specific rules.

  GC-068  First-trimester ultrasound                    AUTO_APPROVED
  GC-069  NIPT in advanced maternal age                 AUTO_APPROVED
  GC-070  Postpartum depression brexanolone IV          IN_REVIEW (REMS)
  GC-071  Gestational diabetes insulin                  AUTO_APPROVED
  GC-072  Postpartum tubal ligation (Medicaid 30-day)   AUTO_APPROVED
"""

from __future__ import annotations

from tests.clinical.golden_cases import (
    EscalationBranch,
    ExpectedOutcome,
    GoldenCase,
)

OB_CASES: list[GoldenCase] = [
    # ─────────────────────────────────────────────────────────────────────────
    # GC-068 — First-trimester ultrasound (dating + viability).
    # ─────────────────────────────────────────────────────────────────────────
    GoldenCase(
        case_id="GC-068",
        title="First-trimester ultrasound — dating + viability per ACOG",
        diagnosis_code="Z34.81",
        diagnosis_description="Encounter for supervision of other normal pregnancy, first trimester",
        procedure_code="76801",
        procedure_description="Ultrasound, pregnant uterus, real-time with image documentation, first trimester",
        clinical_notes=(
            "31-year-old female (G2P1), positive home pregnancy test 4 weeks "
            "ago, last menstrual period uncertain by recall. Initial OB visit "
            "today. Routine first-trimester dating ultrasound requested to "
            "confirm gestational age, viability, and assess for multiple "
            "gestation. No symptoms of complication. No prior IUFD or "
            "ectopic. Standard prenatal care plan initiated."
        ),
        guidelines_context=(
            "ACOG Practice Bulletin on Ultrasonography in Pregnancy: routine "
            "first-trimester ultrasound for dating and viability is standard "
            "of care, particularly when LMP is uncertain. Authorization is "
            "routine. CMS Medicaid coverage for prenatal care is mandatory."
        ),
        expected_outcome=ExpectedOutcome.AUTO_APPROVED,
        expected_branch=EscalationBranch.BRANCH_1_AUTO_APPROVE,
        reasoning_must_include=["pregnancy", "ultrasound", "dating"],
        # chg-25 review (F2): "denying" backfilled -- whole-word matching no
        # longer catches it implicitly from "deny".
        reasoning_must_not_include=["experimental", "deny", "denying"],
        clinical_rationale=(
            "Routine prenatal first-trimester ultrasound. ACOG standard of care. Clean approve."
        ),
        judge_scoring_criteria=(
            "Score highly if rationale identifies this as routine prenatal "
            "imaging. Penalize for any escalation or denial."
        ),
    ),
    # ─────────────────────────────────────────────────────────────────────────
    # GC-069 — NIPT in advanced maternal age.
    # ─────────────────────────────────────────────────────────────────────────
    GoldenCase(
        case_id="GC-069",
        title="NIPT cell-free DNA screening in AMA (38yo) — auto-approve per ACOG/SMFM",
        diagnosis_code="O09.512",
        diagnosis_description="Supervision of elderly primigravida, second trimester",
        procedure_code="81420",
        procedure_description="Fetal chromosomal aneuploidy DNA sequence analysis (NIPT)",
        clinical_notes=(
            "38-year-old female (G1P0), 12 weeks pregnant by dating "
            "ultrasound. Advanced maternal age. No personal or family "
            "history of chromosomal abnormalities. No prior children with "
            "genetic conditions. Genetic counseling completed; patient "
            "electing non-invasive prenatal testing (cell-free DNA "
            "screening for common aneuploidies) over diagnostic invasive "
            "testing as initial screen. OB requesting NIPT."
        ),
        guidelines_context=(
            "ACOG / SMFM Joint Practice Bulletin: NIPT is appropriate "
            "screening for all pregnancies regardless of age, with stronger "
            "endorsement in patients with elevated baseline risk (advanced "
            "maternal age, prior aneuploid pregnancy, positive serum "
            "screening). Coverage is routine for AMA pregnancies."
        ),
        expected_outcome=ExpectedOutcome.AUTO_APPROVED,
        expected_branch=EscalationBranch.BRANCH_1_AUTO_APPROVE,
        reasoning_must_include=["NIPT", "screening", "AMA"],
        reasoning_must_not_include=["diagnostic required", "experimental"],
        clinical_rationale=(
            "NIPT in AMA pregnancy with genetic counseling completed. "
            "ACOG/SMFM-endorsed screening. Clean approve."
        ),
        judge_scoring_criteria=(
            "Score highly if rationale cites AMA, the ACOG/SMFM endorsement, "
            "and genetic counseling completion. Penalize for demanding "
            "diagnostic CVS/amniocentesis first (it's the patient's choice) "
            "or for denial."
        ),
    ),
    # ─────────────────────────────────────────────────────────────────────────
    # GC-070 — Postpartum depression brexanolone IV (REMS).
    # ─────────────────────────────────────────────────────────────────────────
    GoldenCase(
        case_id="GC-070",
        title="Postpartum depression — brexanolone IV per FDA REMS (IN_REVIEW)",
        diagnosis_code="F53.0",
        diagnosis_description="Postpartum depression",
        procedure_code="J1632",
        procedure_description="Brexanolone (Zulresso) injection",
        clinical_notes=(
            "27-year-old female, postpartum day 21 (delivered first child). "
            "Severe postpartum depression: Edinburgh Postnatal Depression "
            "Scale 22. Failed adequate trial of sertraline (4 weeks, "
            "minimal response). No active SI but reports daily depressed "
            "mood, anhedonia, sleep disturbance, intrusive negative "
            "thoughts about parenting. No bipolar history. Psychiatrist "
            "requesting brexanolone 60-hour continuous IV infusion at "
            "REMS-certified inpatient facility."
        ),
        guidelines_context=(
            "FDA-approved label (brexanolone) + ACOG Postpartum Care "
            "Recommendations: brexanolone is FDA-approved for moderate-to-"
            "severe postpartum depression in adults; administration requires "
            "60-hour continuous infusion under REMS program (Zulresso REMS) "
            "at a certified healthcare setting with continuous monitoring "
            "for sedation/loss of consciousness. Specialist review for "
            "REMS-program initiation is policy."
        ),
        expected_outcome=ExpectedOutcome.IN_REVIEW,
        expected_branch=EscalationBranch.BRANCH_2_MEDICAL_DIRECTOR,
        reasoning_must_include=["REMS", "certified", "specialist"],
        reasoning_must_not_include=["auto-approve", "outpatient"],
        clinical_rationale=(
            "Brexanolone is REMS-required. Clinical criteria for PPD are "
            "met (EPDS 22, SSRI failure), but REMS-program initiation + "
            "60-hour inpatient infusion requirement triggers specialist "
            "review. Not a denial — therapy may proceed."
        ),
        judge_scoring_criteria=(
            "Score highly if rationale identifies the REMS requirement and "
            "the certified-setting administration. Penalize for auto-approval "
            "(ignores REMS), denial (therapy is appropriate), or for "
            "treating it as outpatient (it's a 60-hour infusion)."
        ),
    ),
    # ─────────────────────────────────────────────────────────────────────────
    # GC-071 — Gestational diabetes insulin initiation.
    # ─────────────────────────────────────────────────────────────────────────
    GoldenCase(
        case_id="GC-071",
        title="Gestational diabetes — insulin initiation after diet failure per ACOG",
        diagnosis_code="O24.414",
        diagnosis_description="Gestational diabetes mellitus in pregnancy, insulin controlled",
        procedure_code="J1815",
        procedure_description="Insulin injection, per 5 units",
        clinical_notes=(
            "33-year-old female (G2P1), 28 weeks gestation. Gestational "
            "diabetes diagnosed at 26 weeks by 75-g OGTT (1-hour 195, "
            "2-hour 168). Initial management: medical nutrition therapy + "
            "exercise + glucose monitoring × 2 weeks. Glucose log shows "
            "60% of fasting values > 95 mg/dL and 50% of 1-hr postprandial "
            "values > 140 mg/dL despite adherence. OB/MFM recommending "
            "insulin initiation (NPH bedtime + meal-time aspart)."
        ),
        guidelines_context=(
            "ACOG Practice Bulletin on Gestational Diabetes: glycemic targets "
            "(fasting < 95, 1-hr postprandial < 140) must be achieved to "
            "minimize macrosomia + neonatal hypoglycemia risk. Medical "
            "nutrition therapy is first-line; pharmacologic therapy "
            "(insulin preferred; metformin acceptable second-line) is "
            "indicated when targets are not met. Insulin is the gold "
            "standard in pregnancy due to extensive safety data."
        ),
        expected_outcome=ExpectedOutcome.AUTO_APPROVED,
        expected_branch=EscalationBranch.BRANCH_1_AUTO_APPROVE,
        reasoning_must_include=["gestational diabetes", "insulin", "target"],
        reasoning_must_not_include=["experimental", "metformin required first"],
        clinical_rationale=(
            "GDM with documented failure of dietary management to achieve "
            "glycemic targets. Insulin is preferred pharmacologic therapy "
            "in pregnancy per ACOG. Clean approve."
        ),
        judge_scoring_criteria=(
            "Score highly if rationale cites the dietary trial, the glucose "
            "log failures, and the ACOG insulin preference. Penalize for "
            "demanding metformin step-up first (insulin is preferred in "
            "pregnancy) or for denial."
        ),
    ),
    # ─────────────────────────────────────────────────────────────────────────
    # GC-072 — Postpartum tubal ligation (Medicaid 30-day rule documented).
    # ─────────────────────────────────────────────────────────────────────────
    GoldenCase(
        case_id="GC-072",
        title="Postpartum tubal ligation — Medicaid 30-day sterilization consent documented",
        diagnosis_code="Z30.2",
        diagnosis_description="Encounter for sterilization",
        procedure_code="58611",
        procedure_description="Ligation or transection of fallopian tube(s) at time of cesarean or other intra-abdominal surgery",
        clinical_notes=(
            "34-year-old female (G4P3), Medicaid-insured. Currently 36 weeks "
            "pregnant. Sterilization consent form signed 35 days ago "
            "(satisfies Medicaid 30-day waiting period). Patient is requesting "
            "postpartum tubal ligation at the time of planned cesarean "
            "delivery (repeat C-section indicated by prior 2 C-sections). "
            "No coercion documented; counseling on permanence completed; "
            "patient confirms decision."
        ),
        guidelines_context=(
            "CMS Medicaid Sterilization Consent Requirements (42 CFR 441.250): "
            "Medicaid-funded sterilization requires (a) age ≥ 21, (b) "
            "informed-consent form signed ≥ 30 days but ≤ 180 days prior to "
            "the procedure, (c) not in labor or under emergent circumstances "
            "at time of consent, (d) not coerced. ACOG endorses postpartum "
            "tubal ligation when consent and counseling are properly "
            "documented."
        ),
        expected_outcome=ExpectedOutcome.AUTO_APPROVED,
        expected_branch=EscalationBranch.BRANCH_1_AUTO_APPROVE,
        reasoning_must_include=["sterilization", "consent", "30-day"],
        reasoning_must_not_include=["coerced", "incomplete consent"],
        clinical_rationale=(
            "Postpartum tubal ligation with all Medicaid sterilization-"
            "consent requirements satisfied (age, 30-day waiting period, "
            "appropriate consent, counseling, no coercion). Clean approve. "
            "Tests that the agent recognizes Medicaid-specific procedural "
            "consent rules that differ from commercial."
        ),
        judge_scoring_criteria=(
            "Score highly if rationale identifies the Medicaid 30-day rule "
            "explicitly and confirms the consent date satisfies it. "
            "Penalize for missing the Medicaid-specific rule, denying "
            "without checking consent, or escalation."
        ),
    ),
]
