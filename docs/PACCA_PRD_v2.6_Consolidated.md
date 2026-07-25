# PACCA — Product Requirements Document, v2.6 Consolidated

*For the reader evaluating engineering capability: this PRD is itself the work sample. Every number traces to the repository; the honest-claims matrix (§16.9) states plainly what the system cannot yet defend; and the clinical-review board is described as forming, not operational, because no sweep has run. That discipline — building agentic systems in a regulated domain and reporting them with receipts rather than optimism — is the capability on display. v2.6 is a **hardening & governance** consolidation: the dataset is unchanged (still 105 cases), so §16 carries forward, while §15 records the runtime-governance rollout, the persistence-integrity fixes, and the institutional-memory governance shipped across iterations 7–13.*

> **Status:** Active. **iterations 0–13 closed and tagged/recorded** (`harness-iter-0` … `harness-iter-13`), **31 changes, 0 rollbacks**. Replaces [`PACCA_PRD_v2.5_Consolidated.md`](PACCA_PRD_v2.5_Consolidated.md). v2.5 recorded the 105-case dataset threshold; **v2.6 records the July governance-and-hardening arc** (§15.5): the P-3/P-4/P-5 runtime governance layer, two P0 persistence-integrity fixes (B3 deferrable audit FK, B6 server-side `decision_id`) verified on real PostgreSQL, a real-Postgres CI guard, and the institutional-memory precedent collection brought fully under the scope guard. The dataset is unchanged, so §16 is carried forward.
> **Predecessor (v2.2 narrative content):** [`PACCA_PRD_Consolidated.md`](PACCA_PRD_Consolidated.md) — §1–§14 carried forward unchanged.
> **Audience:** prospective payer customers, HIPAA / SaMD reviewers, clinical-validation board members, investors evaluating production readiness, and the engineering team executing the dataset and validation roadmap.
> **Honesty disclaimer:** PACCA's evaluation dataset is **105 cases** (unchanged since v2.5). This crosses the 100-case production-pilot threshold defined in the v2.4 roadmap, which unlocks a discrete, defensible set of new claims (most importantly ≥10 pp aggregate-drop detection at 80% power, and a sufficient DENY-class sample). It does **not** make the dataset SaMD-grade — that requires 500+ in-house cases plus an ongoing clinical-review-board sample, and the gap is quantified in §16.3 and §16.8. The engineering-maturity scores describe architecture and harness maturity, **not** clinical-validation sufficiency; the two are scored separately and on purpose. The Phase 2 clinical-review board is **in formation, not operational** (§16.7). **v2.6 is a minor (backward-compatible) release** — hardening, governance, and bug fixes; no breaking change, no new clinical claim.

---

## Table of contents

