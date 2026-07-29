<!--
PACCA PR template. Every PR is one of two paths — pick the path that fits and
keep only the relevant block. The harness-engineering path applies to anything
that changes how an agent reasons, what tools it can call, what middleware
fires, or what memory context it sees. Everything else is the standard path.

See docs/HARNESS.md for the constraint levels and harness/manifests/change_manifest.schema.json
for the manifest contract.
-->

## Path

- [ ] **Standard PR** — refactor, docs, infra, build, test addition, dependency bump, non-behavioral fix.
- [ ] **Behavioral PR (harness-engineering discipline)** — modifies agent reasoning, tool surface, memory, middleware, or eval suite. Requires a manifest entry under `harness/manifests/iter-N.json`.

---

## Summary

One paragraph on what this PR does and why.

## What changed

- File-level bullet list. Be specific.

## Verification

What you ran and what passed:

- [ ] `ruff check src/ tests/` clean
- [ ] `ruff format --check src/ tests/` clean
- [ ] `pytest tests/unit` passes
- [ ] `pytest tests/integration` passes (if integration paths touched)
- [ ] `pytest tests/clinical` passes ≥80% LLM-as-judge gate (if agent prompts or RAG touched)
- [ ] Manual smoke test in browser at `http://localhost:3000` (if frontend touched)
- [ ] `docker compose up -d` smoke test (if infra touched)

## Risk

What could break? PHI exposure, audit-trail impact, dependency footprint, autonomy-boundary change, breakage of existing eval cases. If "none," say so explicitly.

## Rollback plan

If this lands and something is wrong, what's the revert path? `git revert <sha>` is fine for most PRs; non-trivial cases (DB migrations, data backfills) need a real plan.

---

<!-- ===================== Behavioral-PR section ===================== -->
<!-- Delete this block if you ticked Standard PR above. -->

## Harness manifest entry

Path to the manifest entry: `harness/manifests/iter-N.json`

- **Iteration tag:** `harness-iter-N`
- **Constraint level:** `<system_prompt | long_term_memory | orchestrator | prompt_registry | audit_schema | eval_suite>`
  <!-- These are the harness surfaces that actually exist today (see docs/HARNESS.md).
       tool_description / tool_implementation / middleware / skill / sub_agent / rag_collection
       are roadmap; a change that instantiates one of those component types for the first
       time is itself notable — call it out explicitly here rather than picking a stand-in. -->

- **Failure pattern targeted:** one sentence
- **Baseline claim:** required if this change is `improvement` or `rollback` at
  iteration >= 18 (D6, forward-only cutover — see docs/HARNESS.md). What did the system do
  BEFORE this change, how did you verify it (a reproducible command or procedure), and what
  did that verification produce? Mechanises AGENT_LESSONS.md P-013 — a claim about existing
  behavior must be measured, not asserted. `new`/`instrumentation` changes, and any change at
  iteration < 18, are exempt.
- **Predicted impact:** what should the next eval round show that the previous one did not?
- **PHI impact:** `none | logged | persisted`
- **Audit-relevant:** `true | false`
- **Rollback granularity:** `single file | multi-file` — if multi-file, justify

## Predicted-vs-observed contract

What observation in the post-merge eval round would (a) ratify this change, (b) revert it? Be specific. "Vibe check" is not a contract.
