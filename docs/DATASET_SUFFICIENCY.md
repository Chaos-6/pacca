# Dataset Sufficiency — Golden-Case Coverage, Statistical Power, and HIPAA / SaMD-Grade Validation Path

> **⚠️ Counting correction (2026-07-29, chg-27).** The figure below was **105, and it was wrong** — not by an authoring error but by a measurement one. It was "verified by unique-ID count", and three ids each identified two different clinical cases (GC-101/102/103, allocated independently by `adult_complexity_cases.py` on 2026-05-31 and re-used by `oncology_breadth_cases.py` on 2026-06-06). Counting ids cannot see that. The on-disk dataset is **108 case objects**; the duplicates were renumbered to GC-106/107/108, so the id range is now GC-001 through GC-108 with no collisions. Nothing was added or removed — the count changed because the *measurement* was corrected. `tests/unit/test_dataset_integrity.py` now gates against a recurrence by de-duplicating on object identity.
>
> **⚠️ Coverage caveat (2026-07-29).** Authored ≠ evaluated. Of the 108 case objects, the in-sample accuracy gate runs **39** and the held-out report runs **32**; the remaining **37 are executed by no evaluation run at all** — whole specialty files (hematology, oncology-depth, depth-extension, denial) that are imported and statically keyword-swept, but never scored by the pipeline. Any sufficiency, power, or coverage claim in this document that assumes the full dataset is exercised is therefore **overstated at present**. The live census prints on every run of `test_evaluation_reachability_census`.
>
> **⚠️ Reconciliation note (updated 2026-07-20).** The on-disk dataset is now **105 cases** (GC-001 through GC-105 — verified by unique-ID count across `tests/clinical/`), past the production-pilot milestone this document defines below. *(Superseded by the counting correction above — retained because the sufficiency verdicts below were derived at this stated figure.)* See [`PACCA_PRD_v2.5_Consolidated.md`](PACCA_PRD_v2.5_Consolidated.md) § 16.3 for the sufficiency verdicts re-derived at the 105-case state (10 pp aggregate-drop detection now met; DENY class now at 7, target ≥5 met). The per-specialty breakdowns in the body of this document were written for the **33-case state** and have NOT yet been re-derived per-cell — trust `tests/clinical/*.py` as the source of truth for which cases exist; trust the body only for the *methodology* and the milestone *targets* (100 / 300 / 500), which are unchanged.
>
> **Status:** body reflects 33-case state; on-disk dataset at 105 (production-pilot milestone crossed); PRD-level sufficiency verdicts re-derived in v2.5 § 16.3; per-cell body re-derivation still pending. Originally drafted 2026-05-25.
> **Audience:** payer-side reviewers, HIPAA / SaMD auditors, clinical-validation board members, prospective customers, and future PACCA contributors who need to defend (or expand) the evaluation dataset.
> **Scope:** PACCA's clinical golden-case evaluation set. Does NOT cover unit-test, integration-test, or performance-test coverage.
> **Companion documents:**
> - [`EVALUATION_COVERAGE.md`](./EVALUATION_COVERAGE.md) — the per-dimension coverage matrix at the current 33-case state.
> - [`STATISTICAL_POWER.md`](./STATISTICAL_POWER.md) — binomial CI math + per-case regression-detection math.
> - [`CASE_PROVENANCE.md`](./CASE_PROVENANCE.md) — for each case, the clinical rationale, the failure mode it probes, the iteration that added it.
> - [`CASE_AUTHORING_GUIDE.md`](./CASE_AUTHORING_GUIDE.md) — the template and SME-review process for adding new cases.
> - [`EVALUATION.md`](./EVALUATION.md) — the parent evaluation methodology stub (Phase H5 of the v2.3 harness cycle); see its "Train-on-test contamination and the held-out split" section for the declared 32-case holdout (`tests/clinical/holdout.py`, enforced by `tests/unit/test_eval_holdout_guard.py`) that keeps the 105-case size this document celebrates from also being read as evidence of clean generalization — some of those cases are named verbatim in the DecisionAgent's prompt/memory.

## Executive answer

**PACCA's current 33-case golden dataset is sufficient for portfolio-prototype credibility, insufficient for production payer deployment, and far below the threshold for HIPAA SaMD-grade clinical validation.** The defensible production-pilot target is 100 cases; the general-payer-deployment target is 300; the SaMD claim requires 500+ in-house plus an ongoing clinical-review-board sample.

The shortfall is not about overall accuracy — the live clinical gate has held at 20/20 = 100% across three iterations. It is about **the kinds of claims the dataset can support**: at 33 cases, PACCA can credibly claim *coverage* (every gate has at least one case on both sides, and every outcome class — including the previously-empty DENIED class — is represented), *failure-mode probing* (every documented LLM failure mode has a named case that catches it), and *cross-specialty breadth* (oncology, cardiology, rheumatology, GI, derm, asthma, T2DM, transplant, neurology, hematology/sickle-cell, behavioral, reproductive-endocrine, orthopedics). It cannot credibly claim *statistical generalization*, *demographic balance*, *within-specialty per-class regression signal*, or *production-load representativeness* — those are earned by adding more cases against an explicit specification.

