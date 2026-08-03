# PACCA Harness Decisions Log

> **What this is:** an append-only log of every behavioral change to PACCA's agent harness, paired with the predicted impact at the time the change shipped and the verified outcome from the next evaluation round. Updated on every `chg-N:` commit.
>
> **What this is for:** three audiences. (1) Engineers reading the codebase, who want to know *why* a component exists, not just *what* it does. (2) Healthcare reviewers (audit, dispute, QA), who need traceability from a clinical decision back to the harness state that produced it. (3) Future iterations of PACCA itself, which use the verdict history to calibrate future predictions.
>
> **Reading this document:** entries are reverse-chronological (newest at top after the seed). Each entry copies the relevant `change_manifest.json` fields, then — when the next iteration's evaluation has run — adds a verdict block. Entries are never edited or deleted; corrections are made by adding a new entry that supersedes the prior one and citing the supersession.
>
> **Format authority:** entries follow the schema at [`harness/manifests/change_manifest.schema.json`](../harness/manifests/change_manifest.schema.json). The methodology is documented in [`docs/HARNESS.md`](./HARNESS.md) and the underlying paper is Lin et al., *Agentic Harness Engineering* (arXiv:2604.25850, 2026).

---

## Index

- [iter-23 — chg-33: GC-034 expects PRE_FLIGHT_ESCALATE](#iter-23-gc034)
- [iter-22 — chg-32: wire prior_denial_codes so Branch 7 can fire](#iter-22-branch7-data)
- [iter-21 — chg-30/31: the redirect after chg-29's rollback](#iter-21-branch6-substring)
- [iter-20 — chg-28/29: make the DENY class observable, then fail to fix it (first ROLLBACK)](#iter-20-deny-class)
- [iter-19 — chg-26/27: two repairs to the evaluation instrument](#iter-19-evaluator-repairs)
- [iter-18 — chg-25: separate what is checkable from what is judged](#iter-18-judge-separation)
- [iter-17 — chg-24: bound the submit transaction to the work](#iter-17-transaction-bounds)
- [iter-16 — chg-21/22/23: three runtime defects from the 2026-07-27 audit](#iter-16-runtime-defects)
- [iter-15 — chg-19/20: make RAG degradation visible](#iter-15-rag-visibility)
- [iter-14 — chg-17/18: make the RAG retrieval path real (import-chain repair + collection/filter/scoring fixes)](#iter-14-rag-real)
- [iter-13 — chg-14/15/16: wire + govern the case_precedents institutional-memory RAG collection](#iter-13-precedent-rag)
- [iter-12 — chg-13: deferrable audit FK, fixes Postgres FK violation (B3)](#iter-12-deferrable-audit-fk)
- [iter-11 — chg-11/chg-12: server-side decision_id + legible integrity failures (B6)](#iter-11-server-side-decision-id)
- [iter-10 — Runtime evidence-grounding detector (P-5 / T-18) (1 change)](#iter-10-evidence-grounding)
- [iter-9 — Scope guard warn→enforce + persistence-guarded DB writes (P-4 / T-17) (1 change)](#iter-9-enforce)
- [iter-8 — Minimum-necessary scope guard (P-4 / T-17), warn mode (1 change)](#iter-8-scope-guard)
- [iter-7 — Per-run IntentRecord (P-3 / T-16) (1 change)](#iter-7-intentrecord)
- [iter-6 — Adult complexity pre-flight + first deny-class H2 entry + full structlog migration (4 changes)](#iter-6-adult-and-deny)
- [iter-5 — Pediatric data + complexity-score model + third H2 entry + structlog cleanup (4 changes)](#iter-5-broad)
- [iter-4 — Second H2 memory entry + decision_agent.py deletion (2 changes)](#iter-4-h2-second-entry)
- [iter-3 — H2 Institutional Memory + Escalation-Branch Completion (3 changes)](#iter-3-h2-and-escalation)
- [iter-2 — Eval-Net Hardening, 6 changes (chg-1 through chg-6)](#iter-2-eval-net-hardening)
- [Correction (2026-05-22) — iter-0 trajectory instrumentation record](#correction-iter0-trajectory)
- [iter-1 — chg-1: Decision Support and Medical Director prompt extraction (Phase H1)](#chg-1-iter-1)
- [iter-0 — Baseline Crystallization (seed)](#iter-0-baseline-crystallization)

---

<a name="iter-23-gc034"></a>

## iter-23 — GC-034 expects PRE_FLIGHT_ESCALATE, 1 change

| Field | Value |
|-------|-------|
| Iteration tag | `harness-iter-23` |
| Date | 2026-08-02 |
| Author | David Reed |
| Constraint levels touched | `evaluation_harness` (chg-33) |
| Behavioral surface modified | NO — case data and tests only |
| Changes | 1 |
| Verdict | pending |

**Description.** The second case (after chg-31's GC-036) found asserting an outcome a
pre-flight boundary forbids. GC-034 expected `DENIED` while its own clinical notes fire the
experimental-treatment check, so the DecisionAgent is never called and the expectation could
never be met. Corrected to `PRE_FLIGHT_ESCALATE` per David's call. Full manifest:
[`harness/manifests/iter-23.json`](../harness/manifests/iter-23.json).

### The outcome is right; the mechanism is not

Branch 4 fires here through three separate defects: substring matching turns one *"No published
Phase III data"* into `phase i` + `phase ii` + `phase iii` hits; there is no negation awareness,
so *"Patient is **not enrolled** in a clinical trial"* matches `clinical trial`; and `off-label`
sits in the experimental keyword list although off-label use of an approved drug is a coverage
question. Every trigger is a false positive that happens to reach the right answer.

The expectation is therefore **coupled to three open bugs**, and a test pins that coupling so
repairing branch 4 goes red with instructions rather than flipping the case silently.

### A near-miss worth keeping

An earlier draft replaced GC-034's `reasoning_must_not_include` on the premise that
`experimental`/`off-label` were forbidden there and would now conflict. They were not — the list
was `["approved", "appropriate", "appropriately", "appropriateness"]`, carrying chg-25's F2
backfill. The replacement would have silently deleted that coverage. A test caught it. The
failure mode — rewriting a constraint list *while* changing an outcome — is the transferable
lesson, not the specific keyword.

---

<a name="iter-22-branch7-data"></a>

## iter-22 — Wire prior_denial_codes so Branch 7 can fire, 1 change

| Field | Value |
|-------|-------|
| Iteration tag | `harness-iter-22` |
| Date | 2026-08-01 |
| Author | David Reed |
| Constraint levels touched | `escalation_branch` (chg-32) |
| Behavioral surface modified | YES — a dormant pre-flight branch becomes reachable in production |
| Changes | 1 |
| Verdict | pending |

**Description.** Branch 7 (`prior_denial_same_service`) is one of seven deterministic pre-flight
checks on a boundary CLAUDE.md says overrides model confidence — and it had **never been able to
fire on a real submission**. `Orchestrator.process_decision` defaults `prior_denial_codes` to
`[]` and no route populated it. Adds a patient-scoped repository lookup, declares
`db.read_prior_denials` on the IntentRecord, and calls it under the P-4 guard. Full manifest:
[`harness/manifests/iter-22.json`](../harness/manifests/iter-22.json).

### What the evaluation harness structurally could not detect

The branch fired only in the golden-set harness, where each case hands it the codes directly.
**The harness supplied what production did not**, so no amount of golden-set passing could have
surfaced the gap — a safety check that cannot trigger, passing tests that feed it its own input.
The detector's own docstring had described the missing query for months and called it "a Week 3
enhancement". The gap was documented at the point of the defect and still shipped, because
nothing tested the production path.

### Placement was load-bearing and wrong on the first attempt

Written next to the orchestrator call it reads better, but a `SELECT` there autobegins a read
transaction that stays open across the LLM window — the 0.54s connection hold chg-24 measured and
removed. The comment on T1's commit says *"Nothing between here and T2 touches this session."*
The lookup now sits inside T1, and `test_run_sites_are_scope_guarded` asserts the guarded actions
as an **ordered** list so a reordering that reintroduces the hold fails rather than passing as
"still guarded".

### Limitations

The clinical gate cannot verify this change at all — it runs the harness pipeline, which supplies
the codes directly and never touches the route. Verification is unit plus real-Postgres
integration. The eval harness remains unable to detect a regression here. Separately, the lookup
is **not fail-closed**: if the query raises, the submission errors rather than escalating, so a
failure to *confirm* the absence of a prior denial is treated as an error rather than "cannot
rule one out". The RAG-degraded path took the opposite posture; reconciling them is open.

---

<a name="iter-21-branch6-substring"></a>

## iter-21 — The redirect after chg-29's rollback, 2 changes

| Field | Value |
|-------|-------|
| Iteration tag | `harness-iter-21` |
| Date | 2026-07-31 |
| Author | David Reed |
| Constraint levels touched | `escalation_branch` (chg-30), `evaluation_harness` (chg-31) |
| Behavioral surface modified | YES — chg-30 changes what pre-escalates |
| Changes | 2 |
| Verdict | chg-30 **KEEP** |

**Description.** chg-30 fixes the defect chg-29 was written for and missed: several approval
markers are substrings of their own negations (`recommended` inside `not recommended`), so a
single unambiguous negative statement satisfied both `has_approval` and `has_rejection` and
fabricated a conflict with itself. chg-31 corrects GC-036 to `PRE_FLIGHT_ESCALATE`. Full
manifest: [`harness/manifests/iter-21.json`](../harness/manifests/iter-21.json).

### Why the aggregate hid the result

The headline accuracy was **unchanged at 85.7% (36/42)** before and after. Reading the total
alone would show chg-30 doing nothing. GC-026 moved fail→pass and GC-019 moved pass→fail in the
same run, netting zero. The "no regression" claim rests on the per-case comparison, not the
aggregate — and GC-019's flip is judge/agent variance, verified unattributable because its
guidelines contain no conflict markers at all.

### What it settled about the previous iteration

GC-026 was denied correctly at score 5 **with chg-29 reverted**. The capability chg-29 tried to
add to institutional memory already existed; the agent had simply never been consulted. That is
the strongest available evidence that the rollback was right and that the original diagnosis was
wrong at the component level rather than merely incomplete.

### Blast radius, measured later

chg-30 shipped admitting its blast radius was unmeasured. The sweep (2026-08-03) found **exactly
two** cases changed: GC-026 and GC-085 — the latter's only approval hit being `indicated` inside
`contraindicated`, the same defect in a case nobody had examined. No held-out case changed.
GC-085 sits in neither the gate nor the holdout, so that correctness improvement is invisible to
every evaluation run.

---

<a name="iter-20-deny-class"></a>

## iter-20 — Make the DENY class observable, then fail to fix it, 2 changes

| Field | Value |
|-------|-------|
| Iteration tag | `harness-iter-20` |
| Date | 2026-07-30 |
| Author | David Reed |
| Constraint levels touched | `evaluation_harness` (chg-28), `long_term_memory` (chg-29) |
| Behavioral surface modified | chg-29 YES (reverted); chg-28 NO |
| Changes | 2 |
| Verdict | chg-29 **ROLLBACK** — PACCA's first |

**Description.** chg-28 wired `DENIAL_CASES` into the accuracy gate, taking DENY-class coverage
from 4/7 to 7/7. chg-29 then added a coverage-criteria deny pattern to institutional memory and
**had no effect whatsoever**. Full manifest:
[`harness/manifests/iter-20.json`](../harness/manifests/iter-20.json).

### The first rollback, and why it was worth having

The live gate returned results byte-identical to the baseline. chg-29 was inert *by
construction*: `ClinicalRiskDetector` pre-escalates all four target cases, and the pipeline
short-circuits before the DecisionAgent runs. The memory file it edited was never read for those
cases.

Two signals had been misread in the same direction. The recurring **0.00 confidence** was the
pipeline's hardcoded value on the pre-escalate path, not agent uncertainty. The "invented
conflict between aligned guidelines" the judge criticised was the *detector's own reason string*
rendered into a synthetic rationale — the judge was grading machine-generated text and reasonably
critiquing it as if an agent had written it. The cases' ground truth was verified carefully; the
causal path never was. P-013 applied to the data and skipped on the mechanism.

### What chg-28 bought

Going from 2 evaluated DENY cases to 5 produced GC-035 as a **discriminating negative** — the one
DENY case the agent is actually consulted about, and the only one it gets right. That single
observation is what exposed the mechanism. The measurement change paid for itself by invalidating
the behavioural change that followed it.

### Limitations

At n=7 the DENY stratum's Wilson interval is about ±26 pp. The class is now *observable*, not
*measurable*, and no DENY-rate claim is licensed by it.

---

<a name="iter-19-evaluator-repairs"></a>

## iter-19 — Two repairs to the evaluation instrument, 2 changes

| Field | Value |
|-------|-------|
| Iteration tag | `harness-iter-19` |
| Date | 2026-07-29 |
| Author | David Reed |
| Constraint levels touched | `evaluation_harness` (chg-26, chg-27) |
| Behavioral surface modified | NO — evaluator and case data |
| Changes | 2 |
| Verdict | chg-25 **IMPROVE** |

**Description.** chg-26 removes `PD-L1 TPS` from GC-018's trap list; chg-27 renumbers three
colliding case ids and adds an object-level dataset integrity gate. Full manifest:
[`harness/manifests/iter-19.json`](../harness/manifests/iter-19.json).

### Moving a check changes what the check means

GC-018's trap keywords were authored for an LLM judge, which reads them semantically — a judge
distinguishes *"PD-L1 TPS: NOT documented"* from *"PD-L1 TPS is 62%"*. chg-25 correctly moved
keyword checking into a string matcher, which cannot. Moving the **reader** silently redefined
the keyword, and the repo's strictest assertion began failing builds on correct behaviour, at 1
in 6 live runs. The rule now in the authoring guide: forbid assertions, never bare criterion
names.

### Counting the wrong noun

chg-27's finding generalises further. Every measurement of the dataset counted unique **ids** —
`all_dataset_case_ids()` returns a `frozenset[str]` — so three ids each identifying two different
clinical cases collapsed into one, and 108 case objects were reported as 105 for seven weeks.
Both authoring PRs had asked "is this id free?", both checked, and both got yes from an
instrument that had already absorbed the cases they were asking about.

Correcting the count exposed a larger fact: only **71 of 108** cases are executed by any
evaluation run. Recorded as a report rather than a gate, per the maintainer's framing that the
2026-05-28 breadth batch was authored as holdout reserve.

---

<a name="iter-18-judge-separation"></a>

## iter-18 — Separate what is checkable from what is judged, 1 change

| Field | Value |
|-------|-------|
| Iteration tag | `harness-iter-18` |
| Date | 2026-07-28 |
| Author | David Reed |
| Constraint levels touched | `evaluation_harness` (chg-25) |
| Behavioral surface modified | NO — evaluator only |
| Changes | 1 |
| Verdict | chg-24 **KEEP** |

**Description.** *(Reconstructed from the manifest — this iteration predates the current
session's direct record.)* PACCA's headline anti-hallucination metric was an LLM's self-reported
boolean, produced from a prompt that labelled keyword constraints as "hallucination markers".
GC-028 produced the same violation and the same decision across two runs; the judge called it an
anti-pattern once and a hallucination the next, flipping the count 0→1 with no system change, and
`correct_outcome` flipped with it. chg-25 introduces a three-tier evaluator: deterministic
keyword checks, deterministic outcome/grounding checks reusing the runtime detector, and a judge
confined to reasoning quality and fabrication. Full manifest:
[`harness/manifests/iter-18.json`](../harness/manifests/iter-18.json).

### Why this was the right constraint level

The evaluator is the instrument every other number depends on. A metric that moves without the
system moving is not a measurement, and it defeats audit: a reviewer asking "how many
hallucinations" got a number that differed between two runs of unchanged code.

---

<a name="iter-17-transaction-bounds"></a>

## iter-17 — Bound the submit transaction to the work, 1 change

| Field | Value |
|-------|-------|
| Iteration tag | `harness-iter-17` |
| Date | 2026-07-28 |
| Author | David Reed |
| Constraint levels touched | `tool_implementation` (chg-24) |
| Behavioral surface modified | NO — persistence boundaries |
| Changes | 1 |
| Verdict | chg-21, chg-22, chg-23 all **KEEP** |

**Description.** *(Reconstructed from the manifest.)* `submit_authorization` held a DB write
transaction open across the Claude API calls (~23s), making SQLite concurrency literally 1 and
pinning a Postgres connection idle-in-transaction for the duration of every request. chg-24
bounds the transaction to the work rather than the request. Full manifest:
[`harness/manifests/iter-17.json`](../harness/manifests/iter-17.json).

### The finding that outlived the change

chg-24's spec named two states for a duplicate submission; a third existed *in flight*, and the
race produced two decisions followed by a permanent 500. That is the instance behind
AGENT_LESSONS **P-013** — a load-bearing claim about current behaviour must be measured, not
asserted — which has since caught three further defects, including chg-29's rollback.

---

<a name="iter-16-runtime-defects"></a>

## iter-16 — Three runtime defects from the 2026-07-27 audit, 3 changes

| Field | Value |
|-------|-------|
| Iteration tag | `harness-iter-16` |
| Date | 2026-07-28 |
| Author | David Reed |
| Constraint levels touched | multiple (chg-21, chg-22, chg-23) |
| Behavioral surface modified | YES — retry policy, escalation persistence, audit durability |
| Changes | 3 |
| Verdict | chg-19, chg-20 both **KEEP** |

**Description.** *(Reconstructed from the manifest.)* chg-21: 5xx/529 responses were not retried,
and the SDK's own hidden retry loop multiplied attempts under a second backoff schedule. chg-22:
a `ScopeViolation` escalation was not persisted, so a fail-closed case never reached the review
queue. chg-23: audit writes shared the business transaction, so a rolled-back request erased the
record documenting it. Full manifest:
[`harness/manifests/iter-16.json`](../harness/manifests/iter-16.json).

### Found by adversarial validation, not by the suite

All three were surfaced by deliberate adversarial review rather than by a failing test — the
pattern behind the maintainer's Designer/Executor/Validator workflow. chg-23 in particular
produced AGENT_LESSONS **P-009**: SQLite proves nothing about FK behaviour, and a
`PRAGMA`-forced SQLite test confirmed the wrong thing. The real-Postgres integration tier caught
what 890 unit tests did not.

---

<a name="iter-15-rag-visibility"></a>

## iter-15 — Make RAG degradation visible, 2 changes

| Field | Value |
|-------|-------|
| Iteration tag | `harness-iter-15` |
| Date | 2026-07-27 |
| Author | David Reed |
| Constraint levels touched | `tool_implementation` (chg-19), `escalation_branch` (chg-20) |
| Behavioral surface modified | YES — chg-20 adds a degradation-triggered escalation |
| Changes | 2 |
| Verdict | pending at the time; both **KEEP** at iter-16 |

**Description.** *(Reconstructed from the manifest.)* Closes the silent-fallback pattern iter-14
fixed one instance of. chg-19 makes `GuidelineRetriever.query()` return a `RetrievalOutcome`
rather than a bare `str`, covering both guideline-retrieval fallback and a separate
precedents-failure axis, so degradation is observable instead of inferred. chg-20 audits every
degradation before deciding whether to proceed, and adds an opt-in escalation
(`rag_degraded_escalates`). Full manifest:
[`harness/manifests/iter-15.json`](../harness/manifests/iter-15.json).

### The generalisable shape

A `str` return type cannot express "this succeeded, but not the way you think". Both iter-14 and
iter-15 are instances of the same failure: a fallback path that works, returns something
plausible, and reports nothing — so nobody learns the primary path is broken. The fix is a return
type with room for the degraded case, not more logging.

---

<a name="iter-14-rag-real"></a>
## iter-14 — Make the RAG retrieval path real, 2 changes

| Field | Value |
|-------|-------|
| Iteration tag | `harness-iter-14` |
| Date | 2026-07-25 |
| Author | David Reed |
| Base model (manifest) | `claude-sonnet-4-5-20250929` |
| Base model (this evaluation) | `claude-sonnet-4-6` — **substituted**, see Limitations |
| Constraint levels touched | `tool_implementation` (chg-17, chg-18) |
| Behavioral surface modified | YES — what guideline context the DecisionAgent sees |
| Changes | 2 |
| Verdict | **KEEP** (both) |

**Description.** chg-17 repairs the import chain that made `pacca.rag.pipeline` unimportable
(`uuid7` → `uuid_extensions`, the missing `ClinicalSpecialty` / `TreatmentCategory` enums,
absent re-exports), so the production pipeline runs instead of a bare-`except` fallback nobody
could observe. chg-18 removes the phantom ungoverned `clinical_guidelines` collection (the
retriever now injects the governed one), replaces `$contains` metadata filters that silently
matched nothing with per-value boolean flags, and derives similarity from the collection's real
distance space. Full manifest: [`harness/manifests/iter-14.json`](../harness/manifests/iter-14.json).

### Verdict (recorded 2026-07-25, at branch HEAD of `fix/audit-p0-p1-corrections`)

| Field | Value |
|-------|-------|
| Outcome | **keep** (chg-17 and chg-18) |
| Live clinical gate | **PASS** — `pytest tests/ -m clinical`, 7 passed / 1 failed, 807.80s |
| Golden-set accuracy | **87.2% (34/39)** against the 80% CI threshold |
| Hallucinations | **0** — `test_zero_hallucinations_on_sparse_cases` PASSED; GC-018/019 clean |
| Judge-failed cases | GC-022, GC-026, GC-027, GC-029, GC-075 (all score 2; correct outcome, weak reasoning on 022/029) |
| Deterministic suite | 774 passed, 2 skipped, 8 deselected (`pytest tests/ -m "not clinical"`) |
| Lint / manifests | `ruff check src/ tests/` clean; `validate_manifest --all` 15/15 valid |
| Precision on predicted_fixes | **3/3 chg-17, 3/3 chg-18** — verified below |
| Recall on risk_cases | no predicted risk case was observed to fire |

**Predicted fixes — verified.** The pipeline is demonstrably live, not merely importable.
A seeded retrieval logs `rag_pipeline_initialized collection=nccn_guidelines` and returns
chunk-scored context (`Relevance Score: 0.61` for the matching lung-screening guideline vs
`0.19` for an unrelated spine guideline), confirming chunk scoring, the governed single
collection, and a real distance space. `pipeline_available` reports `True`. Under the
pre-repair tree the same call logs `rag_pipeline_init_failed … fallback=direct_chromadb`.

**A predicted risk that did *not* materialise.** chg-18 warned that the precedent path could
be affected. It was not: a `/feedback` precedent is still retrieved and rendered to the agent
under the `PAST MEDICAL DIRECTOR DECISIONS (PRECEDENTS)` header, verified directly.

### The one red test, and why it is not attributed to this iteration

`tests/test_level5_flow.py::test_learning_loop_spine` fails: after a Medical Director override
is taught via `/feedback`, the identical weak spine case returns **IN_REVIEW**, not
**AUTO_APPROVED**. [iter-13](#iter-13-precedent-rag) recorded that flip as proven end-to-end,
so this is a genuine behavioural difference against the recorded baseline.

It is **not** caused by chg-17/chg-18. A controlled A/B holding the model constant at
`claude-sonnet-4-6` was run against the pre-repair tree (`5c27714`, the base of this branch)
and this one:

| Tree | RAG path | Outcome | Confidence |
|------|----------|---------|-----------|
| `5c27714` (pre-repair) | `fallback=direct_chromadb` | IN_REVIEW | 0.32 |
| branch HEAD (repaired) | `rag_pipeline` | IN_REVIEW | 0.87 |

Both escalate. The repaired path escalates *better*: its rationale explicitly weighs the
retrieved precedent and declines to generalise it, where the fallback's rationale never
addresses the precedent at all. The remaining delta against iter-13 tracks the **model
substitution**, not the retrieval repair.

The agent's stated reason is worth recording verbatim in substance: the precedent was an
override justified by *severe motor weakness*, and no such finding is documented in the
resubmitted case, so it treats the precedent as a case-specific correction rather than a
generalisable approval pattern. That is the P-5 evidence-grounding invariant
([iter-10](#iter-10-evidence-grounding)) behaving as designed — approving here would mean
approving on evidence absent from the submission. **The assertion was therefore left failing
rather than relaxed.** Whether the Level-4 demo expectation or the P-5 invariant is the
correct contract is a clinical-policy decision for a human reviewer, not a test edit; it is
logged here as the open question this iteration surfaced.

### What the marker fix exposed (non-behavioral, test-only)

`6b6288d` made the clinical gate select by marker rather than by directory, which put
`tests/test_level5_flow.py` into `make test-clinical` for the first time. The module had
never executed in any target, and it carried three latent defects that had nothing to do with
the RAG repair — each pre-dating this branch. Repaired in this commit, no manifest entry
(test-only, no agent surface):

1. **No authentication.** Both routers are mounted with `dependencies=[Depends(verify_token)]`
   (since `9073dc7`, iter-6); every request 401'd. Now sends a Bearer token, mirroring
   `tests/unit/api/conftest.py`.
2. **No database.** A module-level `TestClient(app)` never enters the lifespan, so
   `init_database()` never ran and the first audit write failed with
   `no such table: audit_logs`. The client is now a context-managed fixture.
3. **Stale response contract.** `test_dark_factory_evolution` asserted
   `status == "optimized" | "proposed"` with a `proposal.proposed_text` field. The route has
   returned `proposal_pending` with `proposed_text_preview` since iter-6. Assertions updated
   to the contract the route actually serves.
   Additionally, `request_id` is `UNIQUE` and the SQLite file outlives the run, so the fixed
   ids collided on re-execution; ids are now unique per run.

### Limitations of this evaluation

- **Model substitution.** The manifest's base model `claude-sonnet-4-5-20250929` is refused by
  the available API gateway (`403 model not allowed`). The suite was run with
  `DEFAULT_MODEL=claude-sonnet-4-6`, a supported override (`agents/base.py`). All numbers above
  are therefore *not* directly comparable to iterations measured on `sonnet-4-5`, and the
  learning-loop delta is most likely attributable to this substitution. Re-running the gate on
  `claude-sonnet-4-5-20250929` is required before treating 87.2% as a like-for-like successor
  to the iter-13 figure.
- **Judge non-determinism.** Single run; no tolerance band applied. The five score-2 cases are
  reasoning-quality failures with correct outcomes in 022/029 and genuinely wrong escalations in
  026/027/075 — the latter three are escalate-instead-of-deny errors and are the most useful
  signal in this run for a future iteration.
- **Scores are not comparable across chg-18** by the manifest's own risk note: the chunk-overlap
  guard changed chunk boundaries.

---

<a name="iter-13-precedent-rag"></a>
## iter-13 — Wire + govern the case_precedents institutional-memory RAG collection, 3 changes

| Field | Value |
|-------|-------|
| Iteration tag | `harness-iter-13` |
| Date | 2026-07-24 |
| Author | David Reed |
| Base model | `claude-sonnet-4-5-20250929` |
| Constraint levels touched | `tool_implementation` (chg-14), `middleware` (chg-15, chg-16) |
| Behavioral surface modified | YES — precedent storage, the scope-guard collection model, and the /feedback endpoint's governance |
| Changes | 3 |
| Verdict | **KEEP** (all three). Full unit suite 681 passed; loop proven end-to-end |

**Context.** The prior-decision precedent loop (`/feedback` → embed → retrieve → the DecisionAgent weighs "PAST MEDICAL DIRECTOR DECISIONS") was already ~80% wired. This iteration closed the gaps and brought it under the P-3/P-4/P-5 governance the submit path already had. **Proven end-to-end this session:** a Medical Director precedent written via `/feedback` flipped the identical case from **IN_REVIEW → AUTO_APPROVED**, with the agent's rationale explicitly attributing the change to the precedent.

### chg-14 — Deterministic precedent id + upsert
`add_precedent` used `f"prec_{abs(hash(case_summary))}"` + `.add()`. `hash(str)` is per-process randomized, so the same override got a new id on every restart (silent duplicates), and a same-process repeat collided on `.add()`. The B6 unstable-key anti-pattern, in institutional memory. Fixed with a `sha256` content id (`_precedent_id`) + `upsert`.

### chg-15 — Govern the real RAG collections, not a phantom
`IntentRecord` allowed `["clinical_guidelines"]` — a collection the retriever never queries. The live retriever reads `nccn_guidelines` + `case_precedents`; the submit route's scope check named the phantom, so the P-4 guard governed nothing and precedents were outside the minimum-necessary model. Now `RAG_COLLECTIONS` (SSOT) drives the allow-list, and the route guards the RAG query against each real collection. A drift test asserts the two stay in sync.

### chg-16 — Govern the /feedback precedent write
The precedent write had no `IntentRecord`, no scope guard, and ran *before* its audit. Now: a narrow `institutional_learning` intent (scoped to `case_precedents` + `rag.write_precedent`/`audit.append` only) declared as the first audit event, a scope-guarded write (fail-closed 403), and the learning event audited before the write. Scope-guard Rule 3 now covers any `rag.*` action, so precedent writes are collection-guarded like reads.

### P-5 policy decision (no code change)
The e2e clarified the evidence-grounding interaction is **not** a hard conflict: the agent cites the *submission* evidence id (`EV1`), not a precedent, so P-5 already passes. Decision: **precedents are institutional context, not citable evidence** — `cited_evidence_ids` stays submission-scoped and P-5 stays submission-scoped. Threading stable precedent ids into the grounding set is the roadmap "RAG chunk-id grounding" follow-up.

---

<a name="iter-12-deferrable-audit-fk"></a>
## iter-12 — Deferrable audit FK (B3), 1 change

| Field | Value |
|-------|-------|
| Iteration tag | `harness-iter-12` |
| Date | 2026-07-23 |
| Author | David Reed |
| Base model | `claude-sonnet-4-5-20250929` |
| Constraint levels touched | `audit_schema` (chg-13) — a constraint on the `audit_logs` table |
| Behavioral surface modified | YES — audit-trail persistence: whether a submission's audit writes commit on Postgres |
| Changes | 1 |
| Verdict | **KEEP**. Verified on real Postgres 16 — see below |

### chg-13 — `audit_logs.request_id` FK becomes `DEFERRABLE INITIALLY DEFERRED`

**Failure pattern.** Submitting an authorization on Postgres raised `ForeignKeyViolationError: audit_logs_request_id_fkey` at ~the first audit write, before any AI call. Dev/CI never saw it because SQLite does not enforce foreign keys.

**Root cause.** The submit route flushes audit rows immediately, and the pre-write-audit invariant requires `intent.declared` (`authorizations.py:140`) and `authorization_submitted` (`:152`) — both carrying `request_id` — to be written *before* the parent `authorization_requests` row (`:186`). The FK on `audit_logs.request_id` was non-deferrable, so Postgres checked it at statement time and rejected the first audit flush, whose `request_id` had no parent yet. (The doc's original "never inserts parent rows" cause was already fixed by PR #66; this is the residual ordering-vs-FK conflict. `audit_logs.decision_id` has no FK, so that half of the original report is moot.)

**Why deferral, not reorder.** Moving the parent insert ahead of the audits would satisfy the FK but violate the pre-write-audit invariant (`intent.declared` would no longer be event #0). Making the FK `DEFERRABLE INITIALLY DEFERRED` defers the check to the single per-request COMMIT, by which point the parent row — flushed mid-transaction — exists. Both invariants hold.

**Verification (real Postgres 16, throwaway container).**
- Non-deferrable schema, submit-route write order in one transaction → `FK VIOLATION at statement time`, 0 rows committed. Deferrable schema, same order → **COMMITTED OK, 2 audit rows, 0 orphan rows**.
- `alembic upgrade head` (001→002→003) applied cleanly; `pg_constraint` shows `condeferrable=t, condeferred=t`; `downgrade -1` reverts to `condeferrable=f`.
- `tests/unit/test_audit_fk_deferrable.py` compiles the `audit_logs` DDL under the Postgres dialect and asserts the deferral — the CI-runnable guard the SQLite suite cannot provide.

**Still manual:** the "dropped-FK local hack" the B3 report mentions is live Postgres DB state; re-adding the constraint (via migration 003) is what makes B3 stop throwing. Check with `SELECT conname FROM pg_constraint WHERE conrelid='audit_logs'::regclass AND contype='f';`.

---

<a name="iter-11-server-side-decision-id"></a>
## iter-11 — Server-side `decision_id` + legible integrity failures (B6), 2 changes

| Field | Value |
|-------|-------|
| Iteration tag | `harness-iter-11` |
| Date | 2026-07-23 |
| Author | David Reed |
| Base model | `claude-sonnet-4-5-20250929` |
| Constraint levels touched | `tool_implementation` (chg-11, chg-12) — the agent output contract and the write path. No prompt change: no prompt references `decision_id`, so `PROMPT_REGISTRY` stays at v2.7 |
| Behavioral surface modified | YES — the forced tool schema handed to both agent tiers changed shape |
| Changes | 2 |
| Live clinical gate at iter-11 HEAD | golden-set accuracy **PASSED** + zero-hallucination GC-018/019 **PASSED** (5 clinical tests, 473s of real Claude calls) — the tool-schema change did not degrade clinical output |
| Verdict | **KEEP** (both changes). Deterministic suite 684 passed. Live: the identical case submitted twice returned 200 twice with distinct ids (`PA-6f8905bf9b8a4f18`, `PA-038e4eb1c2334b92`), where the second submission previously 500'd |

### chg-11 — `decision_id` moves out of the model's hands

**Failure pattern.** Resubmitting an already-decided case returned HTTP 500. Found on 2026-07-22 while capturing a README screenshot: the first submission of the provider demo case succeeded, every subsequent one failed with `UNIQUE constraint failed: authorization_decisions.decision_id`.

**Root cause.** Both agent tiers passed `response_model=AuthorizationDecision` to `BaseAgent.execute()`, which builds the forced tool schema from `response_model.model_json_schema()`. `decision_id` is a field on that model, so it was in the schema and **the model populated it** — and that value was written to a `unique=True, index=True` column. The model is deterministic for identical input, so the same case reproduced the same id. Grepping the observed prefixes (`PA-`, `dec_`) across `src/` returns **no matches**: nothing in the codebase ever constructed one.

**Why this is more than a crash.** `audit_logs.decision_id` and the `human_reviews` FK both reference this value. A model that repeats an id silently cross-links two decisions' audit trails — which defeats the correlation-id guarantee the audit design rests on. Hence P0 rather than cosmetic.

**The change.** A new `DecisionDraft` model becomes the `response_model` for both tiers: `status`, `confidence_score`, `rationale`, `cited_evidence_ids` — and nothing else. `decision_id` is minted server-side by `mint_decision_id()` via `AuthorizationDecision`'s `default_factory`. Because the field is absent from `DecisionDraft`, it is absent from the tool schema, so the model **cannot** supply it — this is structural, not advisory. Explicit ids are still honoured for the deterministic escape hatches (`PREESC-`, `SCOPE-`).

**Standing rule adopted:** no LLM-supplied value may land in a unique, indexed, or foreign-keyed column.

### chg-12 — Integrity failures report themselves

**Failure pattern.** The B6 traceback surfaced `PendingRollbackError` rather than the `IntegrityError` that caused it. An operator reading that log learns the session is broken, not why.

**Root cause.** `DecisionRepository.create()` called `session.flush()` with no failure handling. After the `IntegrityError` the session stays poisoned, so the caller's next statement — the audit write — raises `PendingRollbackError` and masks the original.

**The change.** Roll back, log `authorization_decision_write_failed`, re-raise. This is not error recovery: the write genuinely failed and the exception still propagates. It exists so the failure is legible.

---

<a name="iter-10-evidence-grounding"></a>
## iter-10 — Runtime evidence-grounding detector (P-5 / T-18), 1 change

| Field | Value |
|-------|-------|
| Iteration tag | `harness-iter-10` |
| Date | 2026-07-22 |
| Author | David Reed |
| Base model | `claude-sonnet-4-5-20250929` |
| Constraint levels touched | `escalation_branch` (chg-10) — a deterministic safety short-circuit; paired `system_prompt` v2.7 |
| Behavioral surface modified | YES — new escalation path + DecisionAgent prompt |
| Changes | 1 |
| Live clinical gate at iter-10 HEAD | golden-set accuracy **PASSED** + zero-hallucination GC-018/019 **PASSED** (v2.7 citation prompt did not degrade quality). One unrelated **pre-existing** failure — `sme_authoring_smoke_test::test_sme_agent_smoke_round_trip` (`GC-SMOKE` id, a subsystem chg-10 does not touch), same as flagged at T-16 |

### chg-10 — Evidence-grounding detector + evidence-id citation

| Field | Value |
|---|---|
| Type | `new` |
| Constraint level | `escalation_branch` (paired `system_prompt` v2.6→v2.7 per the AHE prompt-pairing rule) |
| Files | `agents/evidence_grounding.py`, `agents/orchestrator.py`, `models/authorization.py`, `models/enums.py`, `agents/decision_support/system_prompt.md`, `agents/prompts/templates.py`, `tests/unit/test_evidence_grounding.py`, `tests/unit/test_h2_memory_criterion_preservation.py` |
| PHI impact | `indirect` |
| Audit relevant | yes |
| Predicted fixes | — (runtime equivalents of the GC-018/019 hallucination scenarios) |
| Risk cases | — (over-escalation from ID drift; declared bound ≤2pp) |
| Verified live | accuracy + GC-018/019 **PASSED** with v2.7; deterministic detector tests green. Verdict **keep** |

A deterministic post-agent check in the orchestrator (before confidence routing): every `cited_evidence_ids` entry must resolve to a submission `EvidenceItem`, else the run is forced to `IN_REVIEW` with a `finding.ungrounded_evidence` audit event and `EscalationReason.UNGROUNDED_EVIDENCE` — the production-path equivalent of the GC-018/019 anti-hallucination eval gate. The paired DecisionAgent prompt (v2.7) instructs the model to populate `cited_evidence_ids` using only ids present in the case.

**Honest scope.** Grounding is against **submission evidence ids only** — RAG chunks carry no agent-visible ids (the retriever hands the agent concatenated text), so a citation cannot name a retrieved chunk; threading RAG chunk ids is a separate future change. The **≤2pp escalation-delta** bound was declared but not directly measured: the accuracy eval runs cases through `ClinicalRiskDetector` + `DecisionAgent` directly, not the orchestrator, so it does not exercise the detector's escalation path — that path is covered by the deterministic orchestrator tests and bounded by id-based (not text) matching. This completes P-5; the CI enforcement (P-6) ships alongside as a non-behavioral change.

<a name="iter-9-enforce"></a>
## iter-9 — Scope guard warn→enforce + persistence-guarded DB writes (P-4 / T-17), 1 change

| Field | Value |
|-------|-------|
| Iteration tag | `harness-iter-9` |
| Date | 2026-07-22 |
| Author | David Reed |
| Base model | `claude-sonnet-4-5-20250929` |
| Constraint levels touched | `middleware` (chg-9) — promotes the chg-8 guard |
| Behavioral surface modified | YES — route now persists request + decision, guard is fail-closed |
| Changes | 1 |
| Live clinical gate at iter-9 HEAD | **not run — not informative.** The accuracy eval runs cases through `ClinicalRiskDetector` + `DecisionAgent` directly and never calls the route, so route-level persistence + guard changes cannot alter a golden-case decision. Deterministic suite **668 passed** |

### chg-9 — Enforce mode + guarded persistence writes

| Field | Value |
|---|---|
| Type | `improvement` (promotes the chg-8 guard; adds deny-capable sites) |
| Constraint level | `middleware` |
| Files | `api/routes/authorizations.py`, `config/settings.py`, `tests/unit/test_audit_trail.py` |
| PHI impact | `indirect` |
| Audit relevant | yes |
| Predicted fixes | — |
| Risk cases | — (correct operation never denies; enforce is fail-closed defense) |
| Verified live | Deterministic only: 668 passed, ruff + mypy + PHI-guard clean, manifest validates. Enforce proven end-to-end via a forced cross-case leak → IN_REVIEW. Verdict **keep** |

The submit route now writes the request (`db.write_request`, before RAG) and the decision (`db.write_decision`, after adjudication), each guarded by `enforce_scope` against the run's `IntentRecord` (identifiers must match). `settings.scope_guard_mode` flips **warn → enforce**. These are the first **identifier-checked, deny-capable** call sites — a cross-case leak now fail-closes to human review instead of merely logging.

**Depends on the persistence repair** (the `create()` methods were broken dead code; fixed + columns made nullable + migration 002, as a separate change) landing first. **Honest note:** correct operation always passes the run's own identifiers + the one allowed collection, so the guard **does not deny in normal flow** — its value is fail-closed defense against a future leak/bug, now active in enforce mode. This completes P-4.

<a name="iter-8-scope-guard"></a>
## iter-8 — Minimum-necessary scope guard (P-4 / T-17), warn mode, 1 change

| Field | Value |
|-------|-------|
| Iteration tag | `harness-iter-8` |
| Date | 2026-07-22 |
| Author | David Reed |
| Base model | `claude-sonnet-4-5-20250929` |
| Constraint levels touched | `middleware` (chg-8) — PACCA's first middleware-tier component (H3), as a call-site wrapper |
| Behavioral surface modified | YES (new guard + escalation path) — but see the no-op note below |
| Changes | 1 |
| Live clinical gate at iter-8 HEAD | **not run — not informative.** Warn mode never blocks, and the single-collection route always targets the one allowed collection, so the guard cannot alter any decision. Deterministic suite **664 passed**; the golden set is unchanged by construction |

### chg-8 — Minimum-necessary scope guard wired at the RAG query (warn mode)

| Field | Value |
|---|---|
| Type | `new` |
| Constraint level | `middleware` (first instance of the H3 tier; a wrapper, no middleware loader exists) |
| Files | `agents/scope_guard.py`, `models/enums.py`, `models/intent.py`, `config/settings.py`, `api/routes/authorizations.py`, `tests/unit/test_scope_guard.py`, `tests/unit/test_audit_trail.py` |
| PHI impact | `indirect` (scope checks read run identifiers) |
| Audit relevant | yes |
| Predicted fixes | — (observability + guard machinery; no golden case flips) |
| Risk cases | — (warn mode: no run blocked) |
| Verified live | Deterministic only: 664 passed, ruff + mypy + PHI-guard clean, manifest validates. Clinical gate deliberately not run (change cannot alter a decision — warn mode + always-allow). Verdict **keep** |

`enforce_scope(intent, action, **call_args)` reads the P-3 `IntentRecord` and fail-closes on a disallowed action, a cross-case identifier mismatch, or a non-allowed/absent RAG collection, logging `scope.allow`/`scope.deny` (arg **names**, never values). It is wired at the one live call site — the RAG query — and a `ScopeViolation` routes to human review (`EscalationReason.SCOPE_VIOLATION`), never a silent continue or 500.

**Honest scope (important).** In the current **single-collection** flow the route always targets the one allowed collection (`clinical_guidelines`), so the guard **cannot deny** even in enforce mode — its deny→human-review path is unreachable. The live effect today is `scope.allow` audit events plus a dormant enforcement path. Real enforcement value needs **deny-capable call sites** (cross-case identifiers on persisted request/decision writes), which in turn need the **broken `AuthorizationRepository.create` / `DecisionRepository.create`** methods repaired — they reference fields the current models don't have and are never called. That persistence repair is a **separate change** (deliberately not bundled into P-4). `chg-9` will flip `settings.scope_guard_mode` to `enforce` once such sites exist.

<a name="iter-7-intentrecord"></a>
## iter-7 — Per-run IntentRecord (P-3 / T-16), 1 change

| Field | Value |
|-------|-------|
| Iteration tag | `harness-iter-7` |
| Date | 2026-07-21 |
| Author | David Reed |
| Base model | `claude-sonnet-4-5-20250929` |
| Constraint levels touched | `audit_schema` (chg-7) |
| Behavioral surface modified | NO — observability / audit only, no agent decision surface |
| Changes | 1 |
| Live clinical gate at iter-7 HEAD | golden-set accuracy threshold **PASSED** + zero-hallucination GC-018/019 **PASSED** (record-only change; prior-auth path unaffected as predicted). One unrelated **pre-existing** failure — `sme_authoring_smoke_test::test_sme_agent_smoke_round_trip` (hardcoded `GC-SMOKE` id rejected by the SME-authoring validator; a subsystem chg-7 does not touch) — flagged separately |

### chg-7 — Per-run IntentRecord as the first audit event

| Field | Value |
|---|---|
| Type | `instrumentation` |
| Constraint level | `audit_schema` |
| Files | `src/pacca/models/intent.py`, `src/pacca/api/routes/authorizations.py`, `tests/unit/test_audit_trail.py` |
| PHI impact | `indirect` (subject_ref = opaque patient_id into a log path) |
| Audit relevant | yes |
| Predicted fixes | — (observability, no fix) |
| Risk cases | — (ordering regression guarded by the extended audit test) |
| Verified live | Clinical gate at iter-7 HEAD: golden-set accuracy **PASSED**, zero-hallucination GC-018/019 **PASSED** — prior-auth path unaffected, as predicted for a record-only change. (Unrelated pre-existing failure `sme_authoring_smoke_test::test_sme_agent_smoke_round_trip` — hardcoded `GC-SMOKE` id, SME-authoring subsystem untouched by chg-7.) |

Adds a typed `IntentRecord` (CausalGate's intent-contract pattern, scoped to PACCA) that the submission route appends as `action="intent.declared"` — the FIRST audit event of every run, before `authorization_submitted`. Record-only: it declares the run's `correlation_id` / `request_id` / `subject_ref` / `purpose` plus declared-constant `allowed_collections` / `allowed_actions` / `expected_effects` / `limits` into the existing `AuditLogModel.details` JSON, so no schema or migration change is needed. P-4 (minimum-necessary scope guard) and P-5 (evidence-grounding detector) will read and cite it.

It lives in the **route**, not the orchestrator (a deviation from the plan's original framing): the route owns audit event #0 and holds the identifiers at pre-flight, and the acceptance criterion is that every trail *begins* with `intent.declared` — which the orchestrator, whose events all follow `authorization_submitted`, structurally cannot satisfy. `tests/unit/test_audit_trail.py` was extended (`intent.declared` is `[0]`, `authorization_submitted` is `[1]`) and a new invariant test added. Deterministic suite 651 passed; ruff + mypy clean.

<a name="iter-6-adult-and-deny"></a>
## iter-6 — Adult complexity pre-flight + first deny-class H2 entry + full structlog migration (4 changes)

| Field | Value |
|-------|-------|
| Iteration tag | `harness-iter-6` |
| Date | 2026-05-31 |
| Author | David Reed |
| Base model | `claude-sonnet-4-5-20250929` |
| Constraint levels touched | `instrumentation` (chg-1), `escalation_branch` (chg-2), `evaluation_harness` (chg-3), `long_term_memory` (chg-4) |
| Behavioral surface modified | YES (chg-2 + chg-4) |
| Changes | 4 |
| Live clinical gate at iter-6 HEAD | golden-20 aggregate **20/20 = 100%** (median of 2 rollouts; mean 4.9; identical distribution to iter-5, zero jitter) |
| Predicted fixes verified live | — (no behavioral predictions; chg-4 is hardening, not a fix) |
| Risk cases preserved | GC-035 (DENIED 0.96 with cited basis + appeal pathway); GC-005 (IN_REVIEW psoriasis — deny memory did not bleed) — chg-4 |
| Manifest | [`harness/manifests/iter-6.json`](../harness/manifests/iter-6.json) |
| Narrative | [`docs/ITERATIONS.md` iter-6 section](./ITERATIONS.md#iter-6-adult-and-deny) |

### chg-1 — Migrate tracing.py to structlog.get_logger

| Field | Value |
|---|---|
| Type | `improvement` |
| Constraint level | `instrumentation` |
| Files | `src/pacca/config/tracing.py`, `tests/unit/test_retry_and_tracing.py` |
| Predicted fixes | — | Risk cases | — |

Finishes the iter-5 chg-1 stopgap. iter-5 chg-1 wrapped structlog-style kwargs in `extra={...}` against a stdlib `logging.Logger` as a no-new-mechanism fix; iter-6 adopts `structlog.get_logger` proper, so the three call sites pass `endpoint` / `detail` / `service_name` as native structured kwargs again and the `extra={...}` shim is gone. This is the second deferral closing: iter-3 chg-1 added `# type: ignore[call-arg]` markers, iter-5 chg-1 wrapped with `extra=`, iter-6 chg-1 finally adopts the substrate the file was originally written against. Tracing tests updated to assert structured-field capture via structlog rather than a stdlib `LogRecord`. Suite green at chg-1 HEAD.

### chg-2 — Adult complexity pre-flight (ADULT_COMPLEX at threshold 4)

| Field | Value |
|---|---|
| Type | `new` |
| Constraint level | `escalation_branch` |
| Files | `src/pacca/agents/clinical_risk_detector.py`, `src/pacca/models/enums.py`, `tests/unit/test_complexity_score_model.py` |
| Predicted fixes | — |
| Risk cases | — |
| Verified live | GOLDEN_CASES held 20/20 green at chg-2 HEAD — no golden adult case crossed threshold 4 to newly escalate |

Adds `EscalationReason.ADULT_COMPLEX` and `ClinicalRiskDetector._check_adult_complex`, mirroring `_check_pediatric_complex`. Fires for age ≥ 18 when `_compute_complexity_score >= settings.complexity_specialist_review_min` (= 4), routing to specialist pre-review. Closes the age-gating gap from iter-5 chg-3: that change built a general integer 1–5 complexity-score model but wired only the pediatric branch to it (the dataset had pediatric points to justify the threshold). A high-complexity adult — multiple comorbidities, prior failures, severe tier — bypassed specialist pre-review because no pre-flight branch consumed the score for adults.

**The phantom finding.** The runbook's design anticipated an orchestrator edit: a branch-2 mapping site that routes the new `EscalationReason` to the IN_REVIEW path. That site does not exist. Branch dispatch in the orchestrator is generic over `EscalationReason` — it routes on `flags.should_pre_escalate` and surfaces `flags.reasons`, never switching on the specific reason. So a new reason routes correctly with zero orchestrator change. The chg-2 commit is additions-only: **122 insertions, 0 deletions**; `_compute_complexity_score` and the pediatric branch are byte-for-byte untouched. The design spec described a routing step the architecture had already generalized past — a reminder that the manifest records what the code did, not what the plan predicted.

### chg-3 — Adult complexity eval set (ADULT_COMPLEXITY_CASES)

| Field | Value |
|---|---|
| Type | `new` |
| Constraint level | `evaluation_harness` |
| Files | `tests/clinical/adult_complexity_cases.py` (new), `tests/clinical/investigate_case.py`, `tests/clinical/test_clinical_accuracy.py` |
| Predicted fixes | — |
| Risk cases | — |
| Verified live | GC-101 IN_REVIEW (ADULT_COMPLEX fired); GC-102 AUTO_APPROVED (negative control held); GC-103 IN_REVIEW |

chg-2 added the adult branch; chg-3 gives it the dataset that validates it. `ADULT_COMPLEXITY_CASES = [GC-101, GC-102, GC-103]`, parallel to `PEDIATRIC_CASES`: GC-101 high-complexity adult (expect IN_REVIEW via ADULT_COMPLEX), GC-102 low-complexity adult (expect AUTO_APPROVED — the **negative control** proving the branch does not over-fire on adult age alone), GC-103 high-complexity adult (expect IN_REVIEW). Routes 2-IN_REVIEW / 1-AUTO_APPROVED as designed. GOLDEN_CASES stays 20 — the count assertion still holds; adult cases live in their own list exactly like pediatric and near-miss cases.

**Scope call — no committed near-miss adult case.** The runbook flagged a possible GC-104 just-below-threshold near-miss (complexity score exactly 3, expect AUTO_APPROVED) to pin the boundary. I did not commit one: GC-102 (low-complexity, AUTO_APPROVED) already serves as the negative control, and a clinical fixture asserting "score == 3 does not escalate" duplicates a unit test that lives more cheaply in `test_complexity_score_model.py` (which exercises the clamp and threshold directly). A third clinical fixture for the same boundary would add eval-suite weight without new signal — YAGNI. Recorded here so the omission reads as a decision, not an oversight.

### chg-4 — First deny-class H2 memory entry (benefit-cap, re-anchored GC-035)

| Field | Value |
|---|---|
| Type | `new` |
| Constraint level | `long_term_memory` |
| Files | `src/pacca/agents/decision_support/long_term_memory.md`, `src/pacca/agents/prompts/templates.py` (v2.5 → v2.6), `tests/unit/test_h2_memory_criterion_preservation.py`, `tests/clinical/investigate_case.py` |
| Predicted fixes | — |
| Risk cases | `["GC-035", "GC-005"]` |
| Verified live | GC-035 DENIED 0.96 with all five benefit-cap criteria enumerated + benefit-document basis + appeal/exception pathway cited; GC-005 IN_REVIEW psoriasis (deny memory did not bleed into an approve-class case); ephemeral documented-acute-injury variant → IN_REVIEW (over-denial guard fires) |

The first deny-class H2 entry — all three prior entries (NSCLC, RA, asthma) are approve-class biologics. Title: "Outpatient benefit-cap exhaustion without a documented exception." Five required criteria gate the deny shortcut; five anti-patterns each resolve to `**Status: IN_REVIEW.** (Not DENIED.)` as over-denial guards. PROMPT_REGISTRY DecisionSupportAgent v2.5 → v2.6.

**Honest framing: hardening, not a fix.** GC-035 was already DENIED 5/5 *before* the memory edit (Step 5a baseline, no H2 deny entry present). chg-4 does not flip a previously-wrong case — `predicted_fixes` is empty. What it adds is (1) the institutional reasoning encoded for traceability and (2) the over-denial guardrail. A deny entry's dangerous failure mode is the inverse of an approve entry's: false denial. The guard was verified **in both directions** — GC-035 still denies correctly *with citation* (not weakened), and an ephemeral variant documenting an acute-injury exception routes IN_REVIEW (the agent quoted the entry's anti-pattern and governing rule verbatim; denial does not become reflexive on pattern-match).

**The re-anchor (GC-034 → GC-035).** The runbook anchored this entry on GC-034 (off-label oncology). During Step 5 I found GC-034 cannot exercise an agent-path deny entry at all: `_check_experimental_treatment` substring-scans `EXPERIMENTAL_DIAGNOSIS_KEYWORDS` (including "off-label") with no negation handling, so GC-034 pre-escalates to IN_REVIEW *before* the DecisionSupportAgent — and thus the H2 memory — ever runs. A deny entry anchored there would be dead on arrival. GC-035 (benefit-cap, no experimental keywords) routes to the agent cleanly, so the entry re-anchored onto it. The off-label↔experimental contradiction in `_check_experimental_treatment` is logged as an **iter-7 finding**: it is a pre-flight keyword-scanner bug (a different constraint level than this `long_term_memory` change), so fixing it here would violate one-scoped-change-per-iteration.

### Iteration-level verdict and verdict on iter-5's 4 chgs

iter-6 closed at HEAD (final commit on `harness/iter-6` before merge). All gates green: 6 manifests validate against the schema; the pytest suite is green; the live golden gate holds 20/20 = 100% (median of 2 rollouts, mean 4.9, distribution identical to iter-5 with zero jitter — empirically confirming no golden regression from any of the four changes).

**All four iter-5 changes verdict** (recorded in [`harness/manifests/iter-6.json`](../harness/manifests/iter-6.json) `verdicts[]`):
- **iter-5 chg-1 (tracing `extra={...}` wrap) = `improve`** — the wrap held green and was correct, but kept tracing on stdlib logging rather than the intended structlog substrate. iter-6 chg-1 supersedes it with `structlog.get_logger`: intent preserved, mechanism refined. The only non-`keep` verdict this round.
- **iter-5 chg-2 (3 pediatric cases) = `keep`** — reused unchanged at iter-6 HEAD; iter-6 chg-3 mirrors its file pattern for the adult eval set.
- **iter-5 chg-3 (complexity-score model) = `keep`** — `_compute_complexity_score` is reused BYTE-FOR-BYTE by iter-6 chg-2's adult path (122 insertions, 0 deletions to the score function or the pediatric branch). The score model generalized to a second age band without modification — the strongest possible `keep`.
- **iter-5 chg-4 (third H2 entry, asthma) = `keep`** — stable; iter-6 chg-4 adds a fourth entry (first deny-class) without displacing it. The criterion-preservation tests for all four entries pass together; the anti-pattern (Not DENIED) floor was raised 16 → 21 to lock the new guards in.

iter-6's own chgs have no behavioral predicted_fixes (chg-4 is hardening). Risk-case preservation on chg-4 (GC-035 + GC-005) verified live. Verdicts on iter-6's 4 chgs will land in iter-7.json's `verdicts` array.

---

<a name="iter-5-broad"></a>
## iter-5 — Pediatric data + complexity-score model + third H2 entry + structlog cleanup (4 changes)

| Field | Value |
|-------|-------|
| Iteration tag | `harness-iter-5` |
| Date | 2026-05-25 |
| Author | David Reed |
| Base model | `claude-sonnet-4-5-20250929` |
| Constraint levels touched | `instrumentation` (chg-1), `evaluation_harness` (chg-2), `escalation_branch` (chg-3), `long_term_memory` (chg-4) |
| Behavioral surface modified | YES (chg-3 + chg-4) |
| Changes | 4 (largest in cycle by count) |
| Live clinical gate at iter-5 HEAD | aggregate to be captured at iteration close; risk cases verified per-chg |
| Predicted fixes verified live | — (no behavioral predictions; risk-case preservation only) |
| Risk cases preserved | GC-012 (IN_REVIEW via pediatric_complex score=4); GC-023 (AUTO_APPROVED score=5); GC-024 (IN_REVIEW score=4); GC-025 (IN_REVIEW score=5) — chg-3 + chg-4 |
| Manifest | [`harness/manifests/iter-5.json`](../harness/manifests/iter-5.json) |
| Narrative | [`docs/ITERATIONS.md` iter-5 section](./ITERATIONS.md#iter-5-broad) |

### chg-1 — Switch tracing.py to extra={...} kwargs

| Field | Value |
|---|---|
| Type | `improvement` |
| Constraint level | `instrumentation` |
| Files | `src/pacca/config/tracing.py` |
| Predicted fixes | — | Risk cases | — |

Closes the iter-3 chg-1 TODO comment. Three logger.info / logger.warning calls used structlog-style keyword arguments against stdlib `logging.Logger`; iter-3 added `# type: ignore[call-arg]` markers with a TODO to switch to structlog or wrap with `extra={...}`. iter-5 chose the wrap path (no new dependency; documented stdlib mechanism via `extra=` dict on the LogRecord). All three call sites updated; three type-ignore markers removed; full suite unchanged.

### chg-2 — Add 3 pediatric cases (PEDIATRIC_CASES)

| Field | Value |
|---|---|
| Type | `new` |
| Constraint level | `evaluation_harness` |
| Files | `tests/clinical/pediatric_cases.py` (new), `tests/clinical/test_clinical_accuracy.py`, `.pre-commit-config.yaml` |
| Predicted fixes | — | Risk cases | — |

Closes the pediatric-coverage gap from iter-4. GC-023 (10yo mild well-controlled asthma → AUTO_APPROVED), GC-024 (16yo moderate Crohn's with immunomodulator failure + growth-delay comorbidity → IN_REVIEW), GC-025 (9yo severe atopic dermatitis with multiple failures → IN_REVIEW). Together with GC-012, 4 pediatric data points spanning the chg-3 discriminator's input space. Mirrors NEAR_MISS_CASES file pattern; wired into the live gate loop. `.pre-commit-config.yaml` gains pytest + pytest-asyncio mypy deps after the modified `test_clinical_accuracy.py` surfaced untyped decorator errors in the hook env.

### chg-3 — Complexity-score model in pediatric_complex check

| Field | Value |
|---|---|
| Type | `improvement` |
| Constraint level | `escalation_branch` |
| Files | `src/pacca/models/clinical.py`, `src/pacca/models/authorization.py`, `src/pacca/agents/clinical_risk_detector.py`, `src/pacca/agents/base.py`, `tests/unit/test_complexity_score_model.py` (new), `tests/clinical/investigate_case.py` |
| Predicted fixes | — |
| Risk cases | `["GC-012", "GC-023", "GC-024", "GC-025"]` |
| Verified live | All 4: GC-012 score 4 IN_REVIEW ✓; GC-023 score 5 AUTO_APPROVED ✓ (judge: "correctly avoids inappropriate escalation despite pediatric age"); GC-024 score 4 IN_REVIEW ✓; GC-025 score 5 IN_REVIEW ✓ |

Replaces the iter-3 chg-1 keyword heuristic in `_check_pediatric_complex` with a numeric integer 1-5 complexity-score. Weighted-sum: age extremes (+2), severity tier (+0 to +3), 2+ prior failures (+1), comorbidities (+1), clamped to [1, 5]. Pediatric escalation threshold = 3.

**Honest framing**: this is a heuristic-in-score-model-clothing given only 4 pediatric data points. The defensibility comes from per-feature clinical rationale + matching the existing Settings 1-5 schema + the 4 data points validating chosen weights against expected outcomes — NOT from data fitting. Overstating empirical grounding would be easy; the qualifier matters.

23 new unit tests in `test_complexity_score_model.py` cover each weight independently, boundary/clamp behavior, structured-field vs parser-fallback paths, and the 4 real pediatric data points. Tangential type-fix work absorbed in this commit: `__all__` added to `authorization.py`; base.py `_call_with_retry` got `-> Any` + `Mapping[str, object]`; tenacity `@retry` + Anthropic `create()` got `# type: ignore[…,unused-ignore]` markers; `APIStatusError` check got a `bool()` cast.

### chg-4 — H2 memory third entry (dupilumab for severe eosinophilic asthma)

| Field | Value |
|---|---|
| Type | `new` |
| Constraint level | `long_term_memory` |
| Files | `src/pacca/agents/decision_support/long_term_memory.md`, `src/pacca/agents/prompts/templates.py` (v2.4 → v2.5), `tests/unit/test_h2_memory_criterion_preservation.py` |
| Predicted fixes | — |
| Risk cases | `["GC-012", "GC-023"]` |
| Verified live | GC-012 IN_REVIEW via pediatric_complex (memory did NOT override the policy check) score 4 ✓; GC-023 AUTO_APPROVED (memory did NOT over-fire on mild) score 5 ✓ |

Third H2 entry following the iter-3 chg-2 / iter-4 chg-1 format: 5 required criteria, 5 anti-patterns each ending `**Status: IN_REVIEW.** (Not DENIED.)`, when-applies / when-not-applies. PROMPT_REGISTRY DecisionSupportAgent v2.4 → v2.5.

The unique-to-iter-5 design element: the entry documents non-override of **both** pre-flight policy checks — iter-3 chg-1's `high_cost_check` AND iter-5 chg-3's `pediatric_complex` check. GC-012 is the canonical interaction case (severe pediatric eosinophilic asthma satisfies the memory's clinical criteria AND the pediatric_complex check correctly escalates). Memory teaches the agent to articulate "clinical criteria met **but policy escalation applies**" on cases where pre-flight has fired.

19 new tests in `test_h2_memory_criterion_preservation.py` covering injection, each required criterion + anti-pattern, the pediatric_complex interaction documentation. Aggregate H2 tests: 51 (across 3 entries).

### Iteration-level verdict and verdict on iter-4's 2 chgs

iter-5 closed at HEAD (final commit on `harness/iter-5` before merge). All gates green: 5 manifests validate; doc-drift guard PASSED; pytest 246 passed in ~7s.

**Both iter-4 changes verdict = `keep`** (recorded in [`harness/manifests/iter-5.json`](../harness/manifests/iter-5.json) `verdicts[]`):
- iter-4 chg-1 (RA biologic H2 entry): all 4 risk cases preserved at iter-5 HEAD with the third H2 entry now active alongside — the criterion-preservation tests for all 3 entries pass.
- iter-4 chg-2 (decision_agent.py deletion): file remains deleted with zero importers; full suite remains green.

iter-5's own chgs have no behavioral predicted_fixes. Risk-case preservation on chg-3 (4 pediatric cases) and chg-4 (GC-012 + GC-023) verified live. Verdicts on iter-5 chg-3 / chg-4 will land in iter-6.json's `verdicts` array.

---

<a name="iter-4-h2-second-entry"></a>
## iter-4 — Second H2 Memory Entry + decision_agent.py Deletion (2 changes)

| Field | Value |
|-------|-------|
| Iteration tag | `harness-iter-4` |
| Date | 2026-05-25 |
| Author | David Reed |
| Base model | `claude-sonnet-4-5-20250929` |
| Constraint levels touched | `long_term_memory` (chg-1), `tool_implementation` (chg-2 removal) |
| Behavioral surface modified | YES (chg-1: second H2 entry); chg-2 is dead-code removal with zero runtime effect |
| Changes | 2 |
| Live clinical gate at iter-4 HEAD | aggregate **20/20 = 100%** preserved (median of 2 rollouts; identical to iter-3-final) |
| Predicted fixes verified live | — (no behavioral predictions; risk-case preservation only) |
| Risk cases preserved | GC-010 (5, IN_REVIEW via cost), GC-005 (5, IN_REVIEW psoriasis), GC-017 (5, IN_REVIEW PsA), GC-016 (5, AUTO_APPROVED Crohn's) |
| Manifest | [`harness/manifests/iter-4.json`](../harness/manifests/iter-4.json) |
| Narrative | [`docs/ITERATIONS.md` iter-4 section](./ITERATIONS.md#iter-4-h2-second-entry) |

### chg-1 — H2 memory second entry (RA biologic after DMARD failure)

| Field | Value |
|---|---|
| Type | `new` |
| Constraint level | `long_term_memory` |
| Files | `src/pacca/agents/decision_support/long_term_memory.md`, `src/pacca/agents/prompts/templates.py` (PROMPT_REGISTRY DecisionSupportAgent v2.3 → v2.4), `tests/unit/test_h2_memory_criterion_preservation.py` (16 new tests, total H2 tests now 35) |
| Predicted fixes | — |
| Risk cases | `["GC-010", "GC-005", "GC-017", "GC-016"]` |
| Verified live | **All 4 scored 5 first-pass.** GC-010 IN_REVIEW via cost (memory cites cost-trigger explicitly); GC-005 IN_REVIEW with AAD-NPF cited (RA memory did NOT bleed into psoriasis); GC-017 IN_REVIEW with ACR PsA Guidelines cited (RA memory did NOT bleed into PsA); GC-016 AUTO_APPROVED with ACG cited (RA memory did NOT interfere) |

The cycle's second Phase H2 institutional memory entry. Follows the forward design notes from [`docs/findings/H2-memory-iteration-1.md`](./findings/H2-memory-iteration-1.md) literally: explicit status routing per anti-pattern (`**Status: IN_REVIEW.** (Not DENIED.)`), risk-case enumeration with live verification, criterion-preservation test extension, PROMPT_REGISTRY version bump.

The entry's most interesting design choice is the **explicit documentation of its non-override of iter-3 chg-1's `high_cost_check`**. The memory's "When the shortcut applies" section says auto-approval is "conditional on" the policy-level cost check; a separate "Important interaction with policy escalation" paragraph teaches the agent the right phrasing on cost-fired cases: "criteria met **but cost escalates per policy**" rather than "criteria met → approve." This is the cleanest possible test of the *memory as support, not replacement* contract from [`docs/findings/GC-001.md`](./findings/GC-001.md). GC-010 scored 5 with the judge praising the explicit policy-trigger reasoning.

iter-3 chg-2's first H2 entry needed a mid-iteration regression fix on GC-021. iter-4 chg-1's second entry needed zero mid-iteration debugging. The cycle's findings are compounding into prescriptive design notes that prevent the failure modes the next iteration would otherwise hit.

### chg-2 — Delete decision_agent.py (dead code, queued since iter-1)

| Field | Value |
|---|---|
| Type | `removal` |
| Constraint level | `tool_implementation` |
| Files | `src/pacca/agents/decision_agent.py` (deleted, 330 lines) |
| Predicted fixes | — |
| Risk cases | — |
| Verified live | Full suite 192 → 192 passed, unchanged from pre-deletion |

The file defined `DecisionSupportAgent` at line 52 but was never imported by any module — the orchestrator and all tests reference `DecisionAgent` in `decision.py` instead (which returns the string `"DecisionSupportAgent"` from its `.name` property via `PROMPT_REGISTRY`). Recorded as a deferred finding in [`harness/manifests/iter-1.json`](../harness/manifests/iter-1.json) chg-1's evidence block (*"Deletion queued as chg-2"*) and re-noted in every iter narrative since. iter-2 pivoted to eval-net work; iter-3 to H2 + escalation; iter-4 finally has the bandwidth for cleanup.

Zero importers confirmed pre-deletion (`grep -rn "from .decision_agent\|import decision_agent" --include="*.py" .` returned empty). All `DecisionSupportAgent` string references in the codebase are test assertions checking agent name return values or `PROMPT_REGISTRY` keys — none require the dead class. Post-deletion test suite unchanged.

The cleanest commit in the cycle — 330 lines deleted, no behavioral surface affected, no test impact.

### Iteration-level verdict and verdict on iter-3's 3 chgs

iter-4 closed at HEAD (final commit on `harness/iter-4` before merge). All gates green:
- Manifest validation: all 5 manifests pass (iter-0/1/2/3/4) against schema
- Doc-drift guard: PASSED
- Unit + harness suite: 208 passed in ~7s (192 baseline + 16 new H2 tests)
- Live clinical gate: aggregate 20/20 = 100% preserved (median of 2 rollouts; identical scores to iter-3-final; zero jitter across both rollouts)

**All three iter-3 changes verdict = `keep`** (recorded in [`harness/manifests/iter-4.json`](../harness/manifests/iter-4.json) `verdicts[]`):
- iter-3 chg-1 verified_fixes: `["GC-010", "GC-012"]` — both still routing correctly with the new memory entry active
- iter-3 chg-2 verified_risks: `["GC-001", "GC-021", "GC-022"]` — all three preserved with the second H2 entry active alongside; criterion-preservation tests for both entries pass
- iter-3 chg-3 — used to capture iter-4 baseline with `--rollouts 2`; distributions show zero jitter

iter-4's own chgs have no behavioral predicted_fixes. Risk-case preservation on chg-1 verified live. Verdicts on iter-4 chg-1 / chg-2 will land in iter-5.json's `verdicts` array.

---

<a name="iter-3-h2-and-escalation"></a>
## iter-3 — H2 Institutional Memory + Escalation-Branch Completion (3 changes; first behavioral iteration)

| Field | Value |
|-------|-------|
| Iteration tag | `harness-iter-3` |
| Date | 2026-05-24 |
| Author | David Reed |
| Base model | `claude-sonnet-4-5-20250929` |
| Constraint levels touched | `escalation_branch` (chg-1), `long_term_memory` (chg-2), `evaluation_harness` (chg-3) |
| Behavioral surface modified | YES — first behavioral iteration of the cycle |
| Changes | 3 |
| Live clinical gate at iter-3 HEAD | aggregate 20/20 = **100%** (median of 2 rollouts; 18 cases score 5, 2 score 4, 0 below 3) |
| Predicted fixes verified live | GC-010 (1 → 5), GC-012 (2 → 4) |
| Risk cases preserved | GC-001 (5 → 5), GC-021 (IN_REVIEW), GC-022 (IN_REVIEW) |
| Manifest | [`harness/manifests/iter-3.json`](../harness/manifests/iter-3.json) |
| Narrative | [`docs/ITERATIONS.md` iter-3 section](./ITERATIONS.md#iter-3-h2-and-escalation) |

### chg-1 — Wire HIGH_COST + PEDIATRIC_COMPLEX into ClinicalRiskDetector

| Field | Value |
|---|---|
| Type | `new` |
| Constraint level | `escalation_branch` |
| Files | `src/pacca/models/clinical.py`, `src/pacca/agents/clinical_risk_detector.py`, `src/pacca/py.typed`, `tests/unit/test_escalation_high_cost_and_pediatric.py`, `tests/clinical/baselines/iter-3-chg1-baseline.json`, `.pre-commit-config.yaml`, `src/pacca/config/tracing.py` |
| Predicted fixes | `["GC-010", "GC-012"]` |
| Risk cases | — |
| Verified live | **GC-010: 1 → 5** (judge cites cost-based escalation); **GC-012: 2 → 4** (judge cites pediatric complexity) |

Closes the SEV-2 findings from iter-2 chg-6 ([`docs/findings/GC-010.md`](./findings/GC-010.md), [`docs/findings/GC-012.md`](./findings/GC-012.md)). Both `EscalationReason` enum values existed in `src/pacca/models/enums.py` since before iter-1; check methods were missing. Hybrid data path: `ClinicalCase` gains three optional structured fields (`estimated_annual_cost`, `patient_age`, `disease_severity`) used when present, with regex parsers on `clinical_notes` as fallback. Cost parser uses **max of all dollar amounts** in the prose — a smoke-test on GC-010 caught a "first-match" bug before unit tests existed. Also adds `src/pacca/py.typed` (PEP 561 marker) which transitively surfaced pre-existing untyped code that was fixed inline; `pydantic-settings` added to the pre-commit mypy hook's `additional_dependencies`; structlog-style `logger.warning(event, detail=...)` calls in `tracing.py` marked with `# type: ignore[call-arg]` + TODO for future structlog migration.

### chg-2 — Phase H2 institutional memory — first entry (NSCLC pembrolizumab)

| Field | Value |
|---|---|
| Type | `new` |
| Constraint level | `long_term_memory` |
| Files | `src/pacca/agents/decision_support/long_term_memory.md` (new), `src/pacca/agents/_prompt_loader.py`, `src/pacca/agents/decision_support/system_prompt.md`, `src/pacca/agents/prompts/templates.py` (PROMPT_REGISTRY DecisionSupportAgent v2.2 → v2.3), `tests/unit/test_h2_memory_criterion_preservation.py` |
| Predicted fixes | — |
| Risk cases | `["GC-001", "GC-021", "GC-022"]` |
| Verified live | **GC-001: 5 → 5** (memory as support, all NCCN criteria still cited); **GC-021: IN_REVIEW + score 5** (after in-iteration wording fix; first-pass regressed to DENIED); **GC-022: IN_REVIEW + score 3** |

The cycle's first behavioral change at the `long_term_memory` constraint level. Per the iter-2 findings design constraints, the entry encodes the FULL criteria set for the NSCLC pembrolizumab pattern (six required criteria, five anti-patterns) so it does not compress away the discriminations that catch GC-021 (PD-L1 < 50%) and GC-022 (EGFR+). The `_prompt_loader.py` extension is backward-compatible: agents without a memory file (e.g. MedicalDirectorAgent) render byte-identical prompts via the `{% if long_term_memory %}` guard. The 19 criterion-preservation tests are this iteration's analog of iter-1's byte-identity check.

Mid-iteration debugging cycle (recorded in [`docs/findings/H2-memory-iteration-1.md`](./findings/H2-memory-iteration-1.md)): the first-pass memory wording said "Route to IN_REVIEW" five times but lacked an explicit IN_REVIEW-vs-DENIED boundary. The agent encountered GC-021 with TWO anti-patterns matched and generalized to `DENIED`. Fix: every anti-pattern now ends with `**Status: IN_REVIEW.** (Not DENIED.)` plus a "Why this distinction matters" paragraph. Re-run produced score `5`, judge text: *"demonstrates genuine case-by-case analysis rather than pattern-matching to a canonical approval case"* — exactly the H2 design contract. Methodology learning: memory writing is closer to prompt engineering than data engineering.

### chg-3 — regression_gate noise_threshold + capture_baseline --rollouts N

| Field | Value |
|---|---|
| Type | `improvement` |
| Constraint level | `evaluation_harness` |
| Files | `tests/clinical/regression_gate.py`, `tests/clinical/capture_baseline.py`, `tests/clinical/golden_cases.py`, `tests/clinical/evaluator.py`, `tests/harness/test_iter2_hardening.py`, `tests/clinical/baselines/iter-3-baseline.json` |
| Predicted fixes | — |
| Risk cases | — |
| Verified live | iter-3 baseline captured with `--rollouts 2`; distributions show **zero jitter** in this capture (every case identical across both rollouts) |

Closes the LLM-as-judge variance false-positive class observed twice in iter-3 chg-1 (GC-005 5→2 with identical agent behavior; GC-017 4→2 across runs). `check_regression` gains a `noise_threshold` parameter (default 0 strict) and `RegressionReport` gains a `jitter` list (drops within band recorded but non-blocking). `capture_baseline.py` gains `--rollouts N` (default 1); the saved baseline file gains an optional `distributions` field for the per-case score lists from multi-rollout runs. Production usage should set `noise_threshold=1` per the recommendation documented in `check_regression`'s docstring.

### Iteration-level verdict and verdict on iter-2's 6 chgs

iter-3 closed at HEAD `0d3342f..` (the final commit on the `harness/iter-3` branch before merge). All gates green:
- Manifest validation: all 4 manifests pass (iter-0, iter-1, iter-2, iter-3) against the schema
- Doc-drift guard: PASSED
- Unit + harness suite: 192 passed in ~7s (139 baseline + 53 new in iter-3)
- Live clinical gate: 3/3 selected tests pass; aggregate 20/20 = 100%
- Live baseline at iter-3 HEAD with `--rollouts 2`: zero jitter, 18 cases at 5, 2 cases at 4

**All six iter-2 changes verdict = `keep`** (recorded in [`harness/manifests/iter-3.json`](../harness/manifests/iter-3.json) `verdicts[]`). The substantive verdict is on iter-2 chg-6's predicted GC-001 fix (2 → ≥4 after stage IIIA → stage IV case-def repair): **verified at iter-3 HEAD with score 5**, and the case-def repair holds even with the chg-2 H2 memory active. The two SEV-2 findings recorded by iter-2 chg-6 (GC-010, GC-012) became iter-3 chg-1's predicted_fixes and both verified.

iter-3's own chgs have no behavioral predicted_fixes beyond chg-1's. Risk-case preservation on chg-2 (GC-001, GC-021, GC-022) verified live. Verdicts on iter-3's chg-2 and chg-3 will land in iter-4.json's `verdicts` array.

---

<a name="iter-2-eval-net-hardening"></a>
## iter-2 — Eval-Net Hardening (Phase H5 slice; 6 changes; no agent surface)

| Field | Value |
|-------|-------|
| Iteration tag | `harness-iter-2` |
| Date | 2026-05-22 (manifest) → 2026-05-24 (finalization) |
| Author | David Reed |
| Base model | `claude-sonnet-4-5-20250929` |
| Constraint levels touched | `evaluation_harness` (5 changes), `instrumentation` (1 change) |
| Behavioral surface modified | none |
| Changes | 6 (`chg-1` schema; `chg-2` regression gate; `chg-3` near-miss cases + gate wiring; `chg-4` doc-drift guard + reconciliation; `chg-5` model SSOT; `chg-6` diagnostic findings + GC-001 repair) |
| Live clinical gate at iter-2 HEAD | PASS (3 of 3 selected tests in 339.52s) |
| Baseline scoreboard | 18 of 20 = 90% pass after chg-6 GC-001 repair (was 17/20 = 85% pre-repair) |
| Manifest | [`harness/manifests/iter-2.json`](../harness/manifests/iter-2.json) (authoritative; full per-change structured data) |
| Narrative | [`docs/ITERATIONS.md` iter-2 section](./ITERATIONS.md#iter-2-eval-net-hardening) |

**Why six entries instead of one per change.** iter-2 is the cycle's first multi-change iteration. The cycle's "one logical change per commit" methodology calls for per-`chg-N` entries in this log; the compact form below preserves the per-change attribution while pointing at the JSON manifest for the full structured fields and at `ITERATIONS.md` for the narrative reasoning.

### chg-1 — Extend manifest schema's `type` and `constraint_level` enums

| Field | Value |
|---|---|
| Type | `improvement` |
| Constraint level | `evaluation_harness` |
| Files | `harness/manifests/change_manifest.schema.json` |
| Predicted fixes | — | Risk cases | — |

Adds `evaluation_harness` to `constraint_level` so Phase H5 measurement-apparatus changes (golden cases, judges, regression gates, drift guards) can validate against the schema. Adds `instrumentation` to BOTH the `type` enum and the `constraint_level` enum so H0 baseline crystallization (the tracing/audit scaffolding iter-0 actually shipped) validates against the schema. Both additions are non-behavioral and close schema gaps surfaced by iter-2's manifest-validation work. Same "broaden when iteration reality demands" pattern iter-1 used for the files-path regex (recorded in iter-1's narrative under "Schema evolution").

### chg-2 — Per-case regression gate + iter-1 baseline scoreboard

| Field | Value |
|---|---|
| Type | `new` |
| Constraint level | `evaluation_harness` |
| Files | `tests/clinical/regression_gate.py`, `tests/clinical/capture_baseline.py`, `tests/clinical/baselines/iter-1-baseline.json`, `tests/harness/test_iter2_hardening.py` |
| Predicted fixes | — | Risk cases | — |

Closes the silent-per-case-degradation gap. The pre-iter-2 clinical accuracy gate was absolute and aggregate: pass = score ≥ 3, gate = pass rate ≥ 80%. A case sliding 5 → 3 still counted as a pass; an over-aggressive H2 institutional-memory entry could erode reasoning quality on every case while keeping decisions correct, and the gate would stay green forever. `regression_gate.py` compares each case's current score to a baseline scoreboard and flags any drop — even when the aggregate stays green. The keystone test `test_CORE_catches_silent_degradation_the_aggregate_gate_misses` constructs a 20-case run where 19 cases are unchanged and GC-001 slides 5 → 3; the legacy aggregate gate would be 100% green, the new gate FAILs and names GC-001. The baseline scoreboard captured at iter-2 HEAD becomes the de-facto iter-1 reference (iter-2 introduces no behavioral change, so an iter-2-HEAD live run reflects iter-1's clinical surface). See chg-6 for the post-baseline diagnostic work this enabled.

### chg-3 — Near-miss memory-trap golden cases + clinical-gate wiring

| Field | Value |
|---|---|
| Type | `new` |
| Constraint level | `evaluation_harness` |
| Files | `tests/clinical/near_miss_cases.py`, `tests/clinical/test_clinical_accuracy.py` |
| Predicted fixes | — | Risk cases | — |

Closes the false-pattern-matching gap. Adds GC-021 (PD-L1 45% — below the 50% pembrolizumab threshold) and GC-022 (EGFR sensitizing mutation present) as siblings of GC-001 that must NOT auto-approve. An H2 institutional-memory entry that compresses "NSCLC + pembrolizumab → approve" would correctly fire on GC-001 (PD-L1 62%) but would incorrectly fire on GC-021 (PD-L1 45%, sub-threshold) and GC-022 (EGFR+, disqualifying). Pre-iter-2 the golden set had no sibling-of-canonical-approve case differing by exactly one disqualifier, so the trap was untestable. The cases live in their own `NEAR_MISS_CASES` list (kept disjoint from `GOLDEN_CASES` so the existing `test_dataset_has_twenty_cases` integrity assertion is preserved) and the clinical-gate loop iterates `GOLDEN_CASES + NEAR_MISS_CASES`. The live clinical gate at iter-2 HEAD passed (3 of 3 tests, 339.52s, aggregate ≥ 80%) which implies both near-miss cases routed correctly to IN_REVIEW (math: 22 total cases, 3 persistent golden-set failures, ≥80% gate ⇒ near-miss failure budget = 1, but the close pass margin strongly implies both were correct).

### chg-4 — Doc-drift guard + iter-0 trajectory.py reconciliation + HARNESS.md repoints

| Field | Value |
|---|---|
| Type | `new` |
| Constraint level | `evaluation_harness` |
| Files | `tests/harness/doc_drift_guard.py`, `tests/harness/test_iter2_hardening.py`, `docs/ITERATIONS.md`, `docs/DECISIONS.md`, `docs/HARNESS.md` |
| Predicted fixes | — | Risk cases | — |

Adds `doc_drift_guard.py` — a CI guard that fails on any `src/*.py` reference in `docs/` that doesn't resolve on disk. The append-only audit logs (DECISIONS.md, ITERATIONS.md) are excluded by default because their protocol preserves superseded references by design. The guard's first run found three drifts: the known one (iter-0's `src/pacca/observability/trajectory.py` references, since reconciled via the superseding [Correction (2026-05-22)](#correction-iter0-trajectory) entry above), and two more in HARNESS.md that iter-2 didn't know about (`orchestrator/escalation_tree.py` → repointed to `agents/orchestrator.py` class `Orchestrator`; `db/audit/schema.py` → repointed to `db/models.py` class `AuditLogModel`). All three drifts cleared; guard now passes.

### chg-5 — Model SSOT: AgentConfig reads from settings.default_model

| Field | Value |
|---|---|
| Type | `improvement` |
| Constraint level | `instrumentation` (no agent surface) |
| Files | `src/pacca/agents/base.py`, `src/pacca/config/settings.py` |
| Predicted fixes | — | Risk cases | — |

Reproducibility scaffolding for iter-3's measurement work. Pre-change, `AgentConfig.model` was a hardcoded string that silently overrode `settings.default_model` — agents ran one model while the iter-1 manifest's `base_model` field recorded another. Now `AgentConfig.model` derives from `settings.default_model` via `Field(default_factory=lambda: get_settings().default_model)`; override via `DEFAULT_MODEL` env. Manifests already agreed on `claude-sonnet-4-5-20250929`; this commit makes the runtime agree too. Constraint level is `instrumentation` (not a behavioral level) because there is no edit to any agent surface — this eliminates a three-way drift between configured, declared, and recorded model.

### chg-6 — Diagnostic findings from iter-1 baseline + GC-001 case-definition repair

| Field | Value |
|---|---|
| Type | `new` |
| Constraint level | `evaluation_harness` |
| Files | `docs/findings/{README, GC-001, GC-010, GC-012}.md`, `tests/clinical/golden_cases.py` (GC-001), `tests/clinical/baselines/iter-1-baseline.json` (GC-001 score updated), `tests/clinical/investigate_case.py` |
| Predicted fixes | `["GC-001"]` (predicted 2 → ≥4) |
| Verified live | **GC-001 flipped 2 → 5** (verified via re-running `investigate_case.py GC-001` after the case-definition repair) |
| Risk cases | — |

The chg-2 baseline-capture run surfaced three persistent per-case failures the aggregate ≥80% gate would have ignored. chg-6 root-causes each (full write-ups under [`docs/findings/`](./findings/)) and applies the only fix that fits at `evaluation_harness` scope:

- [**GC-010** (high-cost biologic)](./findings/GC-010.md) — SEV-2 agent-side bug: missing high-cost escalation branch. `branch_2_medical_director` was designed for this; in code, no component checks `HIGH_COST_THRESHOLD` (the $100K setting exists in `.env` but is never consulted on the decision path). **Deferred to iter-3** at `constraint_level: escalation_branch`.

- [**GC-012** (pediatric severe asthma)](./findings/GC-012.md) — SEV-2 agent-side bug, same class as GC-010: missing pediatric-complexity escalation branch. `COMPLEXITY_AUTO_APPROVE_MAX` and `COMPLEXITY_SPECIALIST_REVIEW_MIN` exist in `.env`; no code consults them. **Deferred to iter-3**; bundle with GC-010 fix into one `chg-` entry.

- [**GC-001** (canonical NSCLC clean approve)](./findings/GC-001.md) — SEV-3 test-data bug: `clinical_notes` say "stage IIIA" (locally advanced); `guidelines_context` cites "metastatic NSCLC" requirements. The agent correctly identified the contradiction and routed to `INFORMATION_NEEDED`; the judge penalized the agent for being right. Fixed in this chg by changing the case definition from "stage IIIA" to "stage IV (metastatic, M1c)". Verified live: GC-001 flipped 2 → 5 same day; the judge's reasoning text on the post-repair run explicitly cites the corrected stage.

Adds `investigate_case.py` as the per-case live-pipeline reproducer (parallel to `capture_baseline.py` but selecting one case and printing the full agent rationale + judge verdict for diagnostic reading). This is the tool every future case-level investigation will use.

**Why GC-010 and GC-012 are NOT fixed in iter-2.** Both require touching `ClinicalRiskDetector.evaluate()` and/or `decision_support/system_prompt.md` — agent-surface changes that would violate iter-2's "no behavioral change" charter and would conflate iter-2's eval-net hardening with bug fixes. The findings docs explicitly record this and constrain the iter-3 design: H2 institutional memory MUST NOT compress away the discriminations these fixes enforce. A memory entry like "RA + abatacept after DMARD failure → approve" must encode the cost guard explicitly.

### Iteration-level verdict

iter-2 is closed. The verdict on iter-1's chg-1 is finalized as `keep` (above, under chg-1's verdict block). iter-2's own chgs carry predicted_fixes only on chg-6 (`["GC-001"]`, verified live the same day). The remaining chgs (1, 2, 3, 4, 5) are non-behavioral; their verdicts will land in iter-3.json's `verdicts` array if any unforeseen interaction with iter-3's H2 work surfaces.

---

<a name="correction-iter0-trajectory"></a>
## Correction (2026-05-22) — iter-0 trajectory instrumentation record

| Field | Value |
|-------|-------|
| Supersedes | the iter-0 entry's references to `src/pacca/observability/trajectory.py` (Files row, baseline-metrics "Source" column, and the "Description" paragraph) |
| Date | 2026-05-22 |
| Author | David Reed |
| Iteration | recorded under iter-2 (`harness-iter-2`), chg-4 |
| Scope | documentation only; no code change |

**What the iter-0 entry claimed.** That iter-0 shipped trajectory instrumentation at `src/pacca/observability/trajectory.py`, emitting a per-step structured JSON record (input, tool calls, output, confidence, escalation decision), and that "Tokens per case (mean)" would be populated from `trajectory.py`.

**What actually shipped.** No `src/pacca/observability/` directory or `trajectory.py` file exists in the codebase. The instrumentation that shipped is **OpenTelemetry span emission in `src/pacca/agents/base.py`**: every agent LLM call opens a span recording `llm.input_tokens`, `llm.output_tokens`, `llm.total_tokens`, and `duration_ms` as span attributes, exported via the tracer configured in `src/pacca/config/tracing.py`.

**Corrected facts.**

1. The iter-0 Files row should read `src/pacca/agents/base.py` (OTel span instrumentation), not `src/pacca/observability/trajectory.py (new)`.
2. The baseline-metrics "Tokens per case (mean)" source is `base.py` span attributes (read via an in-memory span exporter or directly from `response.usage`), not `trajectory.py`.
3. There is **no per-step structured JSON trajectory record** and **no reasoning-step counter**. `DecisionAgent.run` (`src/pacca/agents/decision.py`) issues a single forced-tool-use call; the "evaluation framework steps" are reasoning *within one rationale*, not separate invocations. Any verbosity metric for later iterations is therefore defined as **output-tokens-per-case and/or rationale length**, never as a count of "steps."

**Why this supersedes rather than edits.** Per this log's protocol, the iter-0 entry is left intact; this entry is the authoritative record going forward. A dedicated per-case trajectory-record module remains deferred (candidate for the iter-3/H5 measurement work).

**Recurrence prevention.** A documentation drift guard (`tests/harness/doc_drift_guard.py`, shipped in iter-2 chg-4) now fails CI on any `src/*.py` reference in `docs/` that does not resolve on disk, so a doc can no longer claim a file that isn't there.

---

<a name="chg-1-iter-1"></a>
## chg-1 (iter-1) — Decision Support and Medical Director prompt extraction (Phase H1)

| Field | Value |
|-------|-------|
| Iteration tag | `harness-iter-1` |
| Merged commit | `a72249a` (merge of feature branch into main) |
| Date | 2026-05-04 |
| Author | David Reed |
| Base model | `claude-sonnet-4-20250514` |
| Constraint level | `system_prompt` (primary); also touched `pyproject.toml` and the manifest schema |
| Files (7) | `src/pacca/agents/_prompt_loader.py`, `src/pacca/agents/decision_support/system_prompt.md`, `src/pacca/agents/medical_director/system_prompt.md`, `src/pacca/agents/decision.py`, `pyproject.toml`, `harness/manifests/iter-1.json`, `harness/manifests/change_manifest.schema.json` |
| Type | `improvement` (refactor with no behavioral change predicted) |

**Description.** Extracted the agent-specific bodies of `DECISION_AGENT_SYSTEM` and `MEDICAL_DIRECTOR_AGENT_SYSTEM` from f-string constants in `prompts/templates.py` into file-level mount points at `src/pacca/agents/<agent>/system_prompt.md`. Added a Jinja2 loader (`_prompt_loader.py`) that assembles prompts at runtime from the .md file plus the shared components (`AGENT_IDENTITY`, `CLINICAL_SAFETY_GUIDELINES`, `OUTPUT_FORMAT_INSTRUCTIONS`) which remain canonical in `templates.py`. Wired `decision.py`'s `DecisionAgent` and `MedicalDirectorAgent` classes to use the loader. Reconciled three missing dependency declarations (`jinja2`, `python-jose`, `bcrypt`) in `pyproject.toml` after CI surfaced them. Broadened the schema's files-path pattern to allow repo-root config files after the same CI cycle revealed the iter-0 pattern was too strict.

**Failure pattern addressed.** No clinical or runtime failure pattern. This is the structural commit that establishes the file-level decoupling that Phases H2 and H3 require. The H1 success criterion is byte-identical prompt output, not a behavioral gain.

**Root cause.** Pre-H1, agent prompts lived as Python f-string constants inside `prompts/templates.py`, mixed with module-level interpolation logic. Edits to prompts produced diffs that mixed prompt-text changes with module-rendering changes. Phases H2 (Institutional Memory Layer) and H3 (Cross-Step Middleware Tier) require one-file-per-component diffs to attribute behavioral gains correctly; that attribution is impossible without H1 first.

**Predicted fixes.** None. iter-1 is a refactor; no clinical case is targeted.

**Risk cases.** None recorded in the manifest. The risk model is "any case where the rendered prompt differs by even one character from the pre-H1 baseline." This was preempted by a custom byte-identity check that compared the loader's output to the f-string output character-by-character before any runtime change. The check caught one bug — a missing blank line in the Decision Support `system_prompt.md` file — and the fix was a one-character correction. Both prompts confirmed byte-identical post-fix.

**Why this constraint level.** `system_prompt` is the level being decoupled. Tool descriptions, tool implementations, middleware, skills, and sub-agents remain unchanged. The choice to extract only system prompts in chg-1 keeps the scope narrow and makes the verification gate simple (byte-identity check on rendered prompt strings). The two collateral edits to `pyproject.toml` and the schema are not behavioral changes at the agent layer; they're correctness fixes that the chg-1 work surfaced.

**PHI impact.** None. No code path touching Protected Health Information was modified.

**Audit relevant.** No. Prompt versions tracked in `PROMPT_REGISTRY` remain unchanged across the refactor (still `v2.2`), so audit log entries from before and after this commit reconcile cleanly.

**Rollback plan.** `git revert <merge-sha>`. The orphaned `decision_agent.py` was not modified; `templates.py` was not modified beyond unused-imports cleanup. Reverting `decision.py` and removing the new directories restores the pre-H1 state exactly.

**Process notes from this iteration.** Three findings were observed during the work and recorded explicitly in the manifest. Two were deferred to future commits; one was bundled into chg-1 after CI made the case for it.

- *Deferred to chg-2:* `decision_agent.py` is dead code. Defines `DecisionSupportAgent(BaseAgent[DecisionOutput])` at line 52, but no module imports that class. The orchestrator and tests reference `decision.py`'s `DecisionAgent` instead. Deletion is queued for chg-2 with its own manifest entry.

- *Deferred indefinitely:* `decision.py` houses two tier-distinguished agents (Tier 1 Frontline Nurse, Tier 2 Medical Director) in one Python module. Both prompts were extracted to separate file mount points without restructuring the class layout. If a class-level split is later desired, that becomes its own iteration with its own attribution.

- *Originally deferred, then bundled into chg-1 after CI feedback:* `pyproject.toml` was missing declarations for `python-jose` and `bcrypt`, both of which `requirements.txt` declared. The first PR-CI run also revealed that `jinja2` (a new runtime dependency introduced by the loader in this very commit) was undeclared. All three were added to `pyproject.toml` in the same commit. The original "one logical change per commit" deferral was correct in principle but the CI run made the dependency surface visible enough that bundling all three reconciliations into chg-1 became the cleaner outcome. Methodology choice: when a constraint surfaces during execution that the original plan missed, fix it within scope rather than letting CI red ride.

- *Schema evolution:* the iter-0 schema's files-path pattern restricted entries to `^(src/pacca/|harness/|docs/|tests/)`. Adding `pyproject.toml` to the manifest's files list failed validation. The pattern was broadened to accept repo-root config files (`pyproject.toml`, `requirements*.txt`, `setup.py/cfg`, `Dockerfile`, `.gitignore`, `README.md`, `CHANGELOG.md`, `LICENSE`, `Makefile`) and CI workflows (`.github/`). Also generalized `src/pacca/` to `src/` since the project-specific prefix was unnecessary. The pattern broadening is itself in chg-1 because it was caused by chg-1.

### Verdict (recorded at iter-2 finalization, 2026-05-24; live-gate confirmation appended same day)

| Field | Value |
|-------|-------|
| Outcome | **keep** |
| Full-suite delta vs. iter-0 baseline | 0 — zero behavioral change, as predicted by the H1 refactor contract |
| Live clinical gate at iter-2 HEAD | **PASS** (3 of 3 selected tests in 339.52s; aggregate accuracy ≥ 80% under live LLM-as-judge) |
| Tokens-per-case delta | n/a (no agent-surface change; tokens-per-case attribution is an iter-3/H5 measurement work item) |
| Precision on predicted_fixes | n/a (empty — iter-1 predicted no fixes by design) |
| Recall on risk_cases | n/a (empty — iter-1 predicted no risks by design) |
| Verdict basis | (1) byte-identity verification character-by-character pre-merge, (2) 139/139 unit + harness tests pass at iter-2 HEAD, (3) live clinical gate PASSED, (4) doc-drift guard PASSED, (5) all three manifests validate against the schema |

**Narrative.** iter-1's success criterion was "byte-identical rendered prompts before and after the extraction" — the AHE paper's `paragraph_2 == paragraph_2` bar (Lin et al. §3.2). That criterion was met at commit time via the custom byte-identity check (`/tmp/byte_identity_check.py`) after a one-character fix to `decision_support/system_prompt.md`. iter-2 introduced no agent-surface change (all five iter-2 changes are at `instrumentation` or `evaluation_harness` constraint levels), so iter-2 cannot disturb iter-1's behavioral surface. The live clinical gate confirms this at the system level — 3 of 3 clinical-marked tests passing in 5m39s with `GOLDEN_CASES + NEAR_MISS_CASES` exercised end-to-end.

**Live-baseline scoreboard (the new authoritative iter-1 reference).** Captured at iter-2 HEAD via `capture_baseline.py` after the doc-drift run had been resolved and the API auth restored. Stored at `tests/clinical/baselines/iter-1-baseline.json`. Aggregate: **17 of 20 = 85% pass under the absolute ≥3 rule**, comfortably above the 80% gate.

| Case | Score | Note |
|---|---|---|
| GC-001 | 2 | persistent fail (canonical NSCLC PD-L1 62% auto-approve case — judge consistently scores the rationale as incomplete despite correct outcome) |
| GC-009 | 4 | unchanged from prior placeholder |
| GC-010 | 1 | **anti-pattern threshold crossed** (score 1 in this rubric = hallucination, wrong decision, or invented clinical detail per `evaluator.py` lines 70–76, 173, 177) — investigate before iter-3 |
| GC-012 | 2 | persistent fail (pediatric IN_REVIEW case — reasoning issues but not anti-pattern) |
| GC-017 | 4 | improved from prior placeholder (2 → 4); jitter or judge variance worth noting |
| All others | 5 | clean |

**Findings worth pausing on (recorded for iter-3 consideration).**

1. *GC-010 score-1 floor.* Per the evaluator rubric, score 1 is reserved for critically wrong outcomes — wrong decision, fabricated lab values, invented prior therapy. This is the most serious finding in the run. iter-3's H2 institutional-memory work could *mask* a hallucination by giving it a confident voice; the root cause should be understood before that risk is introduced.
2. *GC-001 canonical-approve scoring 2.* The cleanest case in the dataset returning a reasoning score below threshold. Implication for iter-3: H2 memory compression should target *stronger* rationales on clean approves, not shorter ones, since the judge already penalizes short reasoning here.
3. *GC-017 swing 2→4 across two runs.* LLM-as-judge non-determinism: this is the noise floor the per-case `regression_gate.py` shipped in iter-2 chg-2 must respect. Without a tolerance band, the next run could trip the gate on jitter alone — a known weakness recorded for an iter-3 (or iter-2-supplement) hardening pass.

These findings make the iter-1 baseline scoreboard a *non-trivial* reference for iter-3. The regression gate fires on *any* per-case drop today; with a hallucination already sitting at score 1, iter-3's H2 change must demonstrably leave GC-010 at or above score 1 (it cannot go lower) AND demonstrably not introduce *new* anti-pattern cases. The honest measurement frame for iter-3 is: "did H2 fix any of these, while introducing none?"

---

<a name="iter-0-baseline-crystallization"></a>
## iter-0 — Baseline Crystallization (seed entry)

| Field | Value |
|-------|-------|
| Iteration tag | `harness-iter-0` |
| Date | *(populated when tagged)* |
| Author | David Reed |
| Base model | claude-sonnet-4-20250514 |
| Constraint level | n/a (instrumentation only; no behavioral change) |
| Files | `src/pacca/observability/trajectory.py` (new), `harness/manifests/change_manifest.schema.json` (new), `docs/HARNESS.md` (new), `docs/DECISIONS.md` (new), `docs/ITERATIONS.md` (new), `CHANGELOG.md` (new), `README.md` (updated) |
| Type | `new` (cycle initialization) |

**Description.** Establish the baseline for the v2.3 Harness Engineering Cycle. Tag the v2.2.0 state as `harness-iter-0` and `pre-ahe-baseline`. Add structured trajectory logging. Commit the change-manifest JSON Schema. Add the four documentation files (HARNESS, DECISIONS, ITERATIONS, CHANGELOG). Update README to reference the methodology and link the documentation set.

**Why no behavioral change.** Phase H0's purpose per [PRD §15](../docs/PACCA_PRD_v2.3_Consolidated.md) is to crystallize the measurement infrastructure that subsequent phases iterate against. A behavioral change in iter-0 would contaminate every subsequent attribution, since we could not tell whether a gain came from the v2.3 cycle or from edits made before the cycle formally began. The minimal seed forces every component the cycle adds to earn its place against measured rollouts — the same reasoning the AHE paper applies to its NexAU₀ seed (Lin et al. §3.1).

### Baseline metrics on harness-iter-0

These are the reference numbers every subsequent iteration is measured against. Recorded immediately after tagging.

| Metric | Value | Source |
|--------|-------|--------|
| Unit test count | 140 passing, 0 failing | `pytest tests/` |
| Unit test wall time | ~8 seconds | `pytest tests/` |
| Demo dataset cases | 53 across 8 groups (A–H) | [PRD §19](../docs/PACCA_PRD_v2.3_Consolidated.md) |
| Clinical golden cases | 20 with LLM-as-judge scoring | [PRD §10](../docs/PACCA_PRD_v2.3_Consolidated.md) |
| pass@1 on unified benchmark | *to be populated after H5 unifies the case sources* | Phase H5 deliverable |
| Tokens per case (mean) | *to be populated after H0 instrumentation captures it* | trajectory.py |
| Hallucination zero-tolerance gate | passing on GC-018, GC-019 | `tests/test_clinical_accuracy.py` |
| 7-branch escalation tree coverage | all 7 branches exercised | `tests/test_escalation_tree.py` |

### Why these baseline numbers matter

Two operational consequences flow from recording these numbers explicitly:

First, every claim of improvement in iter-1 onward is a delta against this row. Vague claims like "this iteration improved decision quality" are not admissible — claims must reference one of these metrics or a metric added by a later phase.

Second, the AHE paper's regression-blindness finding ([HARNESS.md §4 Rule 3](./HARNESS.md)) means the eval suite is the safety net against regressions the manifest fails to predict. If the eval suite isn't catching regressions, the eval suite isn't comprehensive enough — Phase H5 expands it specifically to address this.

### iter-0 manifest entry (verbatim)

The full machine-readable manifest is at [`harness/manifests/iter-0.json`](../harness/manifests/iter-0.json) and validates against the schema. The relevant fields:

```json
{
  "iteration": 0,
  "iteration_tag": "harness-iter-0",
  "iso_date": "2026-05-XX",
  "author": "David Reed",
  "base_model": "claude-sonnet-4-20250514",
  "previous_iteration_tag": null,
  "summary": "Crystallize v2.2.0 as the iteration anchor. No behavioral change. Add trajectory instrumentation, manifest schema, and the four documentation files. The minimal seed forces every component the cycle adds to earn its place against measured rollouts.",
  "changes": []
}
```

The `changes` array is empty by design. iter-0 ships infrastructure, not behavior; behavioral changes begin with iter-1's `chg-N:` commits.

### Verdict

n/a — iter-0 has no predictions to verify. The iter-1 evaluation produces baseline-reproduction confirmation only: full suite passes, no regression against the recorded numbers above.

---

## Format reference

Each entry follows this structure:

1. **Header table** — iteration tag, date, author, base model, constraint level, files touched, change type
2. **Description** — one paragraph; what changed
3. **Failure pattern addressed** — phrased as a class, not a single case
4. **Root cause** — why the failure occurs (distinct from the symptom)
5. **Predicted fixes** — case IDs expected to flip from failing to passing
6. **Risk cases** — case IDs at risk of regressing
7. **Why this constraint level** — the engineering decision is *which* level to edit; the rationale lives here
8. **PHI impact** — none / indirect / direct (PACCA-specific field)
9. **Audit relevant** — yes / no (PACCA-specific field; gates whether the change must appear in the immutable PolicyChangeLogEntry per [PRD §14](../docs/PACCA_PRD_v2.3_Consolidated.md))
10. **Rollback plan** — how to revert if the verdict rejects
11. **Verdict** — added after the next iteration's eval; `keep` / `improve` / `rollback` plus precision/recall numbers

The schema at [`harness/manifests/change_manifest.schema.json`](../harness/manifests/change_manifest.schema.json) is the authoritative specification; this prose summary is for human readers.

## On honest reporting

The AHE paper's empirical finding on self-attribution (Lin et al. §4.4.2) is that fix predictions are reliable (~5x random precision and recall) but regression predictions are barely above random (~2x). When a verdict in this document shows a missed regression, that is not a failure of the methodology — it is the methodology working as designed. The honest verdict, including the misses, is what makes the log useful.

This is also what makes the log defensible. A decision log that records only successes is a marketing document; a decision log that records the misses alongside the hits is an engineering record. PACCA's choice is the latter.

---

*This file is updated on every `chg-N:` commit and on every iteration boundary. It is part of PACCA's harness engineering documentation set; see [`docs/HARNESS.md`](./HARNESS.md) for the methodology and [`docs/ITERATIONS.md`](./ITERATIONS.md) for the narrative log.*
