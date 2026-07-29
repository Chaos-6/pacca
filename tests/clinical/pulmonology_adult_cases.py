"""
Adult pulmonology cases — iter-6 Batch E (adult pulm depth to 5).

WHY THESE EXIST
---------------
At iter-5 close, adult pulmonology had ZERO cases (GC-012 was the only
pulm case and it was pediatric). This file adds 5 cases to establish
within-specialty signal across adult asthma biologics, COPD escalation,
pulmonary rehab, sleep medicine, and biologic-class discrimination.

  GC-050  Adult severe eosinophilic asthma dupilumab    AUTO_APPROVED
  GC-051  COPD triple therapy after exacerbation        AUTO_APPROVED
  GC-052  Pulmonary rehab post-exacerbation             AUTO_APPROVED
  GC-053  CPAP initiation post-AASM sleep study         AUTO_APPROVED
  GC-054  Adult severe asthma mepolizumab (anti-IL-5)   AUTO_APPROVED

GC-050 vs GC-054 — both adult severe asthma biologics, different molecular
classes (anti-IL-4Rα vs anti-IL-5). Tests that the agent recognizes both
classes' criteria correctly and does not mis-apply class-specific rules.
"""

from __future__ import annotations

from tests.clinical.golden_cases import (
    EscalationBranch,
    ExpectedOutcome,
    GoldenCase,
)