**Iter-6 increment (this PR).** The dataset grew from 25 → 33 by adding `tests/clinical/expansion_cases.py` (GC-026 through GC-033), specifically targeting the highest-priority gaps identified in `EVALUATION_COVERAGE.md`: closing the 0-DENY gap (GC-026, GC-027), the zero-cardiology gap (GC-027, GC-028), the zero ≥ 80 geriatric gap (GC-028), and one-case-each coverage of behavioral health (GC-029), hematology/sickle cell (GC-030), transplant (GC-031), neurology (GC-032), and reproductive endocrine (GC-033).

## What claim do you want to make?

The table below maps the claim a stakeholder can defensibly hear from PACCA today, against the case count required for that claim and the qualitative + quantitative rationale.

| # | Claim | Cases required | Where PACCA is today | Quantitative rationale | Qualitative rationale |
|---|---|---|---|---|---|
| 1 | "We test every policy gate on both sides of the decision boundary" | ~50 | 33 (closer; DENY-side now covered) | 15–18 gates × 3 polarities (positive / negative / edge) = ~45–55 cases. PACCA today: ~15 gates with average 1.7 cases each ⇒ 25. | This is the **coverage floor** for any policy-enforcing system. Below it, you cannot honestly answer the reviewer's first question ("show me where you test the cost trigger") with a case-ID pointer for every gate. |
| 2 | "We catch aggregate accuracy drops of ≥20% (catastrophic breakage)" | ~15–20 | 33 (comfortably meets target) | Binomial 1-tail test, baseline 95% accuracy, regressed 75%, α=0.05, β=0.20 → n ≈ 15–20. | This is the **lowest defensible regression-detection bar**: it only catches breakage so obvious that almost any test would catch it. Suitable for the "smoke test" tier in CI. |
| 3 | "We catch aggregate accuracy drops of ≥10% (clinically meaningful)" | ~75–100 | 33 (below target — 42-67 more needed) | Binomial 1-tail test, baseline 95% accuracy, regressed 85%, α=0.05, β=0.20 → n ≈ 75–100. | The FDA SaMD validation guidance treats 5–10 percentage-point degradation as the threshold for "clinical significance." A regression in this range is the minimum the eval suite should flag in production. |
| 4 | "Per-case regression detection on every reasoning class" | ~3–5 per family × ~30 families = 100–150 | 33 (below target; iter-6 added 1 case each to 5 new specialty families) | iter-2 chg-2's per-case `regression_gate.py` provides 100% per-case sensitivity *regardless of dataset size*. But "every reasoning class" means every distinct decision pattern PACCA encounters, which requires multiple cases per pattern for the gate to have signal. | Per-case sensitivity is free; per-class coverage is purchased with cases. Without 3+ cases per pattern, a "regression on this class" is indistinguishable from single-case judge noise. |
| 5 | "We catch aggregate drops of ≥5% (subtle silent erosion)" | ~200–300 | 33 (far below target) | Binomial 1-tail test, baseline 95% accuracy, regressed 90%, α=0.05, β=0.20 → n ≈ 200–300. | Phase H2 institutional memory entries can erode reasoning quality without flipping decisions (the iter-2 chg-2 design motivation). Aggregate-level detection at this resolution requires the dataset size. |
| 6 | "Statistically powered for general payer deployment with population-representative case mix" | 300–500 | 33 (far below target) | Prevalence-weighted sampling across major UM specialties: 25 oncology + 12 rheumatology + 10 pulmonology + 10 endocrinology + 10 cardiology + ... ≈ 300+ minimum. Adds 100+ cases for demographic balance (age, gender, ethnicity, SES). | Production payer deployment requires that the evaluation distribution mirror the actual claim distribution — a Medicare Advantage plan's case mix differs from a pediatric Medicaid plan's. Defends the "tested on cases like yours" claim. |
| 7 | "HIPAA / SaMD-grade clinical validation, audit-defensible" | 500+ in-house + ongoing 100/quarter clinical-review-board sample | 33 (portfolio-only) | FDA SaMD guidance (IMDRF SaMD WG/N12 N41 framework + 21 CFR 820.30) implicitly requires (a) statistically-defensible sample size for each clinical specialty the device is labeled for; (b) inter-rater reliability with ≥3 independent clinical reviewers, target Cohen's κ ≥ 0.80; (c) ongoing post-market surveillance evaluation. | This is the bar for "this AI can make clinical recommendations that influence patient care under HIPAA SaMD." Not the bar for "this AI assists human decision-makers" (which is where PACCA is positioned today). |

### Per-line-item detail

