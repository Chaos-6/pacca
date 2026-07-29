# Case Authoring Guide — How to Add a Golden Case to PACCA

> **Companion to:** [`DATASET_SUFFICIENCY.md`](./DATASET_SUFFICIENCY.md), [`CASE_PROVENANCE.md`](./CASE_PROVENANCE.md), [`EVALUATION_COVERAGE.md`](./EVALUATION_COVERAGE.md).
> **Audience:** clinical SMEs, harness engineers, and the maintainer growing the dataset from 25 → 100 → 300 → 500 cases.
> **Last updated:** 2026-05-25 at iter-5 close (25-case state).

This guide describes the template, the workflow, and the review gates for adding a new clinical evaluation case to PACCA. A case that follows this guide will:

- compile cleanly against the existing `GoldenCase` schema,
- carry the provenance metadata required by [`CASE_PROVENANCE.md`](./CASE_PROVENANCE.md),
- be defensible to a HIPAA / SaMD reviewer asking "why does this case exist?",
- contain zero PHI and cite an authoritative clinical guideline,
- run through the regression-gate and judge-scoring pipeline without special handling.

## 1. Before you write — decide WHY this case exists

A new case must answer one of two questions:

| Question | What kind of case satisfies it |
|---|---|
| **Coverage gap** — "we have no case that probes [X]" | A clean positive or negative case for a gate, branch, demographic, or specialty that is currently under-represented. See `EVALUATION_COVERAGE.md` for the current gap map. |
| **Adversarial probe** — "we want to verify the agent will not [Y]" | A failure-mode case (hallucination trap, memory trap, step-therapy bypass, anti-pattern routing). See the failure-mode taxonomy in `CASE_PROVENANCE.md`. |

If you cannot state in one sentence which gap or failure mode this case probes, **do not add it.** Speculative cases dilute the suite without buying detection.

The recommended priority order — which gap to close next — is in [`DATASET_SUFFICIENCY.md` § "Recommended priority order"](./DATASET_SUFFICIENCY.md).

## 2. The schema

A case is an instance of the `GoldenCase` dataclass defined in [`tests/clinical/golden_cases.py`](../tests/clinical/golden_cases.py). All required fields:

| Field | Type | Purpose |
|---|---|---|
| `case_id` | `str` | `GC-NNN`. Monotonically increasing across the entire dataset (GOLDEN + NEAR_MISS + PEDIATRIC + future siblings). |
| `title` | `str` | One human-readable line for test output (≤ 100 chars). |
| `diagnosis_code` | `str` | ICD-10. |
| `diagnosis_description` | `str` | Plain text matching the ICD-10. |
| `procedure_code` | `str` | CPT, HCPCS, or J-code. |
| `procedure_description` | `str` | Brand name in parens after generic. |
| `clinical_notes` | `str` | The synthetic provider notes the agent will read. 3–8 sentences. PHI-free. See § 4. |
| `guidelines_context` | `str` | The synthetic RAG payload — what guidelines retrieval would return for this diagnosis × procedure. 2–5 sentences. Cite real guideline body. |
| `expected_outcome` | `ExpectedOutcome` | `AUTO_APPROVED`, `IN_REVIEW`, `DENIED`, or `PRE_FLIGHT_ESCALATE`. |
| `expected_branch` | `EscalationBranch` | Which SS5.4 branch should fire (1–7 or NONE). |
| `reasoning_must_include` | `list[str]` | Phrases that MUST appear in the agent's rationale as a WHOLE WORD (chg-25: exact, case-insensitive, word-boundary match — not substring, and not judged). 1–4 phrases, lowercase. See § 6. |
| `reasoning_must_not_include` | `list[str]` | Keyword constraints checked the same way. The most powerful test surface — include words the agent must not invent (e.g., a lab value never mentioned in the notes). See § 6 for the word-boundary rule. |
| `prior_denial_codes` | `list[str]` | Empty unless this is a branch_7 case. |
| `clinical_rationale` | `str` | 2–5 sentence human-expert justification for the expected outcome. This is what an SME would write. |
| `judge_scoring_criteria` | `str` | What the LLM-as-judge should explicitly evaluate. Anchors the rubric. |

