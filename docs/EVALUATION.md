# PACCA — Evaluation Methodology

> **Status:** Draft. The current evaluation surface is real and runnable; the consolidated narrative below points to the canonical sources rather than re-stating them. A full evaluation document will land with Phase H5 (Evaluation Harness Expansion).

## What is evaluated today

| Layer | Where it lives | What it checks |
|---|---|---|
| **Unit suite** | [`tests/unit/`](../tests/unit) | 120 tests, all green. Covers escalation tree, models, retry/tracing, audit trail, prompt engineering, security/scalability, config API. Runs in ~7 seconds. |
| **Integration suite** | [`tests/integration/`](../tests/integration) | Cross-component flows including the level-5 maturity flow (`tests/test_level5_flow.py`). |
| **Clinical accuracy / LLM-as-judge** | [`tests/clinical/`](../tests/clinical) | 20-case clinical golden dataset scored 1–5 by Claude Haiku as judge. CI gate at ≥80% accuracy. Hallucinations score automatic 1 — no acceptable rate of inventing clinical data. |
| **Hallucination zero-tolerance** | `GC-018`, `GC-019` in the unit suite | Sparse-notes traps that fail the build on any score-1 hallucination. |
| **Schema validation** | Inline `jsonschema.validate(...)` against [`change_manifest.schema.json`](../harness/manifests/change_manifest.schema.json) | Every change manifest under `harness/manifests/` is validated before merge. A dedicated `pacca.harness.validate_manifest` CLI is a planned H5 deliverable; today the validation runs inline (see "Reproducing today's evaluation" below). | <!-- drift-guard: ignore -->

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
still computed over every verdict exactly as before. **As built**, the live
gate (`test_full_pipeline_meets_accuracy_threshold`) does not yet evaluate the
held-out cases at all, so `accuracy_held_out` reports `0/0` until a future
iteration wires the holdout into an actual evaluation run (see the "Unified
benchmark of 100+ cases" Phase H5 item below) — this split is the reporting
plumbing for that, added ahead of the run it will eventually score.

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