**Claim 1 — Per-gate coverage.** A "gate" is any policy rule the system enforces that can fire or not fire on a given case. PACCA's gates today include the seven escalation branches (CONFIDENCE_BELOW_THRESHOLD, MEDICAL_DIRECTOR_REQUIRED, EXPERIMENTAL_TREATMENT, RARE_CONDITION, CONFLICTING_GUIDELINES, PRIOR_DENIAL_SAME_SERVICE, HIGH_COST, PEDIATRIC_COMPLEX), the clinical-eligibility checks embedded in the system prompt (NCCN / ACR / ACG / NIST-GINA criteria for the major drug classes), and the three confidence-threshold paths (auto-approve / ambiguous / deny). Each needs a positive case (the rule SHOULD fire), a negative case (the rule should NOT fire), and ideally an edge case (at the threshold). Without all three polarities the reviewer cannot trust that the rule actually discriminates the way the code claims it does.

**Claim 2 — 20% aggregate drop detection.** Calculation: for a one-sided binomial test of `p₀ = 0.95` against `p₁ = 0.75`, the required sample size for `α=0.05, β=0.20` is computed via the normal approximation `n ≈ (z_α √(p₀(1-p₀)) + z_β √(p₁(1-p₁)))² / (p₀-p₁)²`. With `z_α = 1.645, z_β = 0.842, p₀-p₁ = 0.20`, this evaluates to `n ≈ ((1.645·0.218) + (0.842·0.433))² / 0.04 = (0.358 + 0.365)² / 0.04 ≈ 13`. Round up to 15-20 for safety margin and to absorb edge effects. PACCA's 25 comfortably meets this. The reason it's not enough: 20% is **catastrophic breakage** — the kind of regression where the test would almost certainly fail anyway, even on a smaller dataset. It's the smoke-test bar, not the production bar.

**Claim 3 — 10% aggregate drop detection.** Same formula, `p₀-p₁ = 0.10`. `n ≈ ((1.645·0.218) + (0.842·0.357))² / 0.01 = (0.358 + 0.301)² / 0.01 ≈ 43`. With safety margin and per-specialty stratification, 75-100 is the operational target. This is the threshold the FDA SaMD validation guidance implicitly uses: any change in a clinical AI's accuracy of 5-10 percentage points is "clinically significant" and triggers re-validation. A dataset that cannot detect a clinically-significant change cannot honestly claim production-grade regression coverage.

**Claim 4 — Per-case per-class detection.** Per-case sensitivity comes from iter-2 chg-2's `regression_gate.py` for free — any drop on any individual case fires the gate regardless of how many cases exist. What costs cases is *per-class* statistical confidence: with one case in a class, a single-rerun jitter event is indistinguishable from a real regression. With 3-5 cases per class, the same jitter event affects one case and the other 2-4 stay stable — the gate fires on the one but the class as a whole is clearly fine. Per-class coverage is therefore the actual driver of how many cases per reasoning pattern.

**Claim 5 — 5% aggregate drop detection.** Same formula, `p₀-p₁ = 0.05`. `n ≈ ((1.645·0.218) + (0.842·0.3))² / 0.0025 = (0.358 + 0.253)² / 0.0025 ≈ 150`. Operational target 200-300 because the binomial approximation degrades at small `p₀-p₁` and because per-specialty stratification multiplies the required count. Phase H2 institutional memory is the canonical case for needing this resolution: a memory entry that erodes reasoning quality by 5% on average might not flip enough decisions to trigger Claim 3's gate, but the cumulative reasoning degradation is real and reviewer-visible.

**Claim 6 — Population-representative.** This is the first claim that requires the dataset to reflect the *customer's* claim distribution, not just the system's policy gates. A representative sample for a typical commercial payer (Blue Cross / Aetna / United / Humana commercial population) distributes roughly: 25% oncology (including biologics), 12% rheumatology/immunology, 10% pulmonology, 10% endocrinology, 10% cardiology, 8% mental health, 8% GI, 7% orthopedics/imaging, 10% other. Pediatric- and geriatric-heavy plans have different distributions and need their own representative samples. The 300-case target reflects the minimum sum across major specialties at production prevalence.

