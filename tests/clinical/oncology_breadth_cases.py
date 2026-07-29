"""
Oncology breadth cases — 300-milestone Batch M (prevalence-weighted), pass 1.

WHY THESE EXIST
---------------
The iter-6 `oncology_depth_cases.py` (GC-083–088) covered six cancer
types as gap-closure: multiple myeloma, ovarian, head/neck, melanoma,
prostate, HCC. The 300-case milestone is *prevalence-weighted* (mirrors
a commercial-payer claim mix) rather than gap-closure, so this file adds
the high-incidence cancer types not yet represented at depth, with a
deliberate spread of outcomes to keep the eval honest.

  GC-101  Metastatic colorectal — FOLFIRI + bevacizumab        AUTO_APPROVED
  GC-102  HR+/HER2- metastatic breast — ribociclib + letrozole AUTO_APPROVED
  GC-103  Metastatic pancreatic 2nd-line, ECOG 3               IN_REVIEW
  GC-104  Metastatic clear-cell RCC — pembrolizumab + axitinib AUTO_APPROVED
  GC-105  Metastatic urothelial — EV monotherapy, wrong line   DENIED

Outcome spread: 3 AUTO_APPROVED, 1 IN_REVIEW, 1 DENIED. The two
non-approve cases probe distinct failure modes:
  - GC-103: an approved drug whose appropriateness is questionable for
    THIS patient (ECOG 3 — excluded from the registration trials;
    NCCN recommends best supportive care at this performance status).
    Tests that the system separates "is the drug guideline-listed" from
    "is it appropriate for this patient."
  - GC-105: the right drug at the wrong line. Enfortumab vedotin
    monotherapy is a later-line option; requesting it first-line in a
    treatment-naïve, cisplatin-eligible patient is a sequencing error.
    Tests step/sequence logic, not on/off-formulary.

GC-101 deliberately differs from GC-034 (off-label pancreatic nivolumab,
DENIED): there the drug had no compendia support; here every drug is
guideline-listed and the discriminator is patient-fit (GC-103) or
line-of-therapy (GC-105).
"""

from __future__ import annotations

from tests.clinical.golden_cases import (
    EscalationBranch,
    ExpectedOutcome,
    GoldenCase,
)

