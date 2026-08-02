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
        # chg-33: was DENIED / NONE. David's call, 2026-08-01: this case should
        # pre-flight escalate. Off-label oncology immunotherapy in a patient who
        # has progressed through two lines, with no compendia support, is a
        # human-review case on the merits regardless of how clear the coverage
        # position reads.
        expected_outcome=ExpectedOutcome.PRE_FLIGHT_ESCALATE,
        expected_branch=EscalationBranch.BRANCH_4_EXPERIMENTAL,
        # Constraints target the machine-generated pre-flight rationale
        # ("Pre-flight escalation triggered. Reasons: ['experimental_treatment']
        # ... Requires human review."), not agent prose. The previous list
        # ("off-label", "compendia", "NCCN") asserted words the agent never gets
        # the chance to write, since it is never called.
        reasoning_must_include=["experimental", "human review"],
        # UNCHANGED, deliberately. An earlier draft of this change replaced these
        # on the theory that they conflicted with the new expected outcome. They
        # do not: the pre-flight rationale contains none of them, and the list
        # carries chg-25's F2 morphological backfill ("appropriately",
        # "appropriateness"), which test_gc_034_and_gc_105_appropriately_variant_
        # now_caught pins. Replacing them would have silently dropped that
        # coverage to fix a conflict that did not exist.
        reasoning_must_not_include=["approved", "appropriate", "appropriately", "appropriateness"],
        clinical_rationale=(
            "Off-label oncology immunotherapy with no NCCN compendia support "
            "and without MSI-high / TMB-high status that would qualify under "
            "tissue-agnostic indications. Escalates to human review rather than "
            "being adjudicated automatically.\n\n"
            "CAVEAT, recorded because it is load-bearing: the OUTCOME here is "
            "right but the MECHANISM currently producing it is not. Branch 4 "
            "fires on this case through three defects, all verified 2026-07-31: "
            "substring matching turns one 'No published Phase III data' into "
            "'phase i' + 'phase ii' + 'phase iii' hits; there is no negation "
            "awareness, so 'Patient is not enrolled in a clinical trial' matches "
            "'clinical trial'; and 'off-label' sits in the experimental keyword "
            "list although off-label use of an FDA-approved drug is a coverage "
            "question, not an experimental one. Every trigger is a false "
            "positive that happens to reach the right answer.\n\n"
            "So this expectation is COUPLED to those defects. Fixing branch 4 "
            "may stop this case escalating, at which point the right branch for "
            "it must be decided rather than assumed. The branch-4 coupling "
            "test in tests/unit/test_escalation_tree.py exists to make that "
            "moment loud. (Named in prose rather than as an identifier on "
            "purpose: this field feeds the keyword-sweep corpus, and an "
            "underscored name registers as a substring hit without a "
            "whole-word one -- manufacturing exactly the false-positive "
            "exposure that sweep measures.)"
        ),
        judge_scoring_criteria=(
            "Score highly if the system routes to human review citing the "
            "experimental/investigational flag. As in GC-006 and GC-009, the "
            "coverage merits are irrelevant here -- the pre-flight fires first "
            "and a human weighs it. Penalize for autonomous approval or denial "
            "without human review."
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
        # chg-31: was DENIED / NONE. The case supplies prior_denial_codes=["J0135"]
        # against procedure_code="J0135" -- the exact input that fires the Branch 7
        # pre-flight check -- while declaring that no escalation is expected. A case
        # cannot opt out of a pre-flight safety boundary by asserting a different
        # outcome; CLAUDE.md is explicit that the escalation tree OVERRIDES model
        # confidence and is not a suggestion. GC-008 is the same scenario (same
        # service resubmitted, no change in clinical status) and correctly expects
        # PRE_FLIGHT_ESCALATE "regardless of clinical merit". Corrected to match.
        expected_outcome=ExpectedOutcome.PRE_FLIGHT_ESCALATE,
        expected_branch=EscalationBranch.BRANCH_7_PRIOR_DENIAL,
        # Constraints now target the pre-flight rationale, which is machine-generated
        # ("Patient has a prior denial record for procedure J0135..."), not agent
        # prose -- so they mirror GC-008's rather than asserting reasoning the agent
        # never gets the chance to write.
        reasoning_must_include=["prior denial", "J0135"],
        reasoning_must_not_include=["auto-approved"],
        prior_denial_codes=["J0135"],
        clinical_rationale=(
            "Re-request with identical documentation 60 days after denial. "
            "Branch 7 (prior_denial) fires on the matching procedure code and "
            "routes to human review -- the reviewer's job is exactly the question "
            "this case poses: did anything change since the prior decision? "
            "The case previously expected DENIED on the doctrine of finality. "
            "That is defensible payer practice but it is not what Branch 7 does, "
            "and auto-denying a resubmission is precisely where a parser miss "
            "compounds: a provider who resubmits may have added something the "
            "extraction dropped. If PACCA should auto-deny repeat claims, that is "
            "a deliberate widening of the automation surface and a change to the "
            "escalation tree, not an expectation encoded in a case."
        ),
        judge_scoring_criteria=(
            "Score highly if the system routes to human review citing the prior "
            "denial on the same procedure code. As in GC-008, clinical merit is "
            "irrelevant here -- the prior-denial pre-flight fires first and the "
            "reviewer determines whether circumstances changed. Penalize for "
            "auto-approval, and for treating this as a fresh first-time request."
        ),
    ),
]
