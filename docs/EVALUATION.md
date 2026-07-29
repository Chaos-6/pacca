# PACCA — Evaluation Methodology

> **Status:** Draft. The current evaluation surface is real and runnable; the consolidated narrative below points to the canonical sources rather than re-stating them. A full evaluation document will land with Phase H5 (Evaluation Harness Expansion).

## What is evaluated today

| Layer | Where it lives | What it checks |
|---|---|---|
| **Unit suite** | [`tests/unit/`](../tests/unit) | 120 tests, all green. Covers escalation tree, models, retry/tracing, audit trail, prompt engineering, security/scalability, config API. Runs in ~7 seconds. |
| **Integration suite** | [`tests/integration/`](../tests/integration) | Cross-component flows including the level-5 maturity flow (`tests/test_level5_flow.py`). |
| **Clinical accuracy / LLM-as-judge** | [`tests/clinical/`](../tests/clinical) | 20-case clinical golden dataset scored 1–5 by Claude Haiku as judge. CI gate at ≥80% accuracy. A judge-detected fabrication scores automatic 1 — no acceptable rate of inventing clinical data. See "Three counters, not one" below for what changed here at chg-25. |
| **Fabrication zero-tolerance** | `GC-018`, `GC-019` (`test_zero_fabrications_on_sparse_cases`) | Sparse-notes traps that fail the build on any score-1 fabrication. Renamed from `test_zero_hallucinations_on_sparse_cases` at chg-25; the assertion is unchanged. |
| **Schema validation** | Inline `jsonschema.validate(...)` against [`change_manifest.schema.json`](../harness/manifests/change_manifest.schema.json) | Every change manifest under `harness/manifests/` is validated before merge. A dedicated `pacca.harness.validate_manifest` CLI is a planned H5 deliverable; today the validation runs inline (see "Reproducing today's evaluation" below). | <!-- drift-guard: ignore -->

## Three counters, not one: separating fabrication from keyword violations (chg-25)

**The defect (2026-07-28).** Before chg-25, the evaluation harness's headline
anti-hallucination metric — `EvaluationReport.hallucinations` — was a single
LLM judge's self-reported boolean, produced from a judge prompt that showed
the judge each case's `reasoning_must_not_include` keywords under the header
"Keywords that must NOT appear (hallucination markers)"
(`tests/clinical/evaluator.py:361–362`, pre-chg-25). That header taught the
judge to conflate two categorically different failure modes: (1) a forbidden
keyword appearing in the rationale — pure string containment, which a
function answers exactly, every time — and (2) the rationale fabricating a
clinical fact not present in the submission, which genuinely requires
judgment.

**The observed instability.** GC-028 (an 82-year-old cardiology case whose
`reasoning_must_not_include` is `["age", "elderly"]`, specifically designed
to probe whether the system inappropriately escalates on age alone) produced
the *same* violation and the *same* decision across two adjacent clinical-gate
runs — `AUTO_APPROVED` both times, all required CMS NCD 20.4 criteria cited,
the rationale discussing the patient's real, submitted age only to explain
that it does *not* exclude treatment:

| Run | Score | Judge's word | `hallucinations` count | `correct_outcome` (judge self-report) |
|---|---|---|---|---|
| iter-16 | 2 | "a critical **anti-pattern**" | 0 | `True` |
| iter-17 | 1 | "a critical **hallucination**" | 1 | `False` |

The system did not change between these runs. Both metrics moved anyway —
and the second judge's own reasoning names the mechanism: it quotes the
prompt's own header back ("the case instructions explicitly flag 'age' and
'elderly' as **hallucination markers**") as its justification for calling a
keyword hit a hallucination. `correct_outcome` moved too, for the same
underlying reason: it was also a self-reported judge boolean, this time for a
question ("did the decision status match the expected outcome?") that a
one-line string comparison answers exactly. Aggregate accuracy stayed
identical both runs (87.2%, 34/39, same five failed case IDs) — only the
safety-relevant numbers moved, silently, with zero system change.