**Claim 7 — HIPAA SaMD-grade.** The Food, Drug, and Cosmetic Act §513(a)(1)(C) classifies software-as-a-medical-device by risk. PACCA at production scale (autonomous prior-authorization decisions) would fall under Class II SaMD per the IMDRF SaMD WG/N12 framework's Categorization Matrix (states C/D × healthcare situations II/III). FDA guidance for Class II SaMD requires clinical validation evidence proportionate to risk, with three pillars: (1) analytical validation (algorithm correctness — what PACCA's golden cases test), (2) clinical validation (the AI's recommendations match expert clinical judgment — what an independent review board provides), (3) clinical performance evaluation (ongoing post-market surveillance). The 500+ in-house number is the analytical-validation minimum; the 100/quarter clinical-board sample is the ongoing clinical-validation minimum.

## Where PACCA is, by dimension

The table below maps each evaluation-relevant dimension to current coverage and gap. Companion: [`EVALUATION_COVERAGE.md`](./EVALUATION_COVERAGE.md) for the full per-case matrix.

| Dimension | Coverage today | Specific gap | Qualitative rationale | Quantitative target |
|---|---|---|---|---|
| **Outcome class** (5: AUTO_APPROVED, IN_REVIEW, INFORMATION_NEEDED, DENIED, PRE_FLIGHT_ESCALATE) | All 5 represented but unbalanced — most cases are AUTO_APPROVE or IN_REVIEW; only 1–2 clear DENY-class cases | Need 3–5 cases with `expected_outcome=DENIED` that can be auto-denied without escalation; 2–3 cases that produce INFORMATION_NEEDED on insufficient docs without ambiguity | Phase H2 memory entries today encode approve patterns only. A deny-class memory entry (e.g., compressing "biologic without step therapy completion → deny") cannot be validated without enough deny-class cases | 5 DENY cases minimum at 100-case milestone; 15 at 300; 25+ at 500 |
| **Escalation branch** (7) | All 7 covered (`test_escalation_branch_coverage` enforces it) | Most branches have 1 case; only confidence-low and medical-director have 2+ | Per-class regression detection (Claim 4) needs 3+ cases per branch so single-case jitter doesn't masquerade as a real branch regression | 3 cases per branch (21 total just for branches) at 100; 5 at 300; 7+ at 500 |
| **Age brackets** (5: <12 / 12–17 / 18–64 / 65–79 / 80+) | 4 pediatric (10/14/16/9 — iter-5 chg-2 just fixed this); 14 adult 18–64; 2 older adult 65–79; 0 elderly 80+; 0 adolescent (12–17 only via GC-024) | No 80+ cases at all. iter-5 chg-3 complexity-score treats `age > 75` as a complexity weight but has no validation. Adolescent (12–17) and elderly (80+) cases needed for the score model to be defensible across the full age range. | The complexity-score model in `_check_pediatric_complex` was scoped to age < 18, but its underlying weights apply to age > 75 too. Without 80+ cases the geriatric weight is untested in production. | 3 cases each for `<12`, `12–17`, `65–79`, `80+` at 100-case milestone; double at 300 |
| **Disease specialty** (12 major UM specialties — oncology, rheumatology, pulmonology, GI, endocrine, cardiology, mental health, orthopedics/imaging, derm, neuro, OB/GYN, transplant) | ~9 covered (oncology, rheumatology, pulmonology, GI, endocrine, derm, psoriasis/PsA, rare disease, lumbar MRI) | Cardiology absent (major UM specialty for production payers); mental health absent; OB/GYN absent; transplant absent | Cardiology alone is ~10% of UM volume in commercial payers. A dataset with zero cardiology cases cannot honestly claim to test PACCA's reasoning on heart failure, structural disease, or implantable device PA — entire policy gates may exist that have never been tested. | 5 cases per missing specialty (≈ 20 added cases) at 100-case milestone; double at 300 |
| **Comorbidity load** | Lightly covered: GC-024 has growth-delay, GC-025 has atopic march, GC-013 has comorbid hypertension | Need cases with explicit 2+ active comorbidities (the typical chronic-disease pattern for ~30% of real UM volume) | The complexity-score model's "comorbidities +1" weight is currently triggered by keyword matches like "comorbid", "history of", "concurrent". Without multi-comorbidity cases, the weight is undertested — false negatives (a comorbid case the parser misses) are invisible. | 5 cases with documented 2+ comorbidities at 100-case milestone; 15 at 300 |
| **Documentation completeness** (3: complete / ambiguous / sparse) | 2 hallucination traps for sparse (GC-018, GC-019); most cases are "complete" | Need 5+ sparse cases at varied completeness tiers (single missing lab, multiple missing fields, narrative-only with no structured data) | Real-world claim data has variable completeness. Without graded coverage, PACCA cannot defend the claim that it handles real-world documentation noise — only the claim that it handles clean documentation. | 5 sparse + 3 ambiguous cases at 100; 15 + 10 at 300 |
| **Cost tier** (3: under threshold / at threshold / over threshold) | GC-010 is the canonical "over threshold" ($288K vs $100K). No "at threshold" cases ($95K-$105K range). | Need cases at the cost boundary (just-under and just-over) to test the cost-check's threshold behavior under realistic numeric ambiguity (rounding, currency formatting, per-unit vs annualized) | The cost parser uses MAX of all dollar amounts in clinical notes (iter-3 chg-1). This was the bug a smoke-test caught on GC-010. Without boundary cases, the parser's behavior at the threshold is untested. | 3 cost-boundary cases at 100; 8 at 300 |
| **Demographics — gender** | Not tracked as a structured field on GoldenCase | All cases mention gender in clinical notes ("58-year-old male", "47-year-old female", etc.) but it's not auditable as a per-case attribute | Production audit defensibility requires "we tested across both biological sex and (where applicable) gender identity." Currently impossible to claim equity testing. | Add `patient_gender` field to GoldenCase; populate all 25 existing cases; balance 50/50 across all new cases at 100/300/500 |
| **Demographics — ethnicity / race** | Not tracked anywhere | Real-world UM has documented racial disparities in approval rates. A dataset without ethnicity tracking cannot validate that PACCA does NOT exhibit those biases. | This is the equity-testing dimension. FDA SaMD guidance and CMS health-equity initiatives both require demographic-stratified accuracy metrics for production deployment. | Add `patient_ethnicity` field; populate existing + new cases proportionally to US population (and to customer's payer population) at 300/500 |
| **Demographics — socio-economic** (SES, insurance type, geography) | Not tracked | Insurance type (Medicare vs Medicaid vs Commercial) drives different policy rules, formularies, and approval workflows. PACCA today is policy-agnostic; a real deployment would have policy variants. | A general payer-deployment claim requires that PACCA's behavior under each insurance type is tested. Currently no insurance-type tracking. | Add `insurance_type` enum; cases per type at 300/500 |

### Per-dimension detail

**Outcome class.** The unbalanced distribution (heavy AUTO_APPROVED + IN_REVIEW, light DENIED) reflects PACCA's design positioning as a *decision-support* system that biases toward escalation rather than autonomous denial. That's clinically defensible (denial is high-stakes, deserves human judgment), but it leaves the agent's deny-reasoning untested. A future H2 entry that compresses "biologic without step therapy → deny" needs validation cases, and you can't fit a memory entry to data you don't have.

**Escalation branch.** All 7 branches are covered, but most have a single case. The per-case regression gate (iter-2 chg-2) provides 100% per-case sensitivity, but the per-class signal-to-noise improves with more cases. Three cases per branch is the minimum for distinguishing "this branch class is regressing" from "one case in this branch happened to jitter."

**Age brackets.** iter-5 chg-2 fixed the pediatric gap. The next gap is geriatric (80+) and adolescent (12-17). The complexity-score model has an "age > 75" weight that today has zero validation cases. That weight could be silently wrong without anyone knowing.

**Disease specialty.** The absent specialties (cardiology, mental health, OB/GYN, transplant) each represent significant UM volume. A claim of "PACCA handles your payer's UM volume" cannot be defended without representative coverage of each major specialty.

**Comorbidity load.** The complexity-score model relies on keyword matching for "comorbid", "history of", "concurrent". Real clinical documentation expresses comorbidities in many ways. Without graded cases, the parser's robustness is untested.

**Documentation completeness.** The two hallucination traps (GC-018, GC-019) test the most extreme sparse-notes failure mode. Real claims sit between "complete" and "sparse" — partial sparseness is the common case and is untested.

**Cost tier.** GC-010 tests the "well over threshold" case. Cost-boundary cases (e.g., $98K and $105K) test the parser's handling of currency formats, rounding, and per-unit vs annualized at the actual decision boundary where parsing errors matter most.

**Demographics.** These are equity-testing dimensions. Production AI in healthcare must demonstrate it doesn't exhibit demographic biases. PACCA today cannot — not because it does, but because the dataset has no structured demographic fields. The fix is mechanical (add fields, populate cases) but the *defense* requires the data.

## Three frameworks for sizing the dataset

The case-count target depends on which framework you use. Use all three layered.

### Framework 1: Per-gate coverage (the floor)

**Premise:** Every policy rule the system enforces needs at least one case that proves the rule fires correctly and one that proves it doesn't fire when it shouldn't, plus an edge case at the threshold.

**Math:**
- Number of distinct gates: G
- Polarities per gate: 3 (positive / negative / edge)
- Total = G × 3

**For PACCA today:**
- 7 escalation branches
- ~5–8 clinical eligibility patterns (NSCLC pembrolizumab, RA biologic, asthma dupilumab, Crohn's biologic, lumbar MRI, T2DM SGLT2, etc.)
- 3 confidence-threshold paths (auto-approve / ambiguous / deny)
- ≈ 15–18 gates × 3 = **45–55 cases minimum**

**What it defends:** "Every policy gate has a case on each side of its decision boundary." A reviewer can point at any gate and ask "show me a case where this fires and one where it doesn't." Below this floor, that question has no answer for some gates.

**What it does not defend:** Statistical claims, demographic claims, prevalence-representative claims. It is purely a *coverage* check.

### Framework 2: Statistical power (the regression-detection bar)

**Premise:** The aggregate accuracy metric (e.g., "≥80% pass rate on the golden set") must be statistically distinguishable from a degraded baseline before the gate can fire.

**Math:** Binomial proportion test. For two proportions `p₀` (baseline) and `p₁` (regressed), with significance level `α` and power `1-β`:
```
n ≈ (z_α · √(p₀(1-p₀)) + z_β · √(p₁(1-p₁)))² / (p₀ - p₁)²
```
where `z_α` is the one-sided critical value at `α` and `z_β` is the critical value at `β`.

**Table:** detailed in [`STATISTICAL_POWER.md`](./STATISTICAL_POWER.md).

| Drop you want to detect (Δ pp) | Required n (α=0.05, β=0.20) | Practical operational target | What it buys |
|---|---|---|---|
| 20 (95% → 75%) | ~15 | 20 | "We catch catastrophic breakage" |
| 10 (95% → 85%) | ~43 | 100 | "We catch clinically meaningful regressions" |
| 5 (95% → 90%) | ~150 | 200–300 | "We notice silent reasoning erosion" |
| 2 (95% → 93%) | ~600+ | 600+ | "We can compare iterations precisely" |

**What it defends:** The aggregate-level regression-detection claim. The per-case regression gate (iter-2 chg-2) provides 100% per-case sensitivity for free; the aggregate-level math here is the *secondary* layer.

**Important note:** The per-case gate dominates for most practical purposes. The aggregate-level math matters when you need a single summary statistic to defend ("aggregate accuracy did not drop by more than X%") for a customer-facing report or an FDA submission.

### Framework 3: Real-world prevalence calibration (the customer-defense bar)

**Premise:** The evaluation set should mirror the customer's actual claim distribution by ICD-10 chapter, specialty, age, and other relevant population dimensions. A dataset that's 50% oncology when the customer's volume is 10% oncology cannot honestly claim representative testing.

**Math:** Per-specialty cases ≈ (specialty % of claim volume) × (total dataset size).

**For a typical commercial payer (general adult population) at 100-case target:**

| Specialty | % of UM volume | Cases at N=100 | Cases at N=300 | Cases at N=500 |
|---|---|---|---|---|
| Oncology (incl. biologics) | 25% | 25 | 75 | 125 |
| Rheumatology / immunology | 12% | 12 | 36 | 60 |
| Pulmonology / asthma / COPD | 10% | 10 | 30 | 50 |
| GI / IBD | 8% | 8 | 24 | 40 |
| Endocrinology | 10% | 10 | 30 | 50 |
| Cardiology | 10% | 10 | 30 | 50 |
| Mental health / psych | 8% | 8 | 24 | 40 |
| Orthopedics / imaging | 10% | 10 | 30 | 50 |
| Other (derm, neuro, OB/GYN, transplant, peds, geriatrics) | 7% | 7 | 21 | 35 |
| **Total** | **100%** | **100** | **300** | **500** |

**What it defends:** "We tested on cases representative of your population." A claim that depends on this defense fails when the customer's actual distribution differs (e.g., a pediatric-Medicaid payer or a Medicare Advantage plan). Each major customer segment needs its own prevalence-calibrated evaluation slice.

**What it does not defend:** Demographic balance within each specialty. That is a layer above prevalence (requiring stratification on age × specialty × demographics).

### Layering the three frameworks

The three frameworks compose: a dataset must pass **all three** to support the corresponding claim level.

- **At 50 cases**: passes Framework 1 (per-gate coverage); fails Framework 2 (cannot detect 10% drops); fails Framework 3 (no prevalence calibration). Claim level: *coverage*.
- **At 100 cases**: passes Framework 1 with margin; passes Framework 2 for 10% drops; partially passes Framework 3 (specialty distribution roughly right for one customer segment). Claim level: *production pilot*.
- **At 300 cases**: passes all three for one customer segment with margin; supports 5% drop detection partially. Claim level: *general payer deployment for that segment*.
- **At 500+ cases**: passes all three at full resolution; supports 5% drop detection with margin; supports demographic stratification. Claim level: *SaMD-grade analytical validation* (still requires the clinical-validation board separately).

## Thresholds in detail

### 50 cases — coverage floor

**What it buys:**
- Every policy gate has a case on both sides
- Every escalation branch has at least one case
- Every documented failure mode (hallucination, false-pattern-matching, silent reasoning degradation) has a named case
- 100% per-case regression detection (free from iter-2 chg-2 regardless of dataset size)

**What it does not buy:**
- Aggregate-level statistical regression detection above the "catastrophic" 20% threshold
- Specialty-prevalence claims
- Demographic-stratification claims
- Edge-case behavior at policy thresholds (need cost-boundary, age-boundary cases)

**Defensible customer claim:** *"PACCA tests every policy gate on both sides of the decision boundary and probes every documented failure mode with a named case that catches that class. The evaluation set is versioned alongside the policy code so any change to either is traceable to the cases that prove it. At this dataset size, we report per-case regression detection but do not claim statistical power for aggregate trend detection."*

### 100 cases — production pilot

**What it buys (in addition to 50-case threshold):**
- Aggregate-level detection of ≥10% accuracy drops (clinically meaningful per FDA SaMD guidance)
- 3 cases per escalation branch (per-class regression signal)
- Per-specialty floor coverage (≥3 cases per major UM specialty)
- Cost-boundary, age-boundary, comorbidity, and graded-completeness cases populated
- Outcome-class balance (5+ DENY cases, balanced AUTO_APPROVE / IN_REVIEW)

**What it does not buy:**
- 5% drop detection (need 200-300 for that)
- Full demographic stratification (need 300+ for race / ethnicity / gender × specialty)
- Multi-customer-segment representativeness (each segment needs its own slice)

**Defensible customer claim:** *"PACCA's evaluation set tests every policy gate on both sides, mirrors typical commercial-payer specialty distribution, and provides 80% statistical power to detect clinically-meaningful (≥10 percentage point) aggregate accuracy drops. Per-case regression detection is 100% sensitive regardless of drop size. Suitable for staged production rollout in a typical commercial payer."*

### 300 cases — general payer deployment

**What it buys (in addition to 100-case threshold):**
- Aggregate-level detection of ≥5% accuracy drops (approaching SaMD-grade)
- 5 cases per escalation branch (strong per-class regression signal)
- Demographic stratification for one customer segment (gender + age + 2-3 ethnicity bins per specialty)
- Multiple-comorbidity coverage at production prevalence
- Edge-case coverage at every policy threshold

**What it does not buy:**
- SaMD-grade clinical validation (requires the ongoing clinical-review-board sample separately)
- All-segment representativeness (still typically slanted toward one customer's population)

**Defensible customer claim:** *"PACCA's 300-case evaluation set mirrors your payer's specialty distribution, is demographically balanced across age / gender / ethnicity within each major specialty, and provides 80% statistical power to detect 5% aggregate accuracy drops. We re-baseline quarterly against your historical claim sample. Suitable for general payer deployment in your population segment."*

### 500+ cases — SaMD-grade analytical validation

**What it buys (in addition to 300-case threshold):**
- Full statistical power at 5% drop detection across every major specialty (not just aggregate)
- Demographic stratification at population-representative resolution
- Coverage of multiple customer segments (commercial + Medicare + Medicaid pediatric + Medicaid adult, each with their own slice)
- Audit trail for every case (provenance, clinical rationale, named failure mode, iteration of origin)
- Inter-rater reliability data (each case scored by ≥3 independent clinical reviewers, Cohen's κ ≥ 0.80)

**What it does not buy by itself:**
- The clinical-validation pillar (FDA SaMD requires ongoing independent clinical-board review separately)
- Post-market surveillance evaluation (requires production deployment with feedback loop)

**Defensible customer claim:** *"PACCA's evaluation regime combines a 500-case in-house golden set with ongoing clinical-review-board sampling (100+ cases / quarter scored by ≥3 independent clinical reviewers with target Cohen's κ ≥ 0.80). The in-house set provides 80% power to detect 5% drops within each major specialty. Findings from each quarterly board review feed back into the golden set. This regime supports HIPAA SaMD Class II analytical validation per FDA guidance."*

## Effort estimates

The case-authoring work is the bottleneck. A single high-quality golden case requires:
- Clinical case synopsis with realistic ICD-10 / CPT codes (~15 min)
- Guideline citation block (~10 min)
- Expected outcome + branch designation (~5 min)
- Reasoning keywords (must-include + must-not-include) (~15 min)
- Clinical rationale (~15 min)
- Judge scoring criteria (~15 min)
- Clinical SME review pass (~15 min, separate person)
- Live verification (~5 min including API call)
- Total: ~90 minutes per case at SME quality

| Target | Cases to add | Effort (single-author, SME-reviewed) | Effort (clinical writer + SME review) | Calendar (1 FTE) | Calendar (1 FTE + clinical SME at 0.25 FTE) |
|---|---|---|---|---|---|
| **100** | +75 | ~112 hours | ~75 hours | 3 weeks | 2 weeks |
| **300** | +275 | ~412 hours | ~275 hours | 10 weeks | 7 weeks |
| **500** | +475 | ~712 hours | ~475 hours | 18 weeks | 12 weeks |

**Notes on the estimate:**
- The numbers assume an experienced clinical writer producing cases at the iter-2 NEAR_MISS_CASES / iter-5 PEDIATRIC_CASES quality level (which took ~60-90 min each to author + verify in this codebase).
- The "clinical writer + SME review" column splits the work: ~45 min author + ~15 min SME = 60 min effective per case at higher quality.
- Calendar assumes 40 hours/week productive time. Add 50% for context-switching overhead in solo work.
- The 500-case target should be paired with the clinical-review-board ongoing sampling; budget separately for that (estimated ~$15K-25K per quarter for an external review panel of 3 specialists).

## Recommended priority order — what to add first

The order maximizes coverage gain per case authored. Numbers below are the *increment* on top of the current 25 cases.

1. **DENY-class cases (5)** — the highest-leverage gap. Zero current cases have `expected_outcome=DENIED` with a clear deny pattern. Examples: biologic without any step therapy completion (clearer than GC-005 which is IN_REVIEW); cosmetic procedure misclassified as medically necessary; experimental treatment with explicit non-coverage. Enables a future H2 deny-pattern memory entry.

2. **Cardiology cases (5)** — major UM specialty, totally absent. Examples: heart failure with reduced ejection fraction requesting ARNI (Entresto); structural heart device (TAVR, MitraClip); ICD primary prevention. Adds a whole specialty's policy-rule class to coverage.

3. **Geriatric (80+) cases (3)** — the complexity-score model's "age > 75" weight has zero validation. Examples: 84yo with multi-morbidity requesting biologic; 87yo on polypharmacy requesting new agent (interaction concerns); 92yo with limited life expectancy considering oncology treatment.

4. **Demographic-balance pass (~10)** — add `patient_gender`, `patient_ethnicity`, `insurance_type` fields to GoldenCase. Populate existing 25 cases (from clinical_notes where stated). Add 10 new cases balanced across underrepresented combinations. Enables equity claims.

5. **Sparse-notes / documentation-completeness ladder (5)** — beyond GC-018/019 hallucination traps. Examples: case with only chief complaint + ICD-10 (no labs); case with structured fields but no narrative; case with narrative but no structured fields. Tests parser robustness at varied completeness tiers.

6. **High-prior-denial / appeals cases (3)** — branch_7 (PRIOR_DENIAL_SAME_SERVICE) has only 1 case. Examples: re-request for previously-denied drug with new clinical evidence (criteria now met); re-request without new evidence (still should deny); appeal request escalation.

7. **Multi-comorbidity cases (5)** — 2+ active conditions per case. Examples: T2DM + CKD + obesity (multi-drug interaction); RA + CHF (biologic safety concern); IBD + depression (psych-screening obligation). Tests the comorbidity-detection parser and complexity weight.

8. **Mental-health / psych UM cases (3)** — different policy-rule class entirely (parity rules, MHPAEA). Examples: TMS for treatment-resistant depression; ketamine infusion authorization; intensive outpatient program (IOP) approval.

**Subtotal of priority batch:** 39 cases. Combined with current 25 → **64 cases**.

To reach 100, add another **36 cases** spanning:
- 5 more cardiology (different sub-specialties)
- 5 more oncology (covering CAR-T, immunotherapy, targeted therapy beyond NSCLC)
- 5 more rheumatology / immunology (Sjögren's, vasculitis, IBD biologics not yet covered)
- 5 more pulmonology (COPD, severe asthma alternatives to dupilumab)
- 5 more endocrinology (DKA, diabetic complications, thyroid)
- 5 more orthopedics / imaging (specific MRI / surgical indications)
- 3 more transplant / nephrology
- 3 more pediatric edge cases (chronic-condition pediatric, neonatal authorization)

## References

| Source | Use |
|---|---|
| Lin et al. (2026), *Agentic Harness Engineering* (arXiv:2604.25850) | The cycle's parent methodology. §4 ("Evaluation"), §5.1 ("Coverage"), Appendix B ("Statistical power"). |
| FDA, *Software as a Medical Device (SaMD): Clinical Evaluation* (Dec 2017; updated guidance forthcoming) | The three-pillar framework (analytical / clinical / clinical performance) used in Claim 7. |
| IMDRF SaMD WG/N12 (2014), *"Software as a Medical Device": Possible Framework for Risk Categorization and Corresponding Considerations* | The Categorization Matrix used to classify PACCA as Class II. |
| IMDRF SaMD WG/N41 (2017), *Software as a Medical Device (SaMD): Clinical Evaluation* | Aligned with FDA's 2017 guidance; the international counterpart. |
| 21 CFR 820.30 — Design Controls | The regulatory framework for SaMD design validation that underlies the analytical-validation pillar. |
| CMS *Health Equity Index* (2024) | The demographic-stratification requirement that underlies the equity-testing dimension. |
| MHPAEA (Mental Health Parity and Addiction Equity Act, 2008) | The parity-rule basis for the mental-health UM cases (priority item 8). |
| HEDIS (Healthcare Effectiveness Data and Information Set), NCQA | The prevalence-weighted-sampling pattern used in Framework 3. |
| NCCN, ACR, ACG, NIST/GINA Guidelines | The clinical-guideline citations used in every PACCA case's `guidelines_context`. Specialty-specific. |
| Cohen, J. (1960), *A coefficient of agreement for nominal scales* | Cohen's κ inter-rater reliability metric cited in Claim 7. |
| PACCA's own [`HARNESS.md`](./HARNESS.md), [`DECISIONS.md`](./DECISIONS.md), [`ITERATIONS.md`](./ITERATIONS.md) | The internal methodology these targets are scoped to. |

## How to use this document

- **Customer conversations** — pick the claim level you want to make (table in §1), point at the dataset's current state, and either defend the claim or scope the gap honestly.
- **Iteration planning** — use the priority order (§5) to scope the next data-only iteration. Each batch closes a specific gap with a specific case count.
- **Audit defense** — the references (§7) ground every claim in an external source. The companion documents (`EVALUATION_COVERAGE.md`, `STATISTICAL_POWER.md`, `CASE_PROVENANCE.md`) provide the per-case, per-cell, per-calculation evidence.
- **Honest framing** — at any dataset size below SaMD-grade, the document IS the methodology defense. The cases prove the gates work; this document explains why the dataset is the right size for the claims being made.

---

*This document is part of the PACCA v2.3+ harness-engineering cycle documentation set. Updated when the dataset crosses a threshold (50 / 100 / 300 / 500) or when a framework calculation changes. Last updated: 2026-05-25 (iter-6 open, 33-case state — closing CASE_AUTHORING_GUIDE.md scaffolding and the first 8-case gap-closure batch).*
