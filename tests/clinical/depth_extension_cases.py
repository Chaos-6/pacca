"""
Depth-extension cases — iter-6 Batches M/N/O/P (per-specialty depth to 5).

WHY THESE EXIST
---------------
Several specialties had only 2 cases at iter-5 close — short of the 5-case
threshold for within-specialty regression signal. This file adds 3 cases
each across 4 specialties for a 12-case batch.

  Batch M — Adolescent (12-17) depth     GC-089, 090, 091
  Batch N — Imaging / radiology depth    GC-092, 093, 094
  Batch O — Dermatology depth            GC-095, 096, 097
  Batch P — GI depth                     GC-098, 099, 100

Reaching GC-100 closes the iter-6 dataset-growth arc and brings the total
to 100 cases — the production-pilot milestone per DATASET_SUFFICIENCY.md.
"""

from __future__ import annotations

from tests.clinical.golden_cases import (
    EscalationBranch,
    ExpectedOutcome,
    GoldenCase,
)

DEPTH_EXTENSION_CASES: list[GoldenCase] = [
    # ─────────────────────────────────────────────────────────────────────────
    # Batch M — Adolescent (12-17) depth (3 cases)
    # ─────────────────────────────────────────────────────────────────────────
    GoldenCase(
        case_id="GC-089",
        title="15yo with ulcerative colitis — mesalamine clean approve per ESPGHAN",
        diagnosis_code="K51.90",
        diagnosis_description="Ulcerative colitis, unspecified, without complications",
        procedure_code="J8499",
        procedure_description="Mesalamine 4.8 g/day oral",
        clinical_notes=(
            "15-year-old female with newly diagnosed mild-to-moderate "
            "ulcerative colitis confirmed by colonoscopy + biopsy. PUCAI "
            "score 22 (mild). No extraintestinal manifestations. Pediatric "
            "GI recommending oral mesalamine 4.8 g/day as first-line "
            "induction therapy."
        ),
        guidelines_context=(
            "ESPGHAN / NASPGHAN Pediatric UC Guidelines: oral 5-ASA is "
            "first-line induction for mild-to-moderate pediatric UC. "
            "Routine coverage."
        ),
        expected_outcome=ExpectedOutcome.AUTO_APPROVED,
        expected_branch=EscalationBranch.BRANCH_1_AUTO_APPROVE,
        reasoning_must_include=["pediatric", "UC", "mesalamine"],
        # chg-25 review (F2): "biologics" (plural) backfilled -- whole-word
        # matching no longer catches it implicitly from "biologic".
        reasoning_must_not_include=["biologic", "biologics", "experimental"],
        clinical_rationale="Mild pediatric UC — 5-ASA first-line per ESPGHAN. Clean approve.",
        judge_scoring_criteria="Score highly if rationale cites PUCAI + ESPGHAN. Should NOT over-escalate on pediatric age alone since severity is mild.",
    ),
    GoldenCase(
        case_id="GC-090",
        title="13yo with MDD severe — IN_REVIEW for pediatric psych specialist",
        diagnosis_code="F32.2",
        diagnosis_description="Major depressive disorder, single episode, severe without psychotic features",
        procedure_code="J8499",
        procedure_description="Escitalopram (Lexapro) 10 mg oral",
        clinical_notes=(
            "13-year-old male with severe MDD. PHQ-A 19. Passive death-wish "
            "ideation, no plan or intent. Family stress, declining grades. "
            "Pediatrician initiating escitalopram with weekly safety check-"
            "ins. No prior pharmacotherapy. Pediatric psychiatry referral "
            "pending."
        ),
        guidelines_context=(
            "AACAP + FDA: SSRI initiation in pediatric MDD requires "
            "pediatric psychiatry oversight per black-box suicidality "
            "warning. Escitalopram FDA-approved for adolescents 12+. "
            "Institutional policy: pediatric antidepressant initiation "
            "triggers specialist review."
        ),
        expected_outcome=ExpectedOutcome.IN_REVIEW,
        expected_branch=EscalationBranch.BRANCH_2_MEDICAL_DIRECTOR,
        reasoning_must_include=["pediatric", "black box", "specialist"],
        reasoning_must_not_include=["auto-approve"],
        clinical_rationale="Pediatric SSRI initiation triggers specialist review per black-box.",
        judge_scoring_criteria="Score highly if rationale cites pediatric + black-box + specialist gate.",
    ),
    GoldenCase(
        case_id="GC-091",
        title="16yo combined hormonal contraception — clean approve per ACOG/AAP",
        diagnosis_code="Z30.011",
        diagnosis_description="Encounter for initial prescription of contraceptive pills",
        procedure_code="J8499",
        procedure_description="Ethinyl estradiol/norgestimate (combined oral contraceptive)",
        clinical_notes=(
            "16-year-old female requesting contraception. Healthy, no "
            "smoking, no migraine with aura, no thromboembolic history. "
            "BP normal. BMI 22. Counseled on options; patient elects "
            "combined oral contraceptive pill. Confidentiality maintained "
            "per state minor-consent law (state permits minor consent for "
            "contraceptive services)."
        ),
        guidelines_context=(
            "ACOG + AAP Adolescent Contraception Guidance + CDC US Medical "
            "Eligibility Criteria: healthy adolescent without contraindications "
            "is Category 1 for combined hormonal contraception. State minor-"
            "consent laws govern confidentiality."
        ),
        expected_outcome=ExpectedOutcome.AUTO_APPROVED,
        expected_branch=EscalationBranch.BRANCH_1_AUTO_APPROVE,
        reasoning_must_include=["contraception", "adolescent", "ACOG"],
        reasoning_must_not_include=["parental consent required", "denied"],
        clinical_rationale="Routine adolescent contraception with no contraindications and minor-consent state.",
        judge_scoring_criteria="Score highly if rationale cites the CDC USMEC Category 1 and state minor-consent law. Penalize for demanding parental consent in a state where minor consent applies.",
    ),
    # ─────────────────────────────────────────────────────────────────────────
    # Batch N — Imaging / radiology depth (3 cases)
    # ─────────────────────────────────────────────────────────────────────────
    GoldenCase(
        case_id="GC-092",
        title="Knee MRI for suspected meniscal tear after PT failure per OARSI",
        diagnosis_code="S83.209A",
        diagnosis_description="Other tear of unspecified meniscus, current injury, initial encounter",
        procedure_code="73721",
        procedure_description="MRI lower extremity without contrast (knee)",
        clinical_notes=(
            "38-year-old male with persistent right knee pain x 8 weeks "
            "after twisting injury. Mechanical symptoms (locking, catching). "
            "PT × 6 weeks with persistent symptoms. Positive McMurray test "
            "on exam. Orthopedics requesting knee MRI to evaluate for "
            "meniscal tear before potential arthroscopy."
        ),
        guidelines_context=(
            "ACR Appropriateness Criteria for Acute Knee Trauma + OARSI: "
            "MRI is indicated for persistent mechanical symptoms after "
            "appropriate trial of conservative therapy. Coverage routine."
        ),
        expected_outcome=ExpectedOutcome.AUTO_APPROVED,
        expected_branch=EscalationBranch.BRANCH_1_AUTO_APPROVE,
        reasoning_must_include=["knee", "meniscal", "conservative"],
        reasoning_must_not_include=["experimental"],
        clinical_rationale="Persistent mechanical knee symptoms after PT — ACR criteria met.",
        judge_scoring_criteria="Score highly if rationale cites mechanical symptoms + PT trial + ACR.",
    ),
    GoldenCase(
        case_id="GC-093",
        title="Shoulder MRI for chronic rotator cuff symptoms — clean approve per ACR",
        diagnosis_code="M75.100",
        diagnosis_description="Unspecified rotator cuff tear or rupture, unspecified shoulder, not specified as traumatic",
        procedure_code="73221",
        procedure_description="MRI upper extremity without contrast (shoulder)",
        clinical_notes=(
            "55-year-old female with chronic right shoulder pain × 4 months. "
            "PT × 8 weeks with subjective improvement but persistent weakness "
            "in external rotation. Positive empty-can and lift-off tests. "
            "Orthopedics requesting shoulder MRI to evaluate for full-"
            "thickness rotator cuff tear before considering surgical repair."
        ),
        guidelines_context=(
            "ACR Appropriateness Criteria for Chronic Shoulder Pain + "
            "AAOS Rotator Cuff Guidelines: MRI is indicated when surgical "
            "intervention is being considered after appropriate conservative "
            "therapy. Coverage routine."
        ),
        expected_outcome=ExpectedOutcome.AUTO_APPROVED,
        expected_branch=EscalationBranch.BRANCH_1_AUTO_APPROVE,
        reasoning_must_include=["shoulder", "rotator cuff", "conservative"],
        reasoning_must_not_include=["experimental"],
        clinical_rationale="Chronic shoulder pain with positive exam after PT trial — ACR criteria met.",
        judge_scoring_criteria="Score highly if rationale cites PT trial + positive physical exam + surgical-planning rationale.",
    ),
    GoldenCase(
        case_id="GC-094",
        title="CT pulmonary angiogram for suspected PE — auto-approve per Wells score",
        diagnosis_code="I26.99",
        diagnosis_description="Other pulmonary embolism without acute cor pulmonale",
        procedure_code="71275",
        procedure_description="CT angiography, chest (CTPA)",
        clinical_notes=(
            "47-year-old female presenting to ED with acute pleuritic chest "
            "pain + dyspnea. Recent 8-hour flight. HR 108, SpO2 93% on room "
            "air. Wells score: 6 (PE likely). D-dimer 1280 ng/mL (elevated). "
            "No contraindications to contrast. ED ordering CT pulmonary "
            "angiogram per institutional acute-PE workup pathway."
        ),
        guidelines_context=(
            "ACR Appropriateness Criteria for Suspected PE + ATS/ESC PE "
            "Guidelines: CTPA is first-line imaging for PE-likely patients "
            "(Wells > 4) with elevated D-dimer. Time-sensitive — anticoagulation "
            "may be needed pending results."
        ),
        expected_outcome=ExpectedOutcome.AUTO_APPROVED,
        expected_branch=EscalationBranch.BRANCH_1_AUTO_APPROVE,
        reasoning_must_include=["PE", "Wells", "CTPA"],
        reasoning_must_not_include=["delayed", "V/Q first"],
        clinical_rationale="High pre-test probability PE with elevated D-dimer — CTPA first-line per ACR. Urgent.",
        judge_scoring_criteria="Score highly if rationale cites Wells + D-dimer + urgent indication. Penalize HEAVILY for any delay framing.",
    ),
    # ─────────────────────────────────────────────────────────────────────────
    # Batch O — Dermatology depth (3 cases)
    # ─────────────────────────────────────────────────────────────────────────
    GoldenCase(
        case_id="GC-095",
        title="Hidradenitis suppurativa adalimumab Hurley stage II per AAD",
        diagnosis_code="L73.2",
        diagnosis_description="Hidradenitis suppurativa",
        procedure_code="J0135",
        procedure_description="Adalimumab (Humira) injection",
        clinical_notes=(
            "29-year-old female with hidradenitis suppurativa, Hurley stage II "
            "(multiple recurrent abscesses with sinus tract formation, "
            "axillary and inguinal involvement). Failed adequate trial of "
            "clindamycin + rifampin × 12 weeks and topical clindamycin. "
            "Dermatologist requesting adalimumab (FDA-approved for HS)."
        ),
        guidelines_context=(
            "AAD HS Guidelines + EADV Position Statement: adalimumab is the "
            "only FDA-approved biologic for moderate-to-severe HS (Hurley II-III). "
            "Indicated after failure of conservative + first-line antibiotic "
            "therapy."
        ),
        expected_outcome=ExpectedOutcome.AUTO_APPROVED,
        expected_branch=EscalationBranch.BRANCH_1_AUTO_APPROVE,
        reasoning_must_include=["HS", "Hurley", "adalimumab"],
        reasoning_must_not_include=["experimental", "first-line for psoriasis"],
        clinical_rationale="HS Hurley II with antibiotic failure — adalimumab is FDA-approved first-line biologic.",
        judge_scoring_criteria="Score highly if rationale cites Hurley stage + antibiotic failure + FDA approval.",
    ),
    GoldenCase(
        case_id="GC-096",
        title="Vitiligo topical ruxolitinib (newer indication) — IN_REVIEW",
        diagnosis_code="L80",
        diagnosis_description="Vitiligo",
        procedure_code="J8499",
        procedure_description="Ruxolitinib 1.5% topical cream",
        clinical_notes=(
            "31-year-old female with non-segmental vitiligo affecting face "
            "and hands, BSA approximately 6%. Failed topical corticosteroids "
            "× 4 months and topical calcineurin inhibitors × 3 months. "
            "Dermatologist requesting topical ruxolitinib (FDA-approved 2022 "
            "for non-segmental vitiligo ages ≥ 12). Patient counseled on "
            "potential systemic absorption with prolonged use."
        ),
        guidelines_context=(
            "FDA-approved label (ruxolitinib topical, Opzelura) + AAD: "
            "ruxolitinib topical is the first FDA-approved repigmentation "
            "therapy for non-segmental vitiligo. Newer indication; many "
            "payer policies require dermatology specialist review for "
            "initial authorization given novelty + cost."
        ),
        expected_outcome=ExpectedOutcome.IN_REVIEW,
        expected_branch=EscalationBranch.BRANCH_2_MEDICAL_DIRECTOR,
        reasoning_must_include=["vitiligo", "ruxolitinib", "specialist"],
        reasoning_must_not_include=["experimental", "auto-approve"],
        clinical_rationale="Newer FDA-approved indication — appropriate for specialist review on first authorization.",
        judge_scoring_criteria="Score highly if rationale recognizes the newer indication + specialist-review gate. Not denial; ruxolitinib is FDA-approved.",
    ),
    GoldenCase(
        case_id="GC-097",
        title="Severe nodular acne isotretinoin — clean approve per iPLEDGE + AAD",
        diagnosis_code="L70.0",
        diagnosis_description="Acne vulgaris",
        procedure_code="J8499",
        procedure_description="Isotretinoin 40 mg oral",
        clinical_notes=(
            "19-year-old male with severe nodulocystic acne, scarring. "
            "Failed adequate trial of oral doxycycline + topical retinoid + "
            "benzoyl peroxide × 6 months. Dermatologist requesting "
            "isotretinoin course. iPLEDGE enrollment confirmed (no "
            "reproductive capacity in male patient — single negative "
            "pregnancy test not required, but iPLEDGE registration is)."
        ),
        guidelines_context=(
            "AAD Acne Guidelines + FDA iPLEDGE REMS: isotretinoin is "
            "indicated for severe nodulocystic acne or scarring acne "
            "unresponsive to conventional therapy. REMS enrollment is "
            "required for all patients regardless of sex. Coverage routine "
            "when iPLEDGE is verified."
        ),
        expected_outcome=ExpectedOutcome.AUTO_APPROVED,
        expected_branch=EscalationBranch.BRANCH_1_AUTO_APPROVE,
        reasoning_must_include=["isotretinoin", "iPLEDGE", "scarring"],
        reasoning_must_not_include=["experimental"],
        clinical_rationale="Severe scarring acne with conventional-therapy failure + iPLEDGE registered — AAD-endorsed.",
        judge_scoring_criteria="Score highly if rationale cites severity + iPLEDGE + AAD criteria.",
    ),
    # ─────────────────────────────────────────────────────────────────────────
    # Batch P — GI depth (3 cases)
    # ─────────────────────────────────────────────────────────────────────────
    GoldenCase(
        case_id="GC-098",
        title="Moderate UC vedolizumab after 5-ASA + steroid failure per ECCO",
        diagnosis_code="K51.90",
        diagnosis_description="Ulcerative colitis, unspecified, without complications",
        procedure_code="J3380",
        procedure_description="Vedolizumab (Entyvio) injection",
        clinical_notes=(
            "37-year-old female with moderate ulcerative colitis. Failed "
            "adequate trial of oral mesalamine 4.8 g/day × 8 weeks and "
            "subsequent prednisone 40 mg taper (initial response, relapse "
            "during taper). Mayo score 8 (moderate). Anti-TNF-naive. "
            "Gastroenterology recommending vedolizumab as first biologic."
        ),
        guidelines_context=(
            "ECCO + AGA UC Guidelines: vedolizumab is appropriate first-"
            "line biologic for moderate UC after 5-ASA + steroid failure. "
            "Gut-selective mechanism preferred for some patients."
        ),
        expected_outcome=ExpectedOutcome.AUTO_APPROVED,
        expected_branch=EscalationBranch.BRANCH_1_AUTO_APPROVE,
        reasoning_must_include=["UC", "vedolizumab", "ECCO"],
        reasoning_must_not_include=["anti-TNF required first"],
        clinical_rationale="Moderate UC with conventional-therapy failure — vedolizumab is ECCO-endorsed first biologic.",
        judge_scoring_criteria="Score highly if rationale cites Mayo + step-therapy + ECCO. Penalize for demanding anti-TNF first (no such hierarchy).",
    ),
    GoldenCase(
        case_id="GC-099",
        title="Eosinophilic esophagitis dupilumab — IN_REVIEW (newer indication)",
        diagnosis_code="K20.0",
        diagnosis_description="Eosinophilic esophagitis",
        procedure_code="J0517",
        procedure_description="Dupilumab (Dupixent) injection",
        clinical_notes=(
            "28-year-old female with biopsy-confirmed eosinophilic "
            "esophagitis (eos peak 45/HPF on endoscopy). Failed proton-pump "
            "inhibitor trial × 8 weeks (eos persisted on repeat biopsy). "
            "Failed swallowed topical fluticasone × 12 weeks (inadequate "
            "histologic response). Symptoms (dysphagia, food impactions) "
            "persist. GI requesting dupilumab (FDA-approved 2022 for EoE)."
        ),
        guidelines_context=(
            "AGA EoE Guidelines + FDA-approved label for dupilumab in EoE: "
            "dupilumab is FDA-approved for EoE in adults and adolescents "
            "≥ 12 years not adequately controlled with PPI + topical steroids. "
            "Newer indication; payer policies typically require GI specialist "
            "review for initial authorization."
        ),
        expected_outcome=ExpectedOutcome.IN_REVIEW,
        expected_branch=EscalationBranch.BRANCH_2_MEDICAL_DIRECTOR,
        reasoning_must_include=["EoE", "dupilumab", "newer"],
        reasoning_must_not_include=["experimental", "auto-approve"],
        clinical_rationale="EoE meets dupilumab criteria; specialist review appropriate for newer indication.",
        judge_scoring_criteria="Score highly if rationale recognizes the newer FDA indication + appropriate specialist gate.",
    ),
    GoldenCase(
        case_id="GC-100",
        title="Achalasia Heller myotomy — clean approve per ACG (cap case at GC-100)",
        diagnosis_code="K22.0",
        diagnosis_description="Achalasia of cardia",
        procedure_code="43279",
        procedure_description="Laparoscopic esophagomyotomy (Heller-type)",
        clinical_notes=(
            "43-year-old male with type II achalasia confirmed on high-"
            "resolution esophageal manometry. Eckardt score 8 (severe "
            "symptoms). Barium esophagram shows characteristic bird's-beak. "
            "Failed initial pneumatic dilation × 2. GI / surgery "
            "recommending laparoscopic Heller myotomy with Dor "
            "fundoplication."
        ),
        guidelines_context=(
            "ACG Clinical Guidelines for Achalasia + ISDE Position: "
            "laparoscopic Heller myotomy (with partial fundoplication for "
            "reflux prevention) is appropriate definitive therapy for type "
            "I-II achalasia. Pneumatic dilation failure supports surgical "
            "approach."
        ),
        expected_outcome=ExpectedOutcome.AUTO_APPROVED,
        expected_branch=EscalationBranch.BRANCH_1_AUTO_APPROVE,
        reasoning_must_include=["achalasia", "Heller", "ACG"],
        reasoning_must_not_include=["experimental"],
        clinical_rationale=(
            "Type II achalasia with pneumatic dilation failure — Heller "
            "myotomy is ACG-endorsed definitive therapy. Clean approve. "
            "Cap case at GC-100 — closes the iter-6 dataset-growth arc."
        ),
        judge_scoring_criteria="Score highly if rationale cites manometry type + dilation failure + ACG.",
    ),
]