**The fix — three tiers, by what each check actually is** (the full design
rationale lives in the chg-25 design spec; this section states the outcome):

1. **Tier 1 (deterministic, exact, zero variance).** `reasoning_must_include`
   / `reasoning_must_not_include` are checked by
   `check_reasoning_constraints()` in plain Python. The judge is no longer
   shown these lists **at all** — it cannot mislabel what it is never shown.
   Reported as `EvaluationReport.constraint_violations`: a list of
   `(case_id, missing, forbidden_present)`.
2. **Tier 2 (deterministic, exact).** Outcome correctness
   (`outcome_matches_expected()`) and evidence grounding
   (`check_evidence_grounding()`, a thin wrapper reusing the production
   runtime detector `agents/evidence_grounding.py::unresolved_cited_evidence`
   so the eval harness and the runtime detector can never silently drift
   apart) are computed in Python, never asked of the judge. Reported as
   `EvaluationReport.outcome_mismatches` and `.ungrounded_citations`.
3. **Tier 3 (judgment, genuinely irreducible).** The 1–5 reasoning-quality
   score, and `fabrication_detected` under a narrow definition (a rationale
   asserting a clinical fact — a value, a prior therapy, a finding — that
   does not appear in the submission), with an explicit counter-example in
   the prompt: discussing a fact that *is* in the notes (e.g. the patient's
   age) is never fabrication, even if a keyword constraint elsewhere flags it
   as an unwanted topic. Reported as `EvaluationReport.fabrications`.

**`EvaluationReport.hallucinations` is removed, not aliased.** A field whose
meaning changed must not keep its old name — code that still reads
`report.hallucinations` now fails loudly (`AttributeError`) instead of
silently reading a renamed-but-reused field with different semantics. A
caller that wants "one number" must now choose which one it means:

```
Fabrications (judge):            N cases   [GC-...]
Constraint violations (exact):   N cases   [GC-...]
Ungrounded citations (exact):    N cases   [GC-...]
```

**GC-018/GC-019 still gate, unchanged.** `test_zero_fabrications_on_sparse_
cases` (renamed from `test_zero_hallucinations_on_sparse_cases`) asserts the
identical zero-tolerance invariant on the identical two sparse-notes traps —
only the field it reads (`fabrication_detected`, under the narrower
definition) changed.

**Judge trustworthiness is now measured, not assumed.** Separating the
counters fixes the conflation; it does not say how far the remaining
judgment call (score + `fabrication_detected`) can be trusted. A judge
stability harness (`tests/clinical/judge_stability.py`,
`make test-judge-stability`) scores the same (case, decision, rationale)
tuple N times and reports the **disagreement rate** (any score differs
across runs) and, more importantly, the **band-crossing rate** — the
fraction of cases where runs land on both sides of `MINIMUM_PASSING_SCORE`,
which is where judge noise becomes a pass/fail flip rather than harmless
jitter. This is a report, not a gate (same "warn before enforce" posture as
the held-out accuracy report above), and its own regression test is fully
stubbed — it has not yet been run against the live judge to obtain a real
noise measurement; that is a deliberate follow-up, not a claim made here.

**Provenance.** `JudgeVerdict` and `EvaluationReport` now carry `judge_model`
and `judge_prompt_version` (bumped to `v2.0` at chg-25) alongside the existing
`raw_response`, so a disputed score is re-examinable months later against the
exact prompt version that produced it.

**Self-preference posture, stated on purpose.** The judge
(`claude-haiku-4-5-20251001`) and the generator (PACCA's DecisionAgent runs
`claude-sonnet-4-5-20250929`) are deliberately different models — a real
mitigation for same-model self-enhancement bias in LLM-as-judge setups. This
was true by accident of a cost-driven model choice before chg-25; it is
recorded here as a decision made on purpose going forward, with the caveat
that a weaker judge grading a stronger generator has its own ceiling (it can
fail to recognize reasoning sophistication it cannot itself produce).

## Train-on-test contamination and the held-out split