ONCOLOGY_BREADTH_CASES: list[GoldenCase] = [
    # ─────────────────────────────────────────────────────────────────────────
    # GC-101 — Metastatic colorectal cancer, FOLFIRI + bevacizumab first-line.
    # ─────────────────────────────────────────────────────────────────────────
    GoldenCase(
        case_id="GC-101",
        title="Metastatic colorectal — FOLFIRI + bevacizumab first-line per NCCN",
        diagnosis_code="C18.9",
        diagnosis_description="Malignant neoplasm of colon, unspecified",
        procedure_code="J9035",
        procedure_description="Bevacizumab (Avastin) injection",
        clinical_notes=(
            "59-year-old female with newly-diagnosed metastatic colorectal "
            "adenocarcinoma, synchronous liver metastases (unresectable). "
            "KRAS/NRAS/BRAF testing: KRAS G12D mutant (anti-EGFR therapy not "
            "indicated). MSI-stable. ECOG 1. Oncology recommending first-line "
            "FOLFIRI + bevacizumab. Adequate hepatic and renal function, no "
            "uncontrolled hypertension, no recent bleeding or GI perforation "
            "history."
        ),
        guidelines_context=(
            "NCCN Colon Cancer Guidelines: FOLFIRI + bevacizumab is a "
            "preferred (Category 1) first-line regimen for metastatic "
            "colorectal cancer. Bevacizumab is biomarker-agnostic; anti-VEGF "
            "benefit does not depend on RAS/BRAF status. KRAS-mutant disease "
            "specifically excludes anti-EGFR agents (cetuximab/panitumumab), "
            "making the bevacizumab-backbone choice appropriate."
        ),
        expected_outcome=ExpectedOutcome.AUTO_APPROVED,
        expected_branch=EscalationBranch.BRANCH_1_AUTO_APPROVE,
        reasoning_must_include=["bevacizumab", "first-line", "NCCN"],
        reasoning_must_not_include=["experimental", "off-label"],
        clinical_rationale=(
            "Category 1 first-line regimen for mCRC. KRAS-mutant status "
            "correctly steers away from anti-EGFR toward the bevacizumab "
            "backbone. Complete documentation, no contraindications. Clean "
            "approve."
        ),
        judge_scoring_criteria=(
            "Score highly if the rationale cites NCCN Category 1 for "
            "FOLFIRI + bevacizumab AND recognizes that KRAS-mutant status "
            "makes the bevacizumab (not anti-EGFR) backbone the correct "
            "choice. Penalize for flagging the KRAS mutation as a denial "
            "reason — it is a treatment-selection input, not a barrier here."
        ),
    ),
    # ─────────────────────────────────────────────────────────────────────────
    # GC-102 — HR+/HER2- metastatic breast cancer, CDK4/6i + AI first-line.
    # ─────────────────────────────────────────────────────────────────────────
    GoldenCase(
        case_id="GC-102",
        title="HR+/HER2- metastatic breast — ribociclib + letrozole first-line",
        diagnosis_code="C50.911",
        diagnosis_description="Malignant neoplasm of unspecified site of right female breast",
        procedure_code="J8499",
        procedure_description="Ribociclib (Kisqali) oral tablets",
        clinical_notes=(
            "62-year-old postmenopausal female with hormone-receptor-positive "
            "(ER 95%, PR 80%), HER2-negative (IHC 0) metastatic breast cancer "
            "with bone and limited liver metastases. ECOG 0. No prior therapy "
            "for metastatic disease. Baseline ECG QTc 430 ms (normal), LFTs "
            "within normal limits. Oncology recommending first-line ribociclib "
            "+ letrozole."
        ),
        guidelines_context=(
            "NCCN Breast Cancer Guidelines: a CDK4/6 inhibitor (ribociclib, "
            "palbociclib, or abemaciclib) combined with an aromatase inhibitor "
            "is the preferred (Category 1) first-line therapy for HR+/HER2- "
            "metastatic breast cancer in postmenopausal patients. The "
            "MONALEESA-2 trial established ribociclib + letrozole in this "
            "setting. Baseline and on-treatment QTc and LFT monitoring are "
            "standard; baseline values here are within range."
        ),
        expected_outcome=ExpectedOutcome.AUTO_APPROVED,
        expected_branch=EscalationBranch.BRANCH_1_AUTO_APPROVE,
        reasoning_must_include=["CDK4/6", "aromatase", "first-line"],
        reasoning_must_not_include=["experimental", "HER2-positive"],
        clinical_rationale=(
            "Textbook Category 1 indication: HR+/HER2- metastatic breast "
            "cancer, postmenopausal, first-line, CDK4/6i + AI. Receptor "
            "status confirmed, baseline QTc/LFTs support ribociclib safety "
            "monitoring. Clean approve."
        ),
        judge_scoring_criteria=(
            "Score highly if the rationale confirms HR+/HER2- status and "
            "cites CDK4/6 inhibitor + aromatase inhibitor as Category 1 "
            "first-line. Penalize for any confusion treating this as "
            "HER2-positive (which would route to anti-HER2 therapy instead)."
        ),
    ),
    # ─────────────────────────────────────────────────────────────────────────
    # GC-103 — Metastatic pancreatic, 2nd-line nal-IRI, ECOG 3 (appropriateness).
    # The drug is guideline-listed; the patient's performance status is the
    # discriminator. Approved-drug ≠ appropriate-for-this-patient.
    # ─────────────────────────────────────────────────────────────────────────
    GoldenCase(
        case_id="GC-103",
        title="Pancreatic 2nd-line nal-IRI at ECOG 3 — performance-status review",
        diagnosis_code="C25.9",
        diagnosis_description="Malignant neoplasm of pancreas, unspecified",
        procedure_code="J9205",
        procedure_description="Irinotecan liposome (Onivyde) injection",
        clinical_notes=(
            "70-year-old male with metastatic pancreatic adenocarcinoma, "
            "progression after first-line gemcitabine + nab-paclitaxel. "
            "Performance status has declined to ECOG 3 (in bed > 50% of "
            "waking hours, requires assistance with most activities). "
            "Ongoing cancer cachexia with 14% body-weight loss over 8 weeks, "
            "albumin 2.6 g/dL. Oncology requesting second-line nanoliposomal "
            "irinotecan + 5-FU/leucovorin. Patient and family wish to "
            "'try everything.'"
        ),
        guidelines_context=(
            "NCCN Pancreatic Adenocarcinoma Guidelines: nanoliposomal "
            "irinotecan + 5-FU/leucovorin is a Category 1 second-line option "
            "after gemcitabine-based therapy (NAPOLI-1). HOWEVER, NCCN "
            "explicitly limits systemic second-line therapy to patients with "
            "preserved performance status (ECOG 0–2); for ECOG 3–4, best "
            "supportive care / hospice is the recommended pathway. NAPOLI-1 "
            "enrolled ECOG 0–1 patients; efficacy and tolerability at ECOG 3 "
            "are unestablished and toxicity risk is high."
        ),
        expected_outcome=ExpectedOutcome.IN_REVIEW,
        expected_branch=EscalationBranch.BRANCH_2_MEDICAL_DIRECTOR,
        reasoning_must_include=["ECOG", "performance status", "second-line"],
        reasoning_must_not_include=["off-label", "experimental"],
        clinical_rationale=(
            "The drug itself is Category 1 for this line of therapy, so this "
            "is NOT an off-label or non-compendia denial. The genuine "
            "question is patient appropriateness: at ECOG 3 with cachexia and "
            "hypoalbuminemia, the patient falls outside the population in "
            "which benefit was demonstrated, and NCCN steers ECOG 3 toward "
            "supportive care. This is a values-laden, patient-specific "
            "judgment (family wishes vs. likely net harm) that warrants "
            "medical-director review rather than an automated approve or "
            "deny."
        ),
        judge_scoring_criteria=(
            "Score highly if the rationale (1) acknowledges the regimen is "
            "guideline-listed for second-line, (2) identifies ECOG 3 + "
            "cachexia as the appropriateness concern that places the patient "
            "outside the trial population, and (3) routes to medical-director "
            "review rather than auto-approving or hard-denying. Penalize for "
            "treating this as off-label (it is not) or for auto-approving on "
            "the drug's guideline status alone without weighing performance "
            "status."
        ),
    ),
    # ─────────────────────────────────────────────────────────────────────────
    # GC-104 — Metastatic clear-cell RCC, pembrolizumab + axitinib first-line.
    # ─────────────────────────────────────────────────────────────────────────
    GoldenCase(
        case_id="GC-104",
        title="Metastatic clear-cell RCC — pembrolizumab + axitinib first-line",
        diagnosis_code="C64.9",
        diagnosis_description="Malignant neoplasm of unspecified kidney, except renal pelvis",
        procedure_code="J9271",
        procedure_description="Pembrolizumab (Keytruda) injection",
        clinical_notes=(
            "64-year-old male with metastatic clear-cell renal cell carcinoma, "
            "pulmonary and nodal metastases. IMDC intermediate risk (1 risk "
            "factor: time from diagnosis to systemic therapy < 1 year). "
            "ECOG 1. No active autoimmune disease, no prior immunotherapy. "
            "Oncology recommending first-line pembrolizumab + axitinib."
        ),
        guidelines_context=(
            "NCCN Kidney Cancer Guidelines: pembrolizumab + axitinib is a "
            "preferred (Category 1) first-line regimen for advanced clear-cell "
            "RCC across all IMDC risk groups (favorable, intermediate, poor), "
            "established by KEYNOTE-426. Absence of active autoimmune disease "
            "supports checkpoint-inhibitor eligibility."
        ),
        expected_outcome=ExpectedOutcome.AUTO_APPROVED,
        expected_branch=EscalationBranch.BRANCH_1_AUTO_APPROVE,
        reasoning_must_include=["clear-cell", "first-line", "IMDC"],
        reasoning_must_not_include=["experimental", "autoimmune"],
        clinical_rationale=(
            "Category 1 first-line IO + TKI combination for metastatic "
            "clear-cell RCC. IMDC risk documented, no checkpoint-inhibitor "
            "contraindication. Clean approve."
        ),
        judge_scoring_criteria=(
            "Score highly if the rationale cites pembrolizumab + axitinib as "
            "Category 1 first-line for clear-cell RCC and notes the absence "
            "of autoimmune contraindication. Penalize for requiring a TKI "
            "monotherapy trial first — combination IO + TKI is the guideline "
            "first-line, not a step-up after monotherapy."
        ),
    ),
    # ─────────────────────────────────────────────────────────────────────────
    # GC-105 — Metastatic urothelial, enfortumab vedotin MONOTHERAPY requested
    # first-line in a treatment-naïve, cisplatin-eligible patient. Right drug,
    # wrong line — a sequencing denial (distinct from off-label / formulary).
    # ─────────────────────────────────────────────────────────────────────────
    GoldenCase(
        case_id="GC-105",
        title="Urothelial — enfortumab vedotin monotherapy first-line, wrong line of therapy",
        diagnosis_code="C67.9",
        diagnosis_description="Malignant neoplasm of bladder, unspecified",
        procedure_code="J9177",
        procedure_description="Enfortumab vedotin (Padcev) injection",
        clinical_notes=(
            "68-year-old male with newly-diagnosed metastatic urothelial "
            "carcinoma of the bladder, treatment-naïve (no prior systemic "
            "therapy). Renal function adequate (eGFR 65 mL/min), no hearing "
            "loss or significant neuropathy — cisplatin-eligible. ECOG 1. "
            "Oncology requesting enfortumab vedotin MONOTHERAPY as first-line "
            "systemic therapy."
        ),
        guidelines_context=(
            "NCCN Bladder Cancer Guidelines: for cisplatin-eligible, "
            "treatment-naïve metastatic urothelial carcinoma, preferred "
            "first-line therapy is enfortumab vedotin + pembrolizumab "
            "(EV-302) or platinum-based combination chemotherapy. Enfortumab "
            "vedotin MONOTHERAPY is indicated in LATER lines — after prior "
            "platinum-containing chemotherapy AND a PD-1/PD-L1 inhibitor, or "
            "for platinum-ineligible patients who have received a PD-1/PD-L1 "
            "inhibitor. EV monotherapy is not a guideline-supported first-line "
            "option for a treatment-naïve, cisplatin-eligible patient."
        ),
        expected_outcome=ExpectedOutcome.DENIED,
        expected_branch=EscalationBranch.NONE,
        reasoning_must_include=["first-line", "monotherapy", "sequenc"],
        # chg-25 review (F2): "appropriately"/"appropriateness" backfilled --
        # whole-word matching no longer catches them implicitly from
        # "appropriate".
        reasoning_must_not_include=["approved", "appropriate", "appropriately", "appropriateness"],
        clinical_rationale=(
            "This is a line-of-therapy (sequencing) error, not an off-label "
            "or formulary issue. Enfortumab vedotin is an on-label, "
            "guideline-listed urothelial agent — but its monotherapy "
            "indication is for later lines. In a treatment-naïve, "
            "cisplatin-eligible patient the guideline first-line is EV + "
            "pembrolizumab or platinum-based chemotherapy. The correct action "
            "is to deny the EV-monotherapy request and redirect to a "
            "guideline-concordant first-line regimen."
        ),
        judge_scoring_criteria=(
            "Score highly if the rationale identifies that EV monotherapy is "
            "a later-line option and that this treatment-naïve, "
            "cisplatin-eligible patient should receive EV + pembrolizumab or "
            "platinum-based chemotherapy first-line, and denies on that "
            "sequencing basis. Penalize for denying as 'off-label' or "
            "'not covered' (the drug is on-label, just mis-sequenced) and for "
            "approving on the drug's general urothelial indication without "
            "checking line of therapy."
        ),
    ),
]
