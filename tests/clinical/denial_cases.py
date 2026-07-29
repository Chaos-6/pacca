"""
Denial-class cases — iter-6 Batch A (DENY expansion to 5).

WHY THESE EXIST
---------------
At iter-5 close, the dataset had ZERO DENY-class cases. Iter-6 expansion_cases
added GC-026 (proton-beam for low-risk prostate per NCCN/ASTRO) and GC-027
(cath without non-invasive workup per ACC/AHA). This file adds 3 more to
reach 5 — the minimum sample size for a defensible "we test denials" claim
per DATASET_SUFFICIENCY.md § "Recommended priority order".

The 3 cases probe the 3 most common production-deny categories beyond
patient-preference (GC-026) and workup-hierarchy (GC-027):

  GC-034  Off-label use without NCCN compendia support      DENIED
  GC-035  Frequency-cap violation (PT visits over benefit)  DENIED
  GC-036  Re-request after prior denial without new evidence DENIED

Per CASE_AUTHORING_GUIDE.md § 9 — at ≥ 5 cases, a category warrants its own
file. DENY now has its own file.
"""

from __future__ import annotations

from tests.clinical.golden_cases import (
    EscalationBranch,
    ExpectedOutcome,
    GoldenCase,
)

DENIAL_CASES: list[GoldenCase] = [
    # ─────────────────────────────────────────────────────────────────────────
    # GC-034 — Off-label oncology biologic without NCCN compendia support.
    # ─────────────────────────────────────────────────────────────────────────
    GoldenCase(
        case_id="GC-034",
        title="Off-label nivolumab for pancreatic adenocarcinoma — no compendia support",
        diagnosis_code="C25.0",
        diagnosis_description="Malignant neoplasm of head of pancreas",
        procedure_code="J9299",
        procedure_description="Nivolumab (Opdivo) injection",
        clinical_notes=(
            "61-year-old male with metastatic pancreatic adenocarcinoma, "
            "progression on first-line FOLFIRINOX and second-line "
            "nab-paclitaxel + gemcitabine. PD-L1 not tested. MSI-stable. "
            "Tumor mutational burden not measured. Oncologist requesting "
            "nivolumab off-label, citing general immune checkpoint inhibitor "
            "activity in solid tumors. No published Phase III data for "
            "pancreatic ductal adenocarcinoma. Patient is not enrolled in a "
            "clinical trial. ECOG 1."
        ),
        guidelines_context=(
            "NCCN Pancreatic Adenocarcinoma Guidelines (current version): "
            "nivolumab monotherapy is not a Category 1, 2A, or 2B recommendation "
            "for metastatic pancreatic adenocarcinoma in MSI-stable disease "
            "with normal TMB. NCCN Drugs & Biologics Compendium does not list "
            "this indication. CMS Medicare NCD recognizes NCCN compendia for "
            "off-label oncology coverage; absence of compendia listing means "
            "off-label use is not covered. Clinical-trial enrollment is the "
            "appropriate path for this patient."
        ),
        expected_outcome=ExpectedOutcome.DENIED,
        expected_branch=EscalationBranch.NONE,
        reasoning_must_include=["off-label", "compendia", "NCCN"],
        # chg-25 review (F2): "appropriately"/"appropriateness" backfilled --
        # whole-word matching no longer catches them implicitly from
        # "appropriate".
        reasoning_must_not_include=["approved", "appropriate", "appropriately", "appropriateness"],
        clinical_rationale=(
            "Off-label oncology immunotherapy in a tumor type without NCCN "
            "compendia support and without MSI-high / TMB-high status that "
            "would qualify under tissue-agnostic indications. CMS NCD + "
            "commercial policy align: no compendia, no coverage. The clinical "
            "redirect is to a clinical trial, not authorization."
        ),
        judge_scoring_criteria=(
            "Score highly if rationale identifies the off-label status, the "
            "absence of compendia support, and recommends trial enrollment "
            "as the redirect. Penalize for auto-approval or IN_REVIEW — the "
            "guideline position is unambiguous."
        ),
    ),
    # ─────────────────────────────────────────────────────────────────────────
    # GC-035 — Physical therapy visits exceeding annual benefit cap.
    # ─────────────────────────────────────────────────────────────────────────
    GoldenCase(
        case_id="GC-035",
        title="PT visits — 50 already used of 30-visit benefit cap; request denied",
        diagnosis_code="M54.5",
        diagnosis_description="Low back pain",
        procedure_code="97110",
        procedure_description="Therapeutic exercise, each 15 min",
        clinical_notes=(
            "52-year-old female with chronic low back pain, currently enrolled "
            "in outpatient physical therapy for the calendar year. Has "
            "completed 50 visits year-to-date (calendar plan year). Provider "
            "requesting approval for 20 additional visits citing ongoing pain. "
            "Annual benefit per plan documentation: 30 outpatient PT visits "
            "per calendar year. No documented appeal for medical-necessity "
            "exception; no documentation of functional gains or progress "
            "toward goals warranting exception consideration. Maintenance "
            "phase, not acute episode."
        ),
        guidelines_context=(
            "Standard commercial PT benefit: 30 outpatient PT visits per "
            "calendar year. Benefit exhaustion is a contractual matter, "
            "separate from medical necessity. Exceptions require: (a) "
            "documented acute new injury, (b) post-surgical episode within "
            "90 days, (c) demonstrable functional progress with measurable "
            "goal achievement. APTA Clinical Practice Guidelines note that "
            "maintenance PT in chronic LBP without acute exacerbation does "
            "not meet medical-necessity exception criteria."
        ),
        expected_outcome=ExpectedOutcome.DENIED,
        expected_branch=EscalationBranch.NONE,
        reasoning_must_include=["benefit", "exceeded", "calendar"],
        reasoning_must_not_include=["approved", "medical necessity met"],
        clinical_rationale=(
            "Benefit cap exhausted (50 of 30 used; patient is over limit). "
            "No documented exception criteria (acute injury, post-surgical, "
            "functional progress goals). Denial is correct; appeal pathway "
            "exists if the provider documents exception criteria, but absent "
            "that documentation, the contractual benefit cap controls."
        ),
        judge_scoring_criteria=(
            "Score highly if rationale identifies the benefit-cap exhaustion "
            "and the absence of documented exception criteria. Penalize for "
            "framing this as a medical-necessity denial (it's a benefit-design "
            "denial), or for auto-approval."
        ),
    ),
    # ─────────────────────────────────────────────────────────────────────────
    # GC-036 — Re-request after prior denial without new evidence.
    # ─────────────────────────────────────────────────────────────────────────
    GoldenCase(
        case_id="GC-036",
        title="Re-request for adalimumab after prior denial — no new clinical evidence",
        diagnosis_code="L40.0",
        diagnosis_description="Psoriasis vulgaris",
        procedure_code="J0135",
        procedure_description="Adalimumab (Humira) injection",
        clinical_notes=(
            "44-year-old male with moderate plaque psoriasis. Request submitted "
            "60 days after prior denial for the same medication on the same "
            "diagnosis. Prior denial reason: inadequate step therapy (topical "
            "trial duration < 12 weeks; only one systemic agent attempted). "
            "Current submission: identical documentation, no new step-therapy "
            "completion documented, no new severity assessment, no documented "
            "treatment failure since prior denial."
        ),
        guidelines_context=(
            "Per plan policy + AAD step-therapy guidelines: re-requests "
            "after denial require either (a) new clinical evidence (worsened "
            "severity, new failure of an additional step), (b) completion "
            "of previously-incomplete step therapy, or (c) a formal appeal "
            "with peer-to-peer review. Re-request with identical documentation "
            "is denied per the doctrine of finality on the prior decision. "
            "The appeal pathway remains available."
        ),
        expected_outcome=ExpectedOutcome.DENIED,
        expected_branch=EscalationBranch.NONE,
        reasoning_must_include=["prior denial", "no new", "evidence"],
        reasoning_must_not_include=["approved", "reconsidered"],
        prior_denial_codes=["J0135"],
        clinical_rationale=(
            "Re-request with identical documentation 60 days after denial. "
            "Branch_7 (prior_denial) pre-flight would fire, but the request "
            "lacks the new-evidence trigger that would justify revisiting. "
            "Denial is correct; the formal appeal pathway is the appropriate "
            "next step for the provider."
        ),
        judge_scoring_criteria=(
            "Score highly if rationale identifies the prior denial, the "
            "absence of new clinical evidence, and notes the appeal pathway. "
            "Penalize for auto-approval, for IN_REVIEW (no new info to "
            "review), or for treating this as a fresh first-time request."
        ),
    ),
]