**The finding (2026-07-27).** The live clinical gate's "20/20 = 100%" headline is
partially in-sample: `src/pacca/agents/decision_support/long_term_memory.md` — part
of the DecisionAgent's system prompt — names specific golden cases verbatim:

```
long_term_memory.md:159   "GC-010 and any future high-cost biologic case"
long_term_memory.md:231   "this is exactly the GC-012 case"
long_term_memory.md:251   GC-035 ...
```

GC-010 and GC-012 are inside the 20-case gate the accuracy number is computed
over. A prompt authored while looking at a case's expected answer is not being
generalization-tested by that case when it's re-run — the "100%" reflects some
memorization, not purely reasoning ability.

**The fix — a declared holdout.** The dataset is 105 cases (`GC-001`..`GC-105`,
no gaps) across `tests/clinical/*_cases.py`, large enough to carve out a real
holdout. `tests/clinical/holdout.py` declares `HELD_OUT_CASE_IDS` — 32 cases that
are (a) never referenced anywhere under `src/pacca/` (no prompt, memory entry, or
detector rule names them) and (b) not part of the five case lists the live
accuracy gate (`TestFullClinicalEvaluation.test_full_pipeline_meets_accuracy_threshold`)
runs today (`GOLDEN_CASES`, `NEAR_MISS_CASES`, `PEDIATRIC_CASES`,
`EXPANSION_CASES`, `ADULT_COMPLEXITY_CASES`). Everything else is
`IN_SAMPLE_CASE_IDS`. See the module docstring in `tests/clinical/holdout.py`
for the exact selection method and the stratification achieved (one case per
`expected_branch` / `ExpectedOutcome` combination present in the eligible pool).

The set is **declared, not derived** — a hand-picked `frozenset`, not "every id
currently unreferenced." A derived holdout would silently shrink every time
someone's prompt edit happened to name a new case; declaring it turns
contamination into a loud, named test failure instead of an invisible shrink.

**Enforcement.** `tests/unit/test_eval_holdout_guard.py` runs as part of the
standard (`make test`) suite and asserts:
1. No held-out case id appears anywhere under `src/pacca/**/*.py` or `**/*.md`.
2. `HELD_OUT_CASE_IDS ∪ IN_SAMPLE_CASE_IDS` equals the full 105-case dataset,
   disjointly (no case lost or double-counted).
3. `len(HELD_OUT_CASE_IDS) >= 20` — the operational target in
   [`STATISTICAL_POWER.md`](STATISTICAL_POWER.md)'s 20-percentage-point-drop row.
4. Every declared held-out id actually exists in the dataset (typo guard).
5. GC-010 and GC-012 specifically remain in `IN_SAMPLE_CASE_IDS` — pinning the
   finding above so it can't silently regress.

**The rule going forward:** no held-out case may ever be referenced in a
prompt, a long-term-memory entry, a detector rule, or added to a case list the
live accuracy gate runs. Doing so is exactly the contamination this mechanism
exists to prevent, and guard test #1 will fail the build, by design.

**What this does and doesn't change.** `tests/clinical/evaluator.py`'s
`EvaluationReport` gained `accuracy_in_sample` / `accuracy_held_out` (plus
per-subset counts), computed by the pure function `split_verdicts_by_holdout()`
over whatever verdicts a run produces. This is reporting only: the existing
`accuracy` and `passed_ci_gate` fields — and the 80% CI-gate threshold — are
still computed over every verdict exactly as before, byte-for-byte.