- [§1–§14 — carried forward from v2.2](#§1§14--carried-forward-from-v22)
- [§15 — Harness Engineering Cycle (summarized; canonical docs cross-referenced)](#§15--harness-engineering-cycle)
- [§16 — Clinical Validation Strategy (updated for the 105-case state)](#§16--clinical-validation-strategy)
  - §16.1 — Validation framework alignment (FDA SaMD + IMDRF)
  - §16.2 — Current evaluation surface
  - §16.3 — Dataset sufficiency — current state vs. SaMD-grade target
  - §16.4 — Statistical-power evidence
  - §16.5 — Per-case provenance and audit trail
  - §16.6 — Coverage matrix and gap analysis
  - §16.7 — Clinical-SME review process (Phase 1 + Phase 2, two-stage CRB)
  - §16.8 — Roadmap: production-pilot (100, reached) → deployment (300) → SaMD-grade (500+)
  - §16.9 — Honest assessment of claims PACCA can defend today
- [§17 — HIPAA Compliance Cross-Reference](#§17--hipaa-compliance-cross-reference)
- [§18 — Change Log](#§18--change-log)

---

## §1–§14 — Carried Forward from v2.2

Sections §1 through §14 remain unchanged from [`PACCA_PRD_Consolidated.md`](PACCA_PRD_Consolidated.md). Topics covered there:

- §1 Vision and product positioning
- §2 User personas and primary use cases
- §3 System scope (in / out)
- §4 Functional requirements (REQ-FN-001 through REQ-FN-NNN, IEEE 29148 format)
- §5 Non-functional requirements (NFR)
- §6 Data model and PHI handling
- §7 Multi-agent architecture (Clinical Risk Detector → Decision Agent → Pre-Flight Branches)
- §8 RAG layer (Phase H4 — guidelines retrieval)
- §9 Audit and observability requirements
- §10 Integration surface (FHIR, X12 278, payer APIs)
- §11 Security model (auth, authorization, secrets, key rotation)
- §12 Performance and scale targets
- §13 Acceptance criteria
- §14 Glossary

These sections (§1–§14) are stable and not re-litigated here. The substantive change in **v2.6** is the rewrite of §15 (the harness cycle) to record iterations 7–13 — the runtime governance layer, the persistence-integrity fixes, and the institutional-memory governance. §16 (the clinical-validation chapter) is carried forward unchanged from v2.5, because the dataset is unchanged at 105 cases.

---

## §15 — Harness Engineering Cycle

PACCA implements the v2.3 harness-engineering methodology described in Lin et al. (2026), *Agentic Harness Engineering* (arXiv:2604.25850). The full methodology and per-iteration record live in dedicated documents — §15 here is the index and the cross-reference policy.

### §15.1 — Canonical documents

| Document | Covers |
|---|---|
| [`HARNESS.md`](HARNESS.md) | The 11 editable harness surfaces (Phases H1–H7 plus PACCA-specific extensions: escalation branches, RAG collections, prompt registry, audit schema), the three observability pillars (logs, metrics, traces), and the three rules of engagement (one change per iteration, change-manifest contract, run-the-gate-before-merging). |
| [`harness/manifests/change_manifest.schema.json`](../harness/manifests/change_manifest.schema.json) | Machine-readable contract for every behavioral change: predicted_fix, risk_cases, verdict structure. |
| [`harness/manifests/iter-N.json`](../harness/manifests/) | Per-iteration change manifests. As of this PRD, **iter-0 through iter-13 are closed and recorded** (`harness-iter-0` … `harness-iter-13`), **31 changes, 0 rollbacks**. iters 0–6 (May) built the reasoning/eval substrate; iters 7–13 (July) are the runtime-governance rollout (P-3/P-4/P-5), the persistence-integrity fixes (B3/B6), and the institutional-memory precedent governance — see §15.5. |
| [`DECISIONS.md`](DECISIONS.md) | Append-only verdict log per the runbook superseding-correction protocol. |
| [`ITERATIONS.md`](ITERATIONS.md) | Per-iteration narrative log in the format of the AHE paper's Appendix C. |
| [`RUNBOOK_iter*.md`](.) | Per-iteration runbook spec (lift the structure for any future iteration). |
| [`findings/`](findings/) | Mid-iteration investigation reports (e.g., `H2-memory-iteration-1.md` documenting the iter-3 chg-2 GC-021 regression and its wording-fix resolution). |

### §15.2 — Phases relevant to PACCA today

| Phase | Description | Status in PACCA |
|---|---|---|
| H1 | System prompt | Stable. Byte-identity check baselined at iter-1 chg-1; criterion-preservation tests added iter-3+. |
| H2 | Long-term memory (institutional memory layer) | Two distinct mechanisms. **(a)** Prompt-injected `long_term_memory.md`: 4 entries — NSCLC pembrolizumab (iter-3), RA biologic (iter-4), asthma dupilumab (iter-5), first deny-class entry (iter-6) — each carrying an explicit "Status: IN_REVIEW. (Not DENIED.)" anti-pattern routing. **(b)** RAG `case_precedents` (Medical Director overrides), fully wired and governed at iter-13 — see §15.5. The `DecisionSupportAgent` prompt registry is at **v2.7** (evidence-id citation, iter-10); `MedicalDirectorAgent` at v2.2. |
| H3 | Sub-agents | Not yet implemented. Roadmap item. |
| H4 | RAG / retrieval | Live in `src/pacca/integrations/vector_store.py`, **dual-collection** (`nccn_guidelines` authoritative + `case_precedents` institutional memory), and **governed by the P-4 scope guard** as of iter-13. Guideline corpus is synthesized for the test set; production-scale ingestion is roadmap. (The old single-collection `rag/pipeline.py` is dead code; the live path falls back past it.) |
| H5 | Evaluation harness | Live: per-case regression gate (iter-2 chg-2), LLM-as-judge (Claude Haiku), k=2 rollouts (iter-3 chg-3), aggregate ≥80% accuracy gate. **CI now also runs a real-Postgres integration guard** (§15.5) so persistence bugs SQLite masks fail the build. |
| H6 | Skills | Not yet implemented. AHE paper lists as future. |
| H7 | Middleware | **First middleware-pattern component live:** the P-4 minimum-necessary scope guard (`agents/scope_guard.py`), a fail-closed call-site wrapper wired in enforce mode across the submit path and the `/feedback` precedent write (iters 8–9, 13). A true framework middleware loader remains roadmap. |

### §15.3 — The PACCA-specific harness surfaces (beyond H1–H7)

PACCA extends the AHE framework with:

- **Escalation branches.** SS5.4 defines 7 escalation branches (branch_1 through branch_7) for the Decision Agent. Each branch has at least one validation case in the dataset (Dimension 1 of [`EVALUATION_COVERAGE.md`](EVALUATION_COVERAGE.md)).
- **Pre-flight detector.** `ClinicalRiskDetector` evaluates experimental-treatment, rare-condition, prior-denial, conflicting-guidelines, high-cost, and pediatric-complex triggers before the Decision Agent runs. Validated via the GC-006 through GC-012 case family.
- **Complexity-score model.** Integer 1–5 weighted heuristic (iter-5 chg-3) for the pediatric_complex check. Validated against GC-012, GC-023, GC-024, GC-025.
- **Hybrid structured-field + parser-fallback data path.** All clinical fields prefer the structured ClinicalCase model when populated; fall back to provider-notes parsing otherwise.
- **Prompt registry.** Versioned prompts under `src/pacca/agents/prompts/`; byte-identity check at every iteration boundary.
- **Audit schema.** Every decision emits a structured audit trail with prompt version, retrieval hits, gate verdicts, judge score.

### §15.4 — Discipline and audit defensibility

Three rules of engagement, enforced by the runbook + branch-and-PR workflow + per-commit hook protocol:

1. **One behavioral change per iteration's chg-N entry.** No "while I'm in here" drive-by edits.
2. **Every chg-N entry has a predicted_fix and risk_cases section in the manifest.** Authored *before* the change; verdict records observed_outcome *after* the change. This protects against post-hoc storytelling.
3. **Run the full gate before merging.** Fast suite + full clinical pipeline + per-case regression gate + (for agent/persistence changes) the real-Postgres integration guard. The suite is currently **743 collected tests** (683 unit + 28 clinical + 27 harness + 2 Postgres integration + 3 flow). As of iter-10 the clinical gate and manifest validation run in CI; branch protection now **requires** lint, tests, manifest validation, the clinical gate, and the Postgres integration guard.

Audit-trail defensibility comes from the combination of (a) append-only DECISIONS.md and ITERATIONS.md, (b) the per-iteration manifests under `harness/manifests/`, and (c) git's signed-tag history.

### §15.5 — The v2.6 governance-and-hardening arc (iterations 7–13)

The July iterations shifted the harness from *reasoning quality* (iters 0–6) to *runtime governance and persistence integrity* — the properties a regulated deployment is judged on. Each shipped as a one-file-diff with a recorded, verified verdict; **0 rollbacks**.

**Runtime governance layer (P-3 → P-5).** A three-gate contract now sits between the model's output and any automated decision, all deterministic (no model judges the model):

- **P-3 · IntentRecord (iter-7).** Every run declares a typed scope contract — allowed collections/actions, opaque subject reference, expected effects — emitted as the *first* audit event (`intent.declared`).
- **P-4 · Minimum-necessary scope guard (iters 8–9, extended iter-13).** A fail-closed wrapper expressing the HIPAA minimum-necessary standard in code: any DB write or RAG access outside the run's declared scope routes to human review (`SCOPE_VIOLATION`). Shipped warn-mode first, then promoted to enforce.
- **P-5 · Evidence-grounding detector (iter-10).** A decision citing an evidence id absent from the submission is forced to human review (`UNGROUNDED_EVIDENCE`) — the anti-hallucination gate promoted from test-time to runtime.

**Persistence integrity — two P0 fixes verified on real PostgreSQL.** Both were invisible to a green SQLite suite, which is itself the lesson:

- **B6 · Server-side `decision_id` (iter-11).** The identifier was model-generated into a `unique` column; a resubmitted case collided and 500'd, and a reused id could cross-link two decisions' audit trails. Now minted server-side, out of the tool schema entirely.
- **B3 · Deferrable audit FK (iter-12).** On Postgres the pre-flight audit rows referenced the parent request before it existed. Making the FK `DEFERRABLE INITIALLY DEFERRED` defers the check to commit — preserving both the audit-first ordering and referential integrity. Reproduced and fixed on Postgres 16.

**Real-Postgres CI guard.** Because SQLite does not enforce foreign keys or compile `JSONB`, an entire class of production bug was invisible to the suite. A `test-postgres` CI job now exercises the submit path against a migration-built Postgres 16 on every PR, and is a required check.

**Institutional-memory governance (iter-13).** The `case_precedents` learning loop — Medical Director overrides embedded and retrieved for similar future cases — was wired but ungoverned. iter-13 gave the precedent write a deterministic content id (closing a B6-class unstable-key bug), brought both RAG collections under the scope guard's real (not phantom) collection model, and put the `/feedback` write under an intent + scope-guarded, audit-before-write flow. The loop is proven end-to-end: a case the guidelines alone send to human review becomes an auto-approval once a matching precedent exists, with the rationale citing it.

---

## §16 — Clinical Validation Strategy

This section answers the question a HIPAA / SaMD reviewer or a payer-customer due-diligence team will ask: **"Is your evaluation dataset adequate to defend the claims you make about your system?"**

The short answer at the 105-case state: **PACCA's evaluation set has crossed the 100-case production-pilot threshold. It is portfolio-credible, production-pilot-capable on narrow scope, and still explicitly not yet HIPAA SaMD-grade. The roadmap to SaMD-grade is defined in §16.8, with effort estimates and case-count thresholds.**

What crossing 100 unlocked, concretely: ≥10 pp aggregate-drop detection at 80% power (§16.4), a sufficient DENY-class sample (now 7 cases, target ≥5), and the **formation** of the Phase 2 clinical-review board (§16.7). What it did not unlock: 5 pp erosion detection, prevalence-weighted representativeness, demographic-equity testing, or the SaMD-grade claim — all of which remain gated on the 300- and 500-case milestones.

### §16.1 — Validation framework alignment (FDA SaMD + IMDRF)

PACCA's validation framework aligns to:

| Authority | Framework | PACCA's evidence document |
|---|---|---|
| FDA | *Software as a Medical Device (SaMD): Clinical Evaluation* (Dec 2017) | [`STATISTICAL_POWER.md`](STATISTICAL_POWER.md) § "FDA SaMD-grade claim alignment" |
| IMDRF | SaMD WG/N41 (2017), *Software as a Medical Device (SaMD): Clinical Evaluation* | [`STATISTICAL_POWER.md`](STATISTICAL_POWER.md) (international counterpart) |
| FDA | 21 CFR 820.30 (Design Controls) | Implicit through HARNESS.md's change-manifest + iteration-log discipline |
| HIPAA | 45 CFR 164 (Security + Privacy Rules) | [`HIPAA_COMPLIANCE.md`](HIPAA_COMPLIANCE.md) + CLAUDE.md "Never log PHI" + synthetic-only fixtures policy |
| ONC | 45 CFR 170 (Health IT certification) | Not currently pursued; relevant if PACCA enters EHR-integrated certification path |
| AMA / Specialty bodies | Specialty practice guidelines (NCCN, ACR, AAD, GINA, etc.) | [`CASE_AUTHORING_GUIDE.md`](CASE_AUTHORING_GUIDE.md) § 5 "The guideline-citation rule" |

The three-pillar SaMD framework breaks down as:

| Pillar | What it requires | PACCA's status at 105 cases |
|---|---|---|
| **Analytical validation — correctness** | The SaMD performs as intended on representative inputs | 105 cases across 7 escalation branches × multiple specialties; live gate 20/20 = 100% on the golden core over three iterations |
| **Analytical validation — robustness** | The SaMD handles adversarial / failure-mode inputs | Named failure-mode cases: hallucination traps (GC-018, GC-019), memory traps (GC-021, GC-022), patient-preference traps (GC-005, GC-026), workup-hierarchy violations (GC-027), controlled-substance + SUD (GC-029), age-only-escalation (GC-028 must not fire), rare-prefix over-escalation (GC-030 must not fire), and the expansion-suite families added iter-6 and after |
| **Analytical validation — repeatability** | The SaMD produces consistent outputs on the same input | k=2 rollouts implemented (iter-3 chg-3); k=4 recommended for SaMD-grade; LLM-as-judge variance characterized |
| **Clinical validation — agreement with experts** | The SaMD's outputs agree with clinical SME judgments | Phase 1 per-case SME concurrence in-house (§16.7); Phase 2 clinical-review board **in formation** (§16.7) — the 100-case threshold that triggers board formation has been crossed, but no board sweep has run |
| **Clinical performance — post-market** | Continuous performance evaluation in production | Pre-deployment. The architecture's audit trail and the per-case gate enable post-market instrumentation; the deployment hasn't happened. |

### §16.2 — Current evaluation surface

PACCA evaluates against a hand-crafted golden-case dataset of **105 unique cases (GC-001–GC-105)**, verified by unique-ID count across `tests/clinical/`. The dataset has grown from the 33-case v2.4 state through the iter-6-and-after specialty- and depth-expansion suites (cardiology, oncology breadth and depth, pulmonology, OB, mental health, neurology, hematology, transplant, geriatric, endocrinology, adult-complexity, ambiguous-completeness, and depth-extension families).

Verified quantitative summary at the 105-case state:

| Property | Value | Note |
|---|---|---|
| Unique cases | 105 (GC-001–GC-105) | `grep -roE 'GC-[0-9]{3}' tests/clinical/ \| sort -u \| wc -l` |
| DENY-class cases | 7 | `expected_outcome=ExpectedOutcome.DENIED`; target ≥5 **met** (was 2 at v2.4) |
| Pre-flight-escalate cases | 5 | `expected_outcome=ExpectedOutcome.PRE_FLIGHT_ESCALATE` |
| Approve / in-review | majority | AUTO_APPROVED + IN_REVIEW across routine and boundary cases |

> **Authoritative per-case composition** — including the file-by-file partition, the per-case rationale, and the failure-mode taxonomy — lives in [`CASE_PROVENANCE.md`](CASE_PROVENANCE.md). This PRD deliberately does not duplicate the per-case table; the canonical doc is the single source of truth for the GC-001–GC-105 breakdown, and a per-case grep of `tests/clinical/` is the ground truth if the two ever disagree.

Evaluation runs through two gates:
1. **Per-case regression gate** ([`regression_gate.py`](../tests/clinical/regression_gate.py)) — iter-2 chg-2. Fires on any single-case score drop > noise_threshold relative to baseline. 100% per-case sensitivity regardless of dataset size.
2. **Aggregate ≥80% accuracy gate** ([`evaluator.py`](../tests/clinical/evaluator.py)) — the LLM-as-judge scores each case 1–5; the dataset-wide pass rate must be ≥ 80%.

The two compose: per-case dominates *detection sensitivity*; aggregate provides the single summary statistic for customer-facing reports and the SaMD claim.

### §16.3 — Dataset sufficiency — current state vs. SaMD-grade target

Per [`DATASET_SUFFICIENCY.md`](DATASET_SUFFICIENCY.md):

| Claim level | Cases required | PACCA today (105) | Gap |
|---|---|---|---|
| Coverage floor (every gate has ≥ 1 case on each side) | ~50 | 105 | **met** |
| 20pp aggregate drop detection (smoke test) | 15–20 | 105 | **met** |
| 10pp aggregate drop detection (clinically meaningful) | 75–100 | 105 | **met** (new at v2.5) |
| Per-case detection on every reasoning class (3-5 per family × ~30 families) | 100–150 | 105 | approaching — at the low end of the band |
| 5pp aggregate drop detection (subtle erosion) | 200–300 | 105 | -95 to -195 |
| Production-deployment representative case mix | 300–500 | 105 | -195 to -395 |
| HIPAA / SaMD-grade clinical validation | 500+ in-house + 100/quarter clinical-review-board sample | 105 | -395 + the review-board process |

### §16.4 — Statistical-power evidence

Per [`STATISTICAL_POWER.md`](STATISTICAL_POWER.md), the binomial-CI math:

```
n ≈ ( z_α · √(p₀(1-p₀)) + z_β · √(p₁(1-p₁)) )² / (p₀-p₁)²
```

Sample sizes for PACCA's evaluation regime (baseline p₀=0.95, α=0.05, power 0.80):

| Δ (drop to detect) | n (formula) | Operational target | Met at 105? |
|---|---|---|---|
| 20 pp | 13 | 20 | ✅ |
| 15 pp | 23 | 30 | ✅ |
| 10 pp | 43 | 100 | ✅ (new at v2.5) |
| 7 pp | 75 | 150 | ❌ |
| 5 pp | 150 | 200–300 | ❌ |
| 3 pp | 390 | 500 | ❌ |
| 2 pp | 870 | 1,000+ | ❌ |

At 105 cases the aggregate gate now detects a ≥10 pp performance drop at 80% power — the "clinically meaningful" detection band. The per-case gate excels at sharp, single-case regressions; the aggregate gate excels at slow, distributed erosion. They are complementary. See `STATISTICAL_POWER.md` § "Sensitivity analysis" for the cross-table.

### §16.5 — Per-case provenance and audit trail

Per [`CASE_PROVENANCE.md`](CASE_PROVENANCE.md), each case in the dataset answers four questions:

- Case ID + file
- Clinical rationale (≤ 2 sentences)
- Named failure mode it probes (or "coverage" for routine cases)
- Iteration of origin

The provenance table is the audit-defense artifact for the question "why does this case exist?" A reviewer can scan it without reading 105 case definitions.

> **Sync note (v2.5):** the provenance table's authoritative rows through GC-033 are current; rows for GC-034–GC-105 (the iter-6-and-after expansion families) are being backfilled from the case-definition files and their originating manifests. Until that backfill lands, `tests/clinical/*.py` and the per-iteration manifests under `harness/manifests/` are the ground truth for the newer cases' provenance.

Failure-mode taxonomy (established and extensible):
- Coverage
- Hallucination zero-tolerance
- False pattern-matching (memory trap)
- Step-therapy enforcement
- Cross-condition memory bleed
- Test-data adequacy
- Discriminator (negative / ambiguous / positive) class
- Branch-N pre-flight
- Confidence-N boundary
- Policy-trigger override (high-cost / pediatric-complex / age-only / rare-prefix)

### §16.6 — Coverage matrix and gap analysis

Per [`EVALUATION_COVERAGE.md`](EVALUATION_COVERAGE.md), the per-dimension picture at the 105-case state:

| Dimension | Defensible claim today | Remaining gap |
|---|---|---|
| Outcome class | All 5 outcomes represented; DENY now at 7 cases (target ≥5 met) | Prevalence-weighting still convenience-sampled, not payer-mix-weighted |
| Escalation branch | All 7 branches plus NONE | 3+ per branch achieved for the core branches; thin branches flagged in the canonical doc |
| Specialty | 14+ specialty families covered, several now at 3+ cases | 5+ per specialty for within-specialty signal at deployment scale |
| Age bracket | Pediatric, adolescent, adult, older adult, 80+ all represented | 3+ per stratum across all specialties |
| Documentation completeness | Complete, ambiguous, sparse + hallucination all represented (ambiguous-completeness family added) | Graded sparseness across more specialties |
| Cost tier | Under-threshold, over-threshold, and boundary cases | Deeper at-threshold coverage |
| Demographics | Gender/age stated in notes | Structured demographic fields needed for equity claims |
| Comorbidity | 0, 1, 2+ loads represented | Graded coverage for parser robustness |
| Failure mode | Every documented mode has ≥ 1 case | Per-mode statistical signal still thin where 1–2 cases each |
| AHE harness component | H1, H2, H4, H5 covered; PACCA-specific extensions covered | H3 (sub-agent), H6 (skill), H7 (middleware) not yet implemented |

> **Sync note (v2.5):** the exact per-specialty and per-branch case counts at 105 are maintained in [`EVALUATION_COVERAGE.md`](EVALUATION_COVERAGE.md); this table summarizes the qualitative coverage claims that hold at 105 and defers precise per-cell counts to the canonical doc.

### §16.7 — Clinical-SME review process (Phase 1 + Phase 2, two-stage CRB)

Per [`CASE_AUTHORING_GUIDE.md`](CASE_AUTHORING_GUIDE.md) §§ 11–12 and [`CRB_SOURCING.md`](CRB_SOURCING.md):

**Phase 1 (per-case, pre-merge, in-house):**

Every new case requires SME concurrence on:
1. Clinical accuracy of the synthetic notes
2. Correctness of the cited guideline body and what it says
3. Correctness of expected_outcome + expected_branch
4. Appropriateness of clinical_rationale

The SME records concurrence in the PR via a "clinical-review: approved — Dr. X, MD, board-certified Y" comment. If the author is the SME, they self-attest in the PR description. In the absence of a credentialed SME for a specialty, the case lands `provisional` and gets the next Phase 2 sweep.

**Phase 2 (clinical-review board) — two-stage activation:**

The Phase 2 clinical-review board activates in two stages, to separate *standing up the board* from *running scored sweeps*:

| Stage | Trigger | What happens | Status |
|---|---|---|---|
| **Formation** | Dataset crosses **100 cases** | Recruit and charter 2–3 credentialed clinicians across major specialties; agree the stratified-sampling protocol and the Cohen's κ target (≥ 0.80 per Landis & Koch 1977). | **In progress.** The 100-case threshold has been crossed (105 as-built). Board members are being convened through a partnership with a Chicago-based charitable foundation (the "Foundation Partner"; identity withheld under NDA). The near-term case-authoring priority tied to this partnership is a **pediatric-specialized golden-case expansion**. |
| **Operational (scored sweeps)** | Dataset reaches **200 cases** | The board scores a random 10% stratified sample each quarter; inter-rater agreement is reported as Cohen's κ; cases where the board disagrees with the cataloged `expected_outcome` are flagged `under-revision` and corrected in the next iteration. Board reports land in `docs/findings/clinical-review-board-<date>.md`. | **Not yet active.** No board sweep has run; no board report exists. This gates the SaMD-grade clinical-validation claim at 500. |

**Honest status:** the board is *forming*, not *operating*. No sweep has run and no `docs/findings/clinical-review-board-*.md` report exists yet. The v2.5 milestone is the *formation trigger* (100 cases, crossed), not the *operational trigger* (200 cases). Any external claim about PACCA's clinical validation must state the board as "in formation," not "operational," until the first scored sweep and its report exist.

### §16.8 — Roadmap: production-pilot (100, reached) → deployment (300) → SaMD-grade (500+)

| Milestone | Cases | Effort (1 FTE + 0.25 FTE clinical SME) | What it buys | Status |
|---|---|---|---|---|
| **Portfolio-credible** | 33 | Done (v2.4) | Coverage of every gate on both sides except then-thin DENY; first-case specialty coverage. | ✅ Reached (v2.4) |
| **Production-pilot** | 100 | ~6–8 weeks | 10pp aggregate-drop detection; ≥5 DENY cases; 3+ cases per major specialty; ambiguous-tier coverage; CRB formation trigger. Defensible for a single-payer pilot in a narrow specialty. | ✅ **Reached (105 as-built, v2.5)** |
| **General payer deployment** | 300 | ~6–8 months | 5pp aggregate-drop detection; per-specialty per-class regression signal; prevalence-weighted distribution; demographic balance enabling equity claims; CRB operational (scored quarterly sweeps from 200). Defensible for broad-deployment-with-clinical-SME-in-the-loop. | ⏳ Next |
| **HIPAA SaMD-grade** | 500+ in-house + 100/quarter CRB sample | ~12–18 months | 3pp aggregate-drop detection; full per-specialty stratification; ongoing inter-rater κ; post-market surveillance. The bar for "this AI can make clinical recommendations that influence patient care under HIPAA SaMD." | ⏳ Gated on the above |

**Honest framing.** Each milestone is a discrete unlock of new claim-types, not continuous improvement with no discrete meaning. Crossing 100 (now 105) genuinely made PACCA more defensible than the 33-case state — it added 10 pp detection and a sufficient DENY sample and it triggered board formation. The next 195 cases to reach 300 unlock claims the 105-case dataset cannot support, and CRB scored sweeps begin at 200 along the way.

**Recommended priority order** for the push toward 300 is in [`DATASET_GROWTH_ROADMAP.md`](DATASET_GROWTH_ROADMAP.md). The near-term authoring priority tied to the CRB-formation partnership is the pediatric-specialized golden-case expansion noted in §16.7.

### §16.9 — Honest assessment of claims PACCA can defend today

| Claim | Defensible at 105? | Evidence |
|---|---|---|
| "PACCA tests every policy gate with at least one case on each side" | Yes. DENY now at 7 cases — the 0-DENY gap that was the most embarrassing pre-iter-6 gap is closed and past the ≥5 sufficiency target. | EVALUATION_COVERAGE.md Dim 1; §16.2 |
| "PACCA covers all 7 escalation branches" | Yes. | EVALUATION_COVERAGE.md Dim 1 |
| "PACCA tests across 14+ specialty areas" | Yes, several now at 3+ cases each. | EVALUATION_COVERAGE.md Dim 2 |
| "PACCA has named adversarial probes for every documented failure mode" | Yes. | EVALUATION_COVERAGE.md Dim 7 + CASE_PROVENANCE.md |
| "PACCA's per-case regression gate has 100% sensitivity on any single-case score drop ≥ 2" | Yes — mathematical guarantee, dataset-size-independent. | STATISTICAL_POWER.md § "Per-case regression-detection math" |
| "PACCA's aggregate gate catches ≥20pp drops with 80% power" | Yes — n=105 well exceeds the n=15 minimum. | STATISTICAL_POWER.md § "Sample sizes" |
| "PACCA's aggregate gate catches ≥10pp drops with 80% power" | **Yes — new at v2.5.** n=105 meets the n=75–100 threshold. | STATISTICAL_POWER.md; §16.4 |
| "PACCA's aggregate gate catches ≥5pp drops (silent erosion)" | NO — n=105 is below the n=200–300 threshold. | STATISTICAL_POWER.md |
| "PACCA's dataset is prevalence-weighted to a major payer's case mix" | NO — current distribution is convenience-sampled across observed gaps, not prevalence-weighted. | EVALUATION_COVERAGE.md gaps |
| "PACCA's dataset enables demographic equity testing" | NO — gender, ethnicity, insurance type are not structured fields on GoldenCase. | EVALUATION_COVERAGE.md Dim 5 |
| "PACCA's Phase 2 clinical-review board is operational" | NO — the board is **in formation** (100-case formation trigger crossed); scored sweeps begin at 200; no sweep has run and no board report exists. | §16.7 |
| "PACCA has SaMD-grade clinical validation" | NO — current state is 105/500+; CRB not yet operational. | This entire §16 |
| "PACCA has the *path* to SaMD-grade clinical validation defined" | YES — §16.8 roadmap, with effort estimates and per-milestone claim unlocks. | This document |

This honest-claim matrix is the answer to the SaMD reviewer's first question: "What can you defend?" The answer is the rows marked Yes; the rows marked NO are the explicit roadmap and the budget-justification for the next 12–18 months of dataset-growth work.

---

## §17 — HIPAA Compliance Cross-Reference

The clinical-validation dataset and process are governed by:

| Requirement | Policy | Document |
|---|---|---|
| Never log PHI | CLAUDE.md (project file) + structlog redaction in `src/pacca/config/tracing.py` | [`HIPAA_COMPLIANCE.md`](HIPAA_COMPLIANCE.md) |
| Synthetic data only in fixtures | CLAUDE.md + CASE_AUTHORING_GUIDE.md § 4 | [`CASE_AUTHORING_GUIDE.md`](CASE_AUTHORING_GUIDE.md) |
| Auth + input validation on every endpoint | CLAUDE.md | `HIPAA_COMPLIANCE.md` + `tests/test_api_security.py` |
| Secrets in env only | CLAUDE.md | `HIPAA_COMPLIANCE.md` + `.env.example` pattern |
| BAA-relevant logging policy | per-deployment | (operator's responsibility) |

The clinical-validation work itself does not introduce any new PHI surface — all 105 cases are synthetic per the authoring guide's § 4. The Phase 2 clinical-review board, when it activates scored sweeps at 200, will need a separate BAA + access-control protocol if the board reviews any real patient data (the recommended setup is for the board to review synthetic cases only, eliminating the BAA question). BAA inventory and the board's data-access posture are tracked in [`BAA_INVENTORY.md`](BAA_INVENTORY.md).

---

## §18 — Change Log

| Version | Date | Change |
|---|---|---|
| v2.2 | (predecessor) | §1–§14 narrative content; see `PACCA_PRD_Consolidated.md` |
| v2.3 stub | 2026-05-25 (initial) | Stub pointing at HARNESS.md + manifests so README links didn't 404 |
| v2.4 | 2026-05-25 | §15 (summarized harness cycle) + §16 (Clinical Validation Strategy — substantive new chapter) + §17 (HIPAA cross-reference) + §18 (change log). Drafted during iter-6 dataset-expansion work at the 33-case state. |
| v2.5 | 2026-07-20 | Records the dataset crossing the 100-case production-pilot threshold (105 as-built, GC-001–GC-105). Refreshed §16.2–§16.9 to that state; reworked §16.7 into a two-stage CRB model (formation at 100 via the Foundation Partner; operational scored κ-sweeps at 200). Reconciled the v2.5 Google-Doc draft against the repo. |
| **v2.6** | **2026-07-24 (this PRD)** | **Hardening & governance consolidation — a minor (backward-compatible) release; dataset unchanged at 105, so §16 carries forward. Rewrote §15 to record iterations 7–13: the P-3/P-4/P-5 runtime governance layer (IntentRecord, minimum-necessary scope guard, evidence-grounding detector); two P0 persistence-integrity fixes verified on real PostgreSQL (B6 server-side `decision_id`, B3 deferrable audit FK); a required real-Postgres CI guard; and the institutional-memory `case_precedents` loop wired and brought fully under the scope guard (iter-13). Updated §15.1 (iter-0…iter-13, 31 changes, 0 rollbacks), §15.2 (registry v2.7; dual-collection governed RAG; first middleware-pattern component), §15.4 (743 tests; required-check set), and added §15.5. No new clinical claim.** |

---

*This document is part of the PACCA v2.6 cycle documentation set. github.com/drdgreed/pacca | David Reed, PhD | Last updated: 2026-07-24 (iterations 0–13 closed; governance-and-hardening arc recorded in §15.5).*