## 3. The template

Copy this block and edit. Comments mark what to change.

```python
GoldenCase(
    case_id="GC-NNN",  # Next monotonically-increasing ID. See git for the latest.
    title="<specialty> <condition> — <one-line clinical hook>",
    diagnosis_code="<ICD-10>",
    diagnosis_description="<plain text matching the ICD-10>",
    procedure_code="<CPT/HCPCS/J-code>",
    procedure_description="<Generic name (Brand name)>",
    clinical_notes=(
        # 3–8 sentences. Include:
        #   - age, sex, relevant demographics
        #   - severity / staging language
        #   - prior therapies (with duration, response)
        #   - relevant labs / imaging (only if the case is testing
        #     that the agent uses them — don't add unused data)
        #   - the requesting provider's stated rationale
        # PHI-free. Never invent: SSN, MRN, DOB, full name, address.
        "<3–8 sentences of synthetic provider notes>"
    ),
    guidelines_context=(
        # 2–5 sentences. What the RAG layer would return.
        # MUST cite a real guideline body (NCCN, ACR, AAD, GINA, ECCO, etc.)
        # Do not invent guideline content. Paraphrase real guidance.
        "<2–5 sentences of synthetic RAG payload>"
    ),
    expected_outcome=ExpectedOutcome.<...>,
    expected_branch=EscalationBranch.<...>,
    reasoning_must_include=["<phrase 1>", "<phrase 2>"],
    reasoning_must_not_include=["<hallucination marker>"],
    clinical_rationale=(
        "<2–5 sentence human-expert justification>"
    ),
    judge_scoring_criteria=(
        "<what the LLM-as-judge should evaluate>"
    ),
),
```

## 4. The PHI rule (non-negotiable)

`clinical_notes` is the field most likely to leak protected information. Follow these rules strictly:

| Allowed | Forbidden |
|---|---|
| `"58-year-old male"` | `"John Smith, DOB 5/4/1967"` |
| `"PD-L1 expression 62%"` | `"PD-L1 results from LabCorp report dated 3/12/2024"` |
| `"NCCN Category 1"` | `"per Dr. Jane Doe's note 4/15/2024"` |
| `"BMI 28"` | `"weight 187 lb, height 5'10''"` (re-identification risk in small populations) |
| `"history of T2DM and HTN"` | `"patient lives at 123 Main St, retired teacher"` |

The PACCA repo's CLAUDE.md is explicit: **"Never put real PHI in fixtures, seeds, or committed files. Synthetic data only."** This applies to authored cases.

If a case is derived from a real patient encounter (e.g., an SME drafting from memory of a clinical experience), the author MUST:

1. Change age by ±3 years and sex if doing so does not invalidate the clinical signal.
2. Re-randomize labs to within ±15% of the original.
3. Remove all dates, locations, and proper nouns except the institution-class ("a pediatric gastroenterologist", not "the pediatric gastroenterologist at Boston Children's").
4. State in the PR description: "derived from clinical experience; PHI removed per CASE_AUTHORING_GUIDE.md § 4."

## 5. The guideline-citation rule

`guidelines_context` MUST cite a real authoritative body. The recognized bodies for PACCA's specialty mix:

| Specialty | Recognized guideline bodies |
|---|---|
| Oncology | NCCN, ASCO, ESMO, CMS NCD |
| Rheumatology | ACR, EULAR |
| Gastroenterology | ACG, AGA, ECCO, ESPGHAN (pediatric) |
| Dermatology | AAD, AAD-NPF (psoriasis), EADV |
| Pulmonology | ATS, ERS, GINA (asthma), GOLD (COPD) |
| Cardiology | ACC/AHA, ESC, HRS (electrophysiology) |
| Endocrinology | ADA, AACE, ATA (thyroid) |
| Neurology | AAN, AHA/ASA (stroke), MS Society |
| Surgery / Orthopedics | AAOS, NASS (spine), ACS |
| Imaging | ACR Appropriateness Criteria, AUC |
| Pediatrics | AAP, plus specialty-specific (e.g., ESPGHAN for peds GI) |

If your case touches a specialty not listed, add the recognized body to this table in the same PR.