PULMONOLOGY_ADULT_CASES: list[GoldenCase] = [
    # ─────────────────────────────────────────────────────────────────────────
    # GC-050 — Adult severe eosinophilic asthma — dupilumab (anti-IL-4R alpha).
    # ─────────────────────────────────────────────────────────────────────────
    GoldenCase(
        case_id="GC-050",
        title="Adult severe eosinophilic asthma — dupilumab per GINA + EMA label",
        diagnosis_code="J45.50",
        diagnosis_description="Severe persistent asthma, uncomplicated",
        procedure_code="J0517",
        procedure_description="Dupilumab (Dupixent) injection",
        clinical_notes=(
            "38-year-old female with severe persistent asthma despite "
            "high-dose ICS-LABA (fluticasone-salmeterol 500/50 BID) + "
            "tiotropium × 6 months. Eosinophil count 480 cells/uL "
            "(elevated; eosinophilic phenotype). FeNO 52 ppb (elevated). "
            "Two oral corticosteroid bursts in past 12 months for "
            "exacerbations. ACT score 16 (poorly controlled). Allergy panel "
            "positive for multiple aeroallergens. Pulmonologist requesting "
            "dupilumab as add-on biologic."
        ),
        guidelines_context=(
            "GINA 2024 + EMA dupilumab label: dupilumab (anti-IL-4Rα) is "
            "indicated for severe asthma in adults with type 2 inflammation "
            "(eosinophils ≥ 150 or FeNO ≥ 25) inadequately controlled on "
            "high-dose ICS-LABA. ≥ 2 exacerbations / year despite optimal "
            "controller therapy supports biologic step-up. No prior biologic "
            "exposure required."
        ),
        expected_outcome=ExpectedOutcome.AUTO_APPROVED,
        expected_branch=EscalationBranch.BRANCH_1_AUTO_APPROVE,
        reasoning_must_include=["severe", "eosinophil", "dupilumab"],
        reasoning_must_not_include=["pediatric only", "experimental"],
        clinical_rationale=(
            "Adult severe eosinophilic asthma meeting GINA criteria for "
            "biologic step-up. Eosinophils + FeNO confirm type 2 phenotype. "
            "Clean approve. The H2 memory entry for asthma dupilumab "
            "(iter-5 chg-4) should fire correctly here — this is the "
            "canonical positive case for that memory."
        ),
        judge_scoring_criteria=(
            "Score highly if rationale cites the eosinophil count, the "
            "controller-therapy failure, the exacerbation frequency, and "
            "the GINA criteria. Penalize for additional step-therapy demand "
            "or for escalation."
        ),
    ),
    # ─────────────────────────────────────────────────────────────────────────
    # GC-051 — COPD escalation to triple therapy per GOLD after exacerbation.
    # ─────────────────────────────────────────────────────────────────────────
    GoldenCase(
        case_id="GC-051",
        title="COPD triple therapy (LABA/LAMA/ICS) after exacerbation per GOLD",
        diagnosis_code="J44.1",
        diagnosis_description="Chronic obstructive pulmonary disease with (acute) exacerbation",
        procedure_code="J7686",
        procedure_description="Fluticasone/umeclidinium/vilanterol (Trelegy Ellipta) inhalation powder",
        clinical_notes=(
            "67-year-old male, GOLD Stage 3 COPD (FEV1 38% predicted), "
            "ex-smoker (40 pack-year). Currently on umeclidinium/vilanterol "
            "(LAMA/LABA) for past 12 months. Two moderate exacerbations and "
            "one hospitalization for severe exacerbation in past 12 months. "
            "Eosinophil count 280 cells/uL. Pulmonologist requesting "
            "escalation to triple therapy (fluticasone/umeclidinium/vilanterol)."
        ),
        guidelines_context=(
            "GOLD 2024 Report: in COPD patients on LAMA/LABA with "
            "exacerbations and eosinophil count ≥ 100 cells/uL, escalation "
            "to triple therapy (LABA/LAMA/ICS) is recommended (Group E). "
            "Single-inhaler triple therapy is preferred over multi-inhaler "
            "regimens for adherence. CMS coverage routine."
        ),
        expected_outcome=ExpectedOutcome.AUTO_APPROVED,
        expected_branch=EscalationBranch.BRANCH_1_AUTO_APPROVE,
        reasoning_must_include=["exacerbation", "eosinophil", "GOLD"],
        reasoning_must_not_include=["experimental", "first-line for new diagnosis"],
        clinical_rationale=(
            "Severe COPD on LAMA/LABA with continued exacerbations and "
            "eosinophilia ≥ 100 — textbook GOLD indication for triple "
            "therapy escalation. Clean approve."
        ),
        judge_scoring_criteria=(
            "Score highly if rationale cites the exacerbation history, "
            "eosinophil count, current regimen, and GOLD step-up criteria. "
            "Penalize for escalation or denial."
        ),
    ),
    # ─────────────────────────────────────────────────────────────────────────
    # GC-052 — Pulmonary rehabilitation post-exacerbation per ATS/ERS.
    # ─────────────────────────────────────────────────────────────────────────
    GoldenCase(
        case_id="GC-052",
        title="Pulmonary rehabilitation post-COPD-exacerbation — auto-approve per ATS",
        diagnosis_code="J44.9",
        diagnosis_description="Chronic obstructive pulmonary disease, unspecified",
        procedure_code="94625",
        procedure_description="Physician/QHP outpatient pulmonary rehabilitation, per session",
        clinical_notes=(
            "63-year-old female with GOLD Stage 2 COPD. Hospitalized 6 weeks "
            "ago for COPD exacerbation; discharged on optimized inhaler "
            "regimen. mMRC dyspnea scale 3. 6-minute walk distance 280 m "
            "(reduced). Referred to pulmonary rehabilitation: 8-week, "
            "2-sessions-per-week structured program with exercise, education, "
            "and psychosocial support. Patient motivated, transportation "
            "available."
        ),
        guidelines_context=(
            "ATS/ERS Statement on Pulmonary Rehabilitation + CMS NCD 240.8 "
            "(Pulmonary Rehabilitation): pulmonary rehab is indicated for "
            "COPD patients with mMRC ≥ 2 or post-exacerbation, with documented "
            "functional impairment. Coverage is routine when ordered by the "
            "treating physician at an approved facility."
        ),
        expected_outcome=ExpectedOutcome.AUTO_APPROVED,
        expected_branch=EscalationBranch.BRANCH_1_AUTO_APPROVE,
        reasoning_must_include=["pulmonary rehabilitation", "mMRC", "ATS"],
        # chg-25 review (F2): "denying" backfilled -- whole-word matching no
        # longer catches it implicitly from "deny".
        reasoning_must_not_include=["experimental", "deny", "denying"],
        clinical_rationale=(
            "Post-exacerbation COPD with documented functional impairment "
            "and physician referral. CMS NCD 240.8 criteria met. Clean "
            "approve."
        ),
        judge_scoring_criteria=(
            "Score highly if rationale cites mMRC, 6MWD, recent hospitalization, "
            "and the ATS/CMS criteria. Penalize for denial or escalation."
        ),
    ),
    # ─────────────────────────────────────────────────────────────────────────
    # GC-053 — CPAP initiation post-AASM-conformant sleep study.
    # ─────────────────────────────────────────────────────────────────────────
    GoldenCase(
        case_id="GC-053",
        title="CPAP for moderate OSA post-AASM-conformant home sleep study",
        diagnosis_code="G47.33",
        diagnosis_description="Obstructive sleep apnea (adult) (pediatric)",
        procedure_code="E0601",
        procedure_description="Continuous airway pressure (CPAP) device, with humidifier and accessories",
        clinical_notes=(
            "52-year-old male with excessive daytime sleepiness, witnessed "
            "apneas, and morning headaches. Epworth Sleepiness Scale 14 "
            "(severe sleepiness). BMI 33, neck circumference 17.5 inches. "
            "Home sleep apnea test (AASM-conformant Type III device): "
            "Apnea-Hypopnea Index 28/hr (moderate OSA), oxygen "
            "desaturation index 22/hr, nadir SpO₂ 84%. Sleep medicine "
            "recommending CPAP titration via auto-PAP."
        ),
        guidelines_context=(
            "AASM Clinical Practice Guideline for Diagnostic Testing for "
            "Adult OSA + CMS NCD 240.4 (CPAP for OSA): CPAP is indicated "
            "for AHI ≥ 15/hr, OR AHI 5-14/hr with associated symptoms "
            "(sleepiness, hypertension, stroke, ischemic heart disease). "
            "Home sleep apnea test (Type III) is acceptable for uncomplicated "
            "patients with high pretest probability. Initial CPAP authorization "
            "covers 90-day trial; ongoing coverage requires documented "
            "adherence (≥ 4 hr / night, ≥ 70% of nights)."
        ),
        expected_outcome=ExpectedOutcome.AUTO_APPROVED,
        expected_branch=EscalationBranch.BRANCH_1_AUTO_APPROVE,
        reasoning_must_include=["AHI", "CPAP", "AASM"],
        reasoning_must_not_include=["experimental", "in-lab required"],
        clinical_rationale=(
            "Moderate OSA documented on AASM-conformant home sleep study "
            "with symptoms supporting therapy. CMS NCD 240.4 criteria met. "
            "Clean approve for initial 90-day CPAP trial."
        ),
        judge_scoring_criteria=(
            "Score highly if rationale cites the AHI, the AASM-conformant "
            "study, and the CMS NCD criteria. Penalize for in-lab study "
            "demand (HSAT is acceptable here), denial, or escalation."
        ),
    ),
    # ─────────────────────────────────────────────────────────────────────────
    # GC-054 — Mepolizumab (anti-IL-5) for severe eosinophilic asthma.
    # Parallel to GC-050 (dupilumab) — different molecular class.
    # ─────────────────────────────────────────────────────────────────────────
    GoldenCase(
        case_id="GC-054",
        title="Adult severe eosinophilic asthma — mepolizumab (anti-IL-5)",
        diagnosis_code="J45.50",
        diagnosis_description="Severe persistent asthma, uncomplicated",
        procedure_code="J2182",
        procedure_description="Mepolizumab (Nucala) injection",
        clinical_notes=(
            "45-year-old male with severe eosinophilic asthma. High-dose "
            "ICS-LABA (budesonide-formoterol 320/9 BID) for 9 months + "
            "tiotropium. Eosinophil count 620 cells/uL on most recent CBC "
            "(consistently > 300 across three measurements over the past "
            "year). Three exacerbations requiring oral steroids in past 12 "
            "months. ACT score 13. No prior biologic exposure. Pulmonologist "
            "requesting mepolizumab."
        ),
        guidelines_context=(
            "GINA 2024 + EMA mepolizumab label: mepolizumab (anti-IL-5) is "
            "indicated for severe eosinophilic asthma in adults with blood "
            "eosinophils ≥ 150 cells/uL at baseline (some payer policies "
            "require ≥ 300) and ≥ 2 exacerbations / year despite high-dose "
            "ICS-LABA. Class distinct from dupilumab (anti-IL-4Rα); both "
            "are appropriate biologics for type-2-high severe asthma — "
            "choice typically driven by phenotype + provider preference."
        ),
        expected_outcome=ExpectedOutcome.AUTO_APPROVED,
        expected_branch=EscalationBranch.BRANCH_1_AUTO_APPROVE,
        reasoning_must_include=["eosinophil", "anti-IL-5", "mepolizumab"],
        reasoning_must_not_include=["dupilumab required first", "experimental"],
        clinical_rationale=(
            "Severe eosinophilic asthma with consistently elevated "
            "eosinophils > 300, multiple exacerbations, and inadequate "
            "control on optimal controller. Mepolizumab criteria fully met. "
            "Tests that the agent recognizes both anti-IL-5 and anti-IL-4Rα "
            "as appropriate first-line biologics without demanding a "
            "specific class sequence."
        ),
        judge_scoring_criteria=(
            "Score highly if rationale cites the eosinophil count, "
            "exacerbation history, controller failure, and the GINA "
            "biologic criteria. Penalize for demanding dupilumab as "
            "step-1 before mepolizumab (no such hierarchy exists), or "
            "for escalation."
        ),
    ),
]