**The held-out cases are now actually evaluated.** A dedicated
`@pytest.mark.holdout` test, `test_held_out_accuracy_report`
(`tests/clinical/test_clinical_accuracy.py::TestHeldOutEvaluation`),
runs the real pipeline (pre-flight → Decision Agent → LLM judge — the same
`_run_cases_through_pipeline()` helper the golden-set gate uses, so the two
paths can't silently drift apart) over `held_out_cases()` and reports
`accuracy_held_out`. Run it with:

```bash
make test-holdout    # requires ANTHROPIC_API_KEY; not a CI gate; ~7.5 minutes
```

### Why `holdout` is its own marker, not `clinical`

It began under `@pytest.mark.clinical`, which put it inside the per-merge gate.
It was moved out on 2026-07-28 for three reasons, in ascending importance:

1. **It is not a gate.** Its only assertion is a wiring check, so it could
   never block a merge — it was pure cost on every qualifying PR.
2. **It is the wrong instrument for regression detection.** That job belongs to
   per-case baseline comparison (`regression_gate.py`). Aggregate accuracy over
   32 cases has poor power for small drops — per `STATISTICAL_POWER.md`, n≈13
   detects a *20-point* drop.
3. **Running a holdout per-merge destroys it.** Every behavioural PR would print
   exactly which held-out cases failed, and the natural, well-intentioned
   response is to tune a prompt until they pass. That is contamination — the
   precise thing this holdout exists to measure — arriving through the one
   channel `tests/unit/test_eval_holdout_guard.py` structurally cannot see: a
   human reading CI output. A holdout is worth exactly what its isolation is
   worth.

It now runs on the nightly `holdout-report` CI job (`continue-on-error`, never
blocking) or deliberately via `make test-holdout`.

> **Invariant:** every `-m "not clinical"` selector must also say
> `not holdout`. Verified 2026-07-28 — a bare `-m "not clinical"` *does* select
> this test, which would silently turn `make test` into a billable 32-case live
> run. The Makefile and `ci.yml` selectors were widened accordingly.

### First recorded reading (2026-07-28)

**87.5% (28/32)**, Wilson 95% CI **71.9%–95.0%**. Failures: GC-037, GC-044,
GC-048, GC-065. Score distribution 27×5, 1×3, 4×2, **zero score-1** — the
anti-hallucination property holds out-of-sample, not only on GC-018/019.

Two cautions on reading it. First, **it is not directly comparable to the
golden-set figure**: the 39-case gate includes 5 pre-flight cases (GC-006, 007,
008, 009, 011) decided deterministically with no model call, and the holdout has
none — the honest comparison is holdout-32 against the gate's LLM-decided-34.
Second, at n=32 the interval is wide enough that 87.5% is not statistically
distinguishable from ~95%; treat this as the first data point establishing a
baseline, not a verdict. **GC-065 is the one to look at first** — it was wrongly
AUTO_APPROVED, the only failure in the unsafe direction; the other three
over-escalated to IN_REVIEW.

Two different key-absent behaviors, both worth stating precisely so neither reads
as the other. Run the test **directly via pytest** with no key set
(`pytest tests/ -m holdout`) and it **skips cleanly** (`1 skipped`) — this is
what keeps `make test` (the fast, deterministic target, which selects
`-m "not clinical and not holdout"` and never reaches this test at all) fast and
green. Run
it via **`make test-holdout`** with no key set, and the *target* **hard-errors
instead** (`ERROR: ANTHROPIC_API_KEY is not set...`, non-zero exit) before
pytest ever starts, matching `make test-clinical`'s existing precedent: a
target whose entire purpose is producing a number fails loud rather than
silently reporting nothing.

`tests/unit/test_held_out_pipeline_wiring.py` is the fast, fully-mocked
regression proof that the test genuinely iterates all 32 declared ids and
produces a non-empty held-out denominator — it runs in `make test`, with
zero API calls, and would fail if a future refactor broke the wiring.

**Deliberately NOT a blocking gate — yet.** `test_held_out_accuracy_report`
asserts only that all 32 cases were evaluated (a wiring sanity check, not an
accuracy threshold). There is no accuracy floor asserted on
`accuracy_held_out`, because there is no established out-of-sample baseline
to gate against on this test's first-ever run — picking a threshold now would
be a unilateral policy call, not an engineering one. This follows the repo's
own **warn-then-enforce precedent**: the P-4 scope guard
(`src/pacca/agents/scope_guard.py`) shipped in warn mode at iter-8 before
enforce mode at iter-9 (see `docs/DECISIONS.md`). Promoting
`accuracy_held_out` to a blocking threshold — and choosing that threshold —
is a deliberate follow-up decision once a baseline exists, not taken in this
change.

**Structural gap: branches 4–7 are unmeasurable out-of-sample.**
`BRANCH_4_EXPERIMENTAL`, `BRANCH_5_RARE`, `BRANCH_6_CONFLICTING`,
`BRANCH_7_PRIOR_DENIAL`, and `ExpectedOutcome.PRE_FLIGHT_ESCALATE` exist ONLY
inside the contaminated golden-20 — GC-006 and GC-009 (branch 4), GC-007
(branch 5), GC-011 (branch 6), GC-008 (branch 7). Every case that exercises
PACCA's deterministic pre-flight safety layer is inside the already-excluded
gate, so **no re-slicing of the existing 105-case dataset can produce an
out-of-sample measurement for it** — only authoring new pre-flight cases in
those four categories can, and that is not done in this change. Concretely:
this holdout can measure out-of-sample performance for branch-1
auto-approvals and branch-2/3 escalations and denials; it proves nothing
out-of-sample about the deterministic pre-flight safety layer that
`ClinicalRiskDetector` implements. Authoring GC-106+ pre-flight cases
(experimental treatment, rare condition, conflicting guidelines, prior
denial — never referenced under `src/pacca/`) is named here as the follow-up
that closes this gap; it is out of scope for this change.

## What ships in Phase H5

The README's v2.3 cycle commits PACCA to a unified benchmark in Phase H5 (weeks 10-12). When that lands, this document expands to cover:

- **Unified benchmark of 100+ cases** drawn from the existing 53-case demo dataset and the 20-case clinical golden set, plus newly synthesized cases targeting under-tested escalation paths.
- **k=2 rollouts per case** to surface non-determinism that single-run benchmarks hide.
- **Pass@1, tokens-per-case, Succ/Mtok metrics** following the AHE paper's measurement conventions.
- **Per-iteration regression history** so any iteration's benchmark delta is comparable to its predicted-impact contract in the change manifest.

## Reproducing today's evaluation

```bash
# Unit + integration (CI-gated)
pytest tests/unit tests/integration

# Clinical accuracy (uses Claude API; costs ~$0.05 per full run)
pytest tests/clinical

# Manifest validation (inline; a dedicated CLI is an H5 deliverable)
python -c "import json, jsonschema; jsonschema.validate(json.load(open('harness/manifests/iter-1.json')), json.load(open('harness/manifests/change_manifest.schema.json')))"

# Doc-drift guard (catches src/*.py references in docs that don't resolve on disk)
python -m pytest tests/harness/doc_drift_guard.py tests/harness/test_iter2_hardening.py

# Coverage report
pytest tests/unit --cov=pacca --cov-report=term-missing
```

## Reading the iteration record

For any harness iteration `harness-iter-N`:

1. Read [`harness/manifests/iter-N.json`](../harness/manifests/) for the predicted-impact contract.
2. Read [`docs/DECISIONS.md`](DECISIONS.md) for the verdict (predicted vs. observed, ratified/reverted).
3. Read [`docs/ITERATIONS.md`](ITERATIONS.md) for the narrative — failure pattern → change → trajectory before/after → eval delta.
4. Compare the manifest's `evidence` block to the iteration's CI run on GitHub Actions.

## What is NOT evaluated yet

Honesty signal: enumerated explicitly so a reader can trust the rest.

- **Production latency under load** — single-process runtime numbers exist; sustained-load benchmarks await live-demo deployment.
- **Cost-per-decision at scale** — token counts per case are recorded; aggregate $/decision projections are simulated, not measured.
- **Adversarial prompt injection** — basic guardrails are unit-tested; a dedicated red-team suite is on the roadmap (post-H5).
- **HIPAA SaMD-grade clinical validation** — the system is positioned as a portfolio artifact and not certified for real-PHI use; clinical validation by a qualified medical director is a deployment-time obligation per `SECURITY.md`.

## Why a stub instead of the full document

This file was promised by the README in advance of Phase H5. A live link to a self-explaining stub reads better than a broken link, and the harness-engineering discipline applies to documentation: H5 will land as its own iteration with its own manifest entry.