**Do not invent guideline content.** If you are not certain what NCCN actually says about (e.g.) third-line therapy for relapsed multiple myeloma, find the published guideline and paraphrase the relevant section — do not write what you assume it says. The judge will score against the cited body; a hallucinated citation propagates into evaluation correctness.

## 6. Choosing `reasoning_must_not_include`

This is the most powerful field in the schema, and the most under-used. It exists to catch a specific, narrow failure mode: **the agent's rationale using a word or phrase you have declared it should never need**, most often because the case was designed to make that word tempting (e.g. testing that the agent doesn't over-escalate on age alone) or because using it would reveal an invented detail.

**The rule:** include phrases that the agent would only produce if it invented data, or that the case specifically exists to probe (e.g. "does the agent inappropriately reason about X").

**How the match works (chg-25, corrected post-review).** `reasoning_must_include` / `reasoning_must_not_include` are checked deterministically in Python — `tests/clinical/evaluator.py::check_reasoning_constraints()` — never by the LLM judge. Matching is **case-insensitive, WHOLE-WORD** matching, not substring containment: a keyword matches only when it appears as its own token, not glued inside a longer word. Concretely:

- Forbidding `"age"` does **not** trigger on "coverage", "dosage", "triage", "stage", "average", "manage", or "antineoplastic **agent**" — all of which are ordinary prior-authorization / oncology vocabulary that would have produced constant false positives under naive substring matching. This was a real bug, found and fixed at chg-25 review; see `docs/EVALUATION.md`.
- Forbidding `"age"` **does** still trigger on "the patient's **age** (82 years)..." or "**Age** alone is not an exclusion" — real, standalone uses of the word.
- **Morphological variants are NOT matched implicitly.** A forbidden `"age"` will NOT catch "ag**ed**" or "ag**es**" — if you mean to forbid those too, list them explicitly: `reasoning_must_not_include = ["age", "aged", "ages"]`. Predictable beats clever: a reader of the keyword list should be able to tell exactly what it catches without guessing at stemming rules.
- Multi-word phrases work as expected (the internal space is already a boundary): `"complete documentation"` matches only that exact phrase, not e.g. "an incomplete documentation set."

Examples from the existing dataset:

- GC-018 (sparse-notes case): `reasoning_must_not_include = ["HbA1c", "creatinine"]` — these labs were not in the notes; if the agent's rationale mentions them, it invented them.
- GC-019 (sparse-notes case): `reasoning_must_not_include = ["prior therapy with"]` — no prior therapy was documented; if the agent invents one, this fires.
- GC-021 (memory-trap case): `reasoning_must_not_include = ["NSCLC + pembrolizumab is always approved"]` — H2 memory compression failure mode.
- GC-028 (geriatric ICD, cardiology): `reasoning_must_not_include = ["age", "elderly"]` — probes whether the agent inappropriately escalates on age alone; a rationale that discusses the patient's real age (to say it's *not* exclusionary) still trips this, by design — that is a keyword-constraint observation, not a fabrication (see `docs/EVALUATION.md`'s three-tier separation).

When authoring, ask: **"what is the most plausible thing the agent would say that would either mean it invented data, or reveal the specific reasoning error this case exists to probe?"** Add that phrase here — and pick keywords specific enough that ordinary vocabulary in the case's own specialty won't collide with them as whole words (short, common words like "age," "stage," or "deny" are the highest-risk choices; check what your case's own `clinical_notes` / `guidelines_context` naturally contain before picking one).

If you cannot think of one, the case may be a routine coverage case (which is fine) — leave `reasoning_must_not_include = []`.

## 7. Choosing `expected_outcome` and `expected_branch`

The branch matters because the per-case regression gate compares both outcome and branch. A case that flips from `BRANCH_1_AUTO_APPROVE` to `BRANCH_2_MEDICAL_DIRECTOR` is a regression even if both produce `AUTO_APPROVED` in the end.

Decision tree:

```
Is the case experimental (CAR-T, phase-N trial, off-label)?
  → expected_branch = BRANCH_4_EXPERIMENTAL

Is the diagnosis ICD-10 in the rare-disease prefix list?
  → expected_branch = BRANCH_5_RARE

Does the case re-request after a prior denial?
  → expected_branch = BRANCH_7_PRIOR_DENIAL

Do the cited guidelines conflict (e.g., NCCN says yes, CMS NCD says no)?
  → expected_branch = BRANCH_6_CONFLICTING

Is the documentation incomplete or evidence borderline?
  → expected_outcome = IN_REVIEW
  → expected_branch = BRANCH_3_LOW_CONFIDENCE

Does the case trigger a policy escalation (high-cost > $100K, pediatric+complex,
  oncology+experimental adjunct)?
  → expected_outcome = IN_REVIEW
  → expected_branch = BRANCH_2_MEDICAL_DIRECTOR

Otherwise — clean documentation, all criteria met, no escalation triggers:
  → expected_outcome = AUTO_APPROVED
  → expected_branch = BRANCH_1_AUTO_APPROVE

Documentation present but criteria explicitly unmet (e.g., guideline requires X,
  case has not-X):
  → expected_outcome = DENIED
  → expected_branch = NONE  (denial is not an escalation)
```

## 8. Naming and ID-allocation discipline

`case_id` is `GC-NNN`. Rules:

- IDs are monotonic across **all** case files. The highest ID in the dataset is the next-1; you start at next.
- IDs are never reused. If a case is removed, its ID is retired (state this in the commit message).
- Adding a case across multiple files in one PR: allocate IDs sequentially in the order they appear in the PR diff.

To find the highest ID, run:

```bash
grep -rh 'case_id="GC-' tests/clinical/ | sed 's/.*GC-\([0-9]*\)".*/\1/' | sort -n | tail -1
```

## 9. Where the case goes — file selection

| Case kind | File | Why |
|---|---|---|
| Clean adult positive/negative or branch-N positive | `tests/clinical/golden_cases.py` (in `GOLDEN_CASES`) | Default. The 20-case canonical suite. |
| Adversarial near-miss (one-disqualifier-off-from-a-known-approve) | `tests/clinical/near_miss_cases.py` (in `NEAR_MISS_CASES`) | Iter-2 chg-3 precedent: kept separate so `GOLDEN_CASES` length is stable and the gate has a named adversarial suite. |
| Pediatric case feeding the complexity-score discriminator | `tests/clinical/pediatric_cases.py` (in `PEDIATRIC_CASES`) | Iter-5 chg-2 precedent: a named contrastive set with a specific model-validation purpose. |
| A new category that doesn't fit the above (e.g., 8+ DENY-class cases warranting a `denial_cases.py`) | New file, new list, mirror the existing pattern | Create the sibling file when the new category has ≥ 5 cases and a coherent purpose. |

If you are adding < 5 cases of a new "category," put them in `GOLDEN_CASES`. Create a new file only when the category earns its own collective name.

## 10. Wire-up checklist (mechanical)

When a new case lands, the following sites must be updated:

| Site | What to add |
|---|---|
| `tests/clinical/<file>.py` | The new `GoldenCase(...)` entry. |
| `tests/clinical/test_clinical_accuracy.py` | If you added a new case file, register it in the test fixture aggregating all cases. |
| `tests/clinical/baselines/<latest>.json` | After first capture run, the baseline scoreboard will include the new case. Commit the baseline update with the case. |
| `docs/CASE_PROVENANCE.md` | Add one row per the schema in that file. **Required.** |
| `docs/EVALUATION_COVERAGE.md` | Update the relevant dimension matrices. **Required.** |
| `docs/DATASET_SUFFICIENCY.md` | Update the case count in § "Where we are" if the new total crosses a threshold (50, 100, 300, 500). |
| Iteration manifest (`docs/iterations/iter-N.json`) | If the case lands in the current iteration, add it under the change's `risk_cases` or as a `chg-N` of its own. |
| `docs/HARNESS.md` § evaluation section | Update the case-count reference if it cites a specific number. |

## 11. The SME review gate (Phase 1 — pre-merge, in-house)

Before merging a case, the author must obtain SME concurrence on:

1. **Clinical accuracy of `clinical_notes`** — would a board-certified specialist in the relevant field find this scenario plausible?
2. **Guideline citation in `guidelines_context`** — does the cited body actually say what the field paraphrases?
3. **Correctness of `expected_outcome` and `expected_branch`** — given the notes + guideline, would the specialist agree this is the right disposition?
4. **Appropriateness of `clinical_rationale`** — is the human-expert justification one a specialist would write?

The SME records concurrence in the PR via a "clinical-review: approved" comment with their name and credential (e.g., `clinical-review: approved — Dr. J. Doe, MD, board-certified rheumatology`).

If the author is the SME (the maintainer who is themselves a clinician), they state this in the PR description and self-attest.

In the absence of a credentialed SME for a specialty, the case lands with a `provisional` tag in `CASE_PROVENANCE.md` and is reviewed in the next clinical-review board sweep (per § 12).

## 12. The clinical-review board gate (Phase 2 — two-stage: formation at 100, scored sweeps at 200)

The Phase 2 board activates in two stages (see `PACCA_PRD_v2.5_Consolidated.md` § 16.7). **Formation** begins when the dataset crosses **100 cases**: recruit and charter the panel and agree the sampling protocol and κ target. **Operational scored sweeps** begin at **200 cases**. As of the 105-case state, the board is *in formation, not operational* — no scored sweep has run.

Once scored sweeps begin, each quarterly clinical-review board sweep runs as:

- A panel of 2–3 credentialed clinicians (covering the major specialties) scores a random 10% stratified sample.
- Inter-rater agreement reported as Cohen's κ.
- Cases where the panel disagrees with the cataloged `expected_outcome` are flagged for revision in the next iteration.
- The board's reports land in `docs/findings/clinical-review-board-<date>.md`.

This is the on-ramp to the FDA SaMD clinical-validation claim (per [`STATISTICAL_POWER.md`](./STATISTICAL_POWER.md) § "FDA SaMD-grade claim alignment").

## 13. After authoring — the verification workflow

1. **Run the test suite locally:** `make test` — confirm the new case parses and the test fixture aggregates it correctly.
2. **Capture a baseline:** `python -m tests.clinical.capture_baseline --rollouts 2` — generates a per-case score for the new case at the current agent state.
3. **Verify the per-case gate:** Re-run the capture; the gate should not fire (a freshly-added case at its own baseline has no regression to detect).
4. **Run a smoke evaluation:** `pytest tests/clinical/test_clinical_accuracy.py -k <case_id>` and verify the LLM-as-judge produces a score ≥ 3 (anything < 3 indicates the case is mis-specified or the agent has a real failure to address before merging).
5. **Update provenance + coverage docs** per § 10.
6. **Open the PR; tag the SME reviewer** per § 11.

## 14. Anti-patterns — do not do these

| Anti-pattern | Why it's wrong |
|---|---|
| Authoring a case to "make the suite bigger" without a stated gap or failure mode | Dilutes detection signal; adds maintenance cost without buying coverage. |
| Copying an existing case and changing the ID + one field | The new case is correlated with the original; failures on both don't actually constitute independent evidence. If you want a near-miss, put it in `near_miss_cases.py` and name the disqualifier explicitly. |
| Including real patient information | HIPAA violation. PR will be rejected and the branch deleted. |
| Inventing guideline content | Propagates into evaluation correctness. PR will be rejected at SME review. |
| Setting `reasoning_must_include = ["approved"]` (overly generic) | The judge will pass on noise. Use specific clinical phrases that distinguish good reasoning from generic reasoning. |
| Omitting `judge_scoring_criteria` | The judge defaults to a generic rubric. Cases without explicit scoring criteria get noisier scores. |
| Adding a case mid-iteration without listing it under a `chg-N` entry in the iteration manifest | Breaks the audit chain. Iter-N's "what was done" must be reconstructable from iter-N.json alone. |

## 15. Reference: the established failure-mode taxonomy

For `CASE_PROVENANCE.md`'s "Named failure mode" column, use one of:

- **Coverage** (routine positive or negative case for a gate)
- **Hallucination zero-tolerance** (sparse-docs adversarial)
- **False pattern-matching (memory trap)** (H2 memory compression failure mode)
- **Step-therapy enforcement** (must not accept patient-preference / cost-only justification)
- **Cross-condition memory bleed** (H2 entry for condition A must not generalize to condition B)
- **Test-data adequacy** (premature imaging / anti-pattern probes)
- **Discriminator (negative / ambiguous / positive) class** (complexity-score model training points)
- **Branch-N pre-flight** (positive case for branch N, where N ∈ 1..7)
- **Confidence-N boundary** (low / ambiguous / high)
- **High-cost / pediatric-complex / other policy-trigger override**

Extend only when an existing mode genuinely doesn't fit, and document the extension in `CASE_PROVENANCE.md`'s taxonomy section.

## 16. Working example — adding GC-026

Suppose we want to close the DENY-class gap with a clean denial case.

**Step 1 — state the gap.** From `EVALUATION_COVERAGE.md`: "DENY: 0 cases. Highest-priority gap." → new case is a clean denial.

**Step 2 — pick the clinical scenario.** A request for proton-beam therapy for a low-risk prostate cancer where guidelines (NCCN, ASTRO) recommend conventional radiation as first-line.

**Step 3 — fill the template:**

```python
GoldenCase(
    case_id="GC-026",
    title="Low-risk prostate proton-beam — denied per NCCN/ASTRO (conventional first-line)",
    diagnosis_code="C61",
    diagnosis_description="Malignant neoplasm of prostate",
    procedure_code="77523",
    procedure_description="Proton-beam radiation therapy",
    clinical_notes=(
        "62-year-old male with newly-diagnosed low-risk prostate adenocarcinoma "
        "(Gleason 3+3=6, PSA 5.2, clinical stage T1c). No prior therapy. "
        "Requesting proton-beam radiation therapy. No documented contraindication "
        "to conventional external-beam radiation therapy. Patient preference cited "
        "as justification ('fewer side effects')."
    ),
    guidelines_context=(
        "NCCN Prostate Cancer Guidelines + ASTRO Model Policy on Proton-Beam: "
        "proton-beam radiation is NOT recommended over conventional intensity-"
        "modulated radiation therapy (IMRT) for low-risk prostate cancer in the "
        "absence of a documented contraindication to IMRT. CMS National Coverage "
        "Determination 110.8.1 mirrors this position. Patient preference is not "
        "an accepted justification for the cost differential."
    ),
    expected_outcome=ExpectedOutcome.DENIED,
    expected_branch=EscalationBranch.NONE,
    reasoning_must_include=["low-risk", "IMRT", "patient preference"],
    reasoning_must_not_include=["approved", "auto-approve"],
    clinical_rationale=(
        "Clean denial case: low-risk prostate cancer with no contraindication to "
        "conventional IMRT, requesting a substantially more expensive modality "
        "with patient preference as the only justification. NCCN, ASTRO, and CMS "
        "NCD all align — proton-beam is not indicated here. Step-therapy "
        "enforcement principle (cf. GC-005) applies: patient preference is not "
        "an acceptable substitute for documented clinical justification."
    ),
    judge_scoring_criteria=(
        "Score highly if the rationale explicitly cites low-risk status, the "
        "guidelines' first-line recommendation for IMRT, the absence of a "
        "contraindication, and the rejection of patient preference as "
        "justification. Penalize for auto-approval or for IN_REVIEW (this case "
        "has unambiguous guideline alignment supporting denial)."
    ),
),
```

**Step 4 — wire up the docs** per § 10:

- `CASE_PROVENANCE.md`: new row `| GC-026 | golden_cases.py | Proton-beam for low-risk prostate; NCCN/ASTRO/CMS NCD all favor IMRT first-line; patient-preference-only justification | **Coverage** (DENY class — closes 0 → 1 gap from EVALUATION_COVERAGE.md); also step-therapy enforcement parallel to GC-005 | iter-6 |`
- `EVALUATION_COVERAGE.md`: bump DENY count to 1 in the outcome-distribution table.
- `DATASET_SUFFICIENCY.md`: bump total to 26.

**Step 5 — verify** per § 13.

**Step 6 — open PR with SME tag** per § 11.

This is the workflow every case follows.

---

*This document is part of the PACCA v2.3 harness-engineering cycle documentation set. Last updated: 2026-05-25 at iter-5 close.*
