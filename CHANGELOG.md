# Changelog

All notable changes to PACCA are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html). PACCA follows a two-axis versioning scheme: a SemVer release (`vMAJOR.MINOR.PATCH`) for the codebase, and a parallel `harness-iter-N` tag for behavioral changes shipped under the harness-engineering discipline introduced in v2.3. The two are coupled — every harness iteration ships from a tagged release — but they record different things: SemVer captures the codebase, harness tags capture the *attribution* of each behavioral change to a specific component-level diff.

> **Note on 2.4–2.6.** The changelog went unmaintained between 2.3.0 and 2.6; those
> three entries were reconstructed from commit and PR history on 2026-07-25 and are
> grouped by theme rather than by a release-day cut. Where a claim mattered it was
> checked against the working tree, not the commit subject.

---

## [Unreleased]

### Fixed — audit corrections

- **The RAG pipeline actually runs.** `pacca.rag.pipeline` could not be imported:
  `models/guidelines.py` did `from uuid7 import uuid7` (the distribution installs its
  module as `uuid_extensions`) and referenced `ClinicalSpecialty` /
  `TreatmentCategory`, which did not exist. `integrations/vector_store.py` caught that
  in a bare `except` and latched a module-level fallback, so every deployment ran the
  degraded path permanently and silently. The import chain is repaired, the failure is
  logged at error level, and `pipeline_available` is inspectable.
- **No phantom collection.** `GuidelineVectorStore` no longer opens a
  `clinical_guidelines` collection on a second ChromaDB client — a store outside the
  P-4 scope guard and outside the test seam. `GuidelineRetriever` injects the governed
  collection it already holds, so the pipeline runs over `nccn_guidelines`.
- **Metadata filters that filter.** Specialty and category filtering used `$contains`,
  a `where_document` operator ChromaDB accepts on metadata but never satisfies —
  returning zero rows, masked by an unfiltered retry. Ingest now writes per-value
  boolean flags and queries match on those. Similarity is derived from the collection's
  real distance space rather than assuming cosine.
- **`tests/test_level5_flow.py` collects.** Its `TestRetriever` subclass reimplemented
  `__init__` with attribute names the real class never had, so every inherited method
  raised `AttributeError` — 3 collection errors on every run. `tests/conftest.py`
  meant to exclude the file but wrote `collect_ignore` paths relative to the rootdir
  when they resolve relative to the conftest, so nothing was excluded. The subclass is
  gone (the retriever takes `db_path`), and the module is marked `clinical`.
- **SECRET_KEY validation cannot be bypassed.** The startup check ran only when
  `APP_ENV != "test"` — a production deployment could skip signing-key validation with
  one environment variable. It now runs everywhere, with `APP_ENV` selecting
  strictness. A key must also carry 8 distinct characters (`"x" * 64` passed before),
  and in production/staging a published placeholder marker is rejected. The unread
  `Settings.secret_key` field, which shipped a weak default, is deleted.
- **The Medical Director queue reads real data.** `frontend/src/components/DirectorQueue.tsx`
  rendered a hardcoded one-element `DEMO_QUEUE`, and no endpoint existed to ask.
  `GET /api/v1/authorizations/review-queue` now serves persisted escalated decisions
  behind `verify_token`, and the component renders them through a typed client with
  loading, error and empty states.

### Changed

- **CI runs the whole non-billable suite.** The `test` job ran `pytest tests/unit`
  only, so `tests/harness`, `tests/integration` and the level-5 flow's collection
  errors were invisible — the badge could be green while `make test-all` was red. Its
  scope now matches `make test-all` exactly.
- **`make test-clinical` selects by marker** (`pytest tests/ -m clinical`), not by
  directory. Scoping to `tests/clinical/` would have left the newly-marked
  `tests/test_level5_flow.py` running in no target at all.
- **Overriding a case requires a typed rationale.** The Director queue previously sent
  a canned paragraph written for one demo case, which would have been attached as
  clinical justification to whatever real case sat under the button.

### Removed

- **The "Confirm denial" button.** It called no endpoint — it filtered the row out of
  local state and nothing else, so it looked like a disposition and was not one. The
  queue now states, from `ReviewQueueResponse.resolution_supported`, that case
  resolution is unimplemented.

---

## [2.6.0] — 2026-07-24

### Added — Governance rollout P-0 → P-6

- **Per-run `IntentRecord`** (`models/intent.py`, chg-7) — a typed, record-only
  contract naming a run's allowed collections and actions, emitted as the first audit
  event (`intent.declared`).
- **Minimum-necessary scope guard** (`agents/scope_guard.py`, chg-8 → chg-9) — a
  fail-closed call-site wrapper that denies out-of-scope tool/DB/RAG calls against the
  `IntentRecord`, wired into the submit route in enforce mode at the two
  identifier-checked DB writes and the RAG query.
- **Runtime evidence-grounding detector** (`agents/evidence_grounding.py`, chg-10) —
  promotes the GC-018/019 anti-hallucination guard from an eval-time check to a runtime
  one: a decision citing an evidence id absent from the submission is forced to human
  review.
- **`case_precedents` fully wired and governed** — Medical Director overrides written by
  `/feedback` are retrieved on submit under a "PAST MEDICAL DIRECTOR DECISIONS" header
  the DecisionAgent prompt weighs. `RAG_COLLECTIONS` is the SSOT the `IntentRecord`
  allow-list mirrors, with a drift test holding the two in sync.
- **Manifest validator CLI** — `python -m pacca.harness.validate_manifest --all`, run by
  the `validate-manifests` CI job on every PR.
- **Doc-drift guard** (`tests/harness/doc_drift_guard.py`) running inside `tests/unit`.
- **`clinical-gate` CI job** — GC-018/019 plus golden-set accuracy on `chg-`/agent-RAG
  PRs and nightly. Inert until the `ANTHROPIC_API_KEY` repo secret exists; blocking
  only once branch protection requires it.
- **Real-Postgres integration tier** (`tests/integration/test_submit_postgres.py`,
  marked `postgres`) guarding the B2/B3 persistence bugs. Skips unless
  `POSTGRES_TEST_URL` is set — `make test-postgres`.
- **Recruiter-facing README + `docs/ENGINEERING.md`**, regenerated figures, and the
  consolidated v2.6 PRD.

### Fixed — persistence layer (B1–B6)

- **B6: the LLM no longer mints a unique business key** (chg-11 / chg-12).
  `decision_id` came out of the model's forced tool-use output and was persisted into a
  `unique=True` column, so resubmitting the same case 500'd on an `IntegrityError` — and
  the missing rollback masked it behind a `PendingRollbackError`. The id is minted
  server-side; the failure path rolls back and logs.
- **B1–B5** — the dual-database split for auth, JSONB-on-SQLite, orphaned audit writes
  against absent parent rows, and the structured-logging calls on stdlib loggers that
  turned a recoverable RAG-init failure into a hard 500.
- **`AuthorizationRepository.create` / `DecisionRepository.create`** repaired — they
  referenced fields the models did not have and were never called.

### Changed

- **`ruff` and `mypy` pinned** to match pre-commit, so an unpinned upgrade cannot break
  CI independently of a code change.

---

## [2.5.0] — 2026-07-20

### Added

- **Demo package** — script, clinical cases, and visual track, framed around the
  Foundation Partner clinical review board.
- **PRD v2.5** covering the 105-case dataset state, with the docs set reconciled
  against it.

### Changed

- **Frontend build migrated to the Tailwind v4 PostCSS setup.**
- **Dev-proxy backend target is env-configurable**, so the frontend is no longer pinned
  to a localhost backend.

---

## [2.4.0] — 2026-06-06

### Added

- **Triage agents for audit and routing** (`evidence_agent.py`,
  `classification_agent.py`), deliberately decoupled from the decision so a triage
  change cannot move a clinical outcome.
- **Runtime-tunable decision thresholds**, reconciling confidence routing with the
  escalation tree.
- **`docs/ENGINEERING_DECISIONS.md`** — the ADR rationale record for the threshold and
  triage work.
- **Oncology breadth cases GC-101 → GC-105** (100 → 105 golden cases).
- **`CLAUDE.md`** — machine-facing project context for coding agents.

### Changed

- **structlog migration** (iter-6), plus an adult-complexity pre-flight check and the
  first deny-path H2 decision entry.

### Fixed

- **Auth users table created in the async DB the handlers actually query** — `register`
  and `login` were querying a table created on a different engine.
- **`env_file` anchored to the repo root**, so the working directory cannot pull in
  looser thresholds.
- **Misleading `escalation_reason` dropped from auto-approve audit records.**

---

## [2.3.0] — 2026-05-09

### Added — Harness engineering methodology

- **Eleven editable harness surfaces** documented in [`docs/HARNESS.md`](docs/HARNESS.md): seven AHE-standard component types (system_prompt, tool_description, tool_implementation, long_term_memory, middleware, orchestrator, eval_suite) plus four PACCA-specific surfaces for healthcare governance.
- **Change-manifest contract** at [`harness/manifests/change_manifest.schema.json`](harness/manifests/change_manifest.schema.json). JSON Schema 2020-12. Every behavioral change ships with a manifest entry: predicted impact, root cause, evidence, rollback plan, and PACCA-specific `phi_impact` / `audit_relevant` fields.
- **Append-only decision log** at [`docs/DECISIONS.md`](docs/DECISIONS.md) — every iteration's predicted-vs-observed verdict, ratified or reverted at file granularity.
- **Iteration narrative log** at [`docs/ITERATIONS.md`](docs/ITERATIONS.md) following the AHE paper's Appendix C format: failure pattern → change → trajectory before/after → eval delta.
- **Phase H0 instrumentation baseline** — OpenTelemetry tracer, trajectory logger, correlation-ID propagation through the orchestrator. Tagged `harness-iter-0`, manifest at [`harness/manifests/iter-0.json`](harness/manifests/iter-0.json).
- **Phase H1 first extraction** — Decision Support and Medical Director system prompts moved from f-string constants to file-mount points at `src/pacca/agents/<agent>/system_prompt.md`. Jinja2 loader assembles prompts at runtime. Tagged `harness-iter-1`, manifest at [`harness/manifests/iter-1.json`](harness/manifests/iter-1.json).
- **Manifest validator** — `python -m pacca.harness.validate_manifest harness/manifests/iter-N.json`. Wired into CI.
- **Stub for the consolidated v2.3 PRD** at [`docs/PACCA_PRD_v2.3_Consolidated.md`](docs/PACCA_PRD_v2.3_Consolidated.md). Section §15 (the cycle phases H0–H5) is in active drafting; the canonical source is `HARNESS.md` plus the per-iteration manifests.
- **Stub for the consolidated evaluation document** at [`docs/EVALUATION.md`](docs/EVALUATION.md). Full unification ships in Phase H5.

### Changed — Lint and CI hygiene

- **Ruff configuration tightened** in `pyproject.toml`: noisy rules suppressed with documented rationale (PLC0415 lazy-import pattern, RUF001/RUF002 typography in clinical prompts, PTH cosmetic, ARG interface conformance, ERA documented future-work markers, PLW global-state patterns). FastAPI dependency-injection markers (`Depends`, `Query`, etc.) registered as immutable calls so they no longer trigger B008.
- **All lint and type-check errors resolved** — repository now passes `ruff check src/ tests/` cleanly. Previous CI runs failed on 389 ruff errors; this release green-lights the lint job.
- **Hand-fixed code-quality items** — exception chaining (`raise … from err`/`from None`) on JWT auth and authorization-route error paths; `contextlib.suppress` replacing bare `try/except/pass` in test fixtures; combined `with` statements; ambiguous variable name (`l` → `lab`) in evidence-aggregation lab-result rendering.

### Changed — Repository-level

- **Real GitHub URLs** in `pyproject.toml` (`yourusername` placeholders replaced with the actual repository path).
- **Issue templates rewritten** for PACCA — bug template captures component, severity, environment, and a P0–P3 self-assessment; feature-request template asks for the harness constraint level and a predicted-impact contract.
- **CHANGELOG restructured** to a two-axis SemVer + harness-iter narrative.

### Removed

- **Stale scratch files** — `initial_file.txt` deleted; `upgrade_to_level5.sh` moved into `scripts/`.

### Notes

This release pairs the v2.2 functional release (multi-agent orchestration, dual-collection RAG, escalation tree, JWT auth, observability) with the v2.3 methodological release (harness engineering, change-manifest discipline, iteration record). The codebase is a portfolio and evaluation artifact — not HIPAA-certified, ships with synthetic data only. See [`SECURITY.md`](SECURITY.md) for production-deployment obligations.

---

## [2.2.0] — 2026-04-04

### Added

- **End-to-end multi-agent pipeline** — Evidence Aggregation, Classification, Decision Support (Tier 1), Medical Director (Tier 2), Policy Evolution (Governance).
- **7-branch escalation tree** with four pre-flight deterministic checks (experimental treatment, rare condition, conflicting guidelines, prior denial) and three post-agent escalation paths.
- **Dual-collection ChromaDB RAG** — `nccn_guidelines` (authoritative, quarterly updates) and `case_precedents` (institutional memory from Medical Director overrides).
- **Eight production-grade safety properties** documented in the README, each unit-tested.
- **Admin Dashboard** for runtime configuration and policy-proposal review.
- **Demo dataset** — 53 synthesized cases across 8 groups (A–H) covering all 7 escalation branches plus a 20-case clinical golden set.
- **Comprehensive PRD and SDD** — [`docs/PACCA_PRD_Consolidated.md`](docs/PACCA_PRD_Consolidated.md), [`docs/PACCA_SDD_v2.2.md`](docs/PACCA_SDD_v2.2.md).

### Changed

- **PRD evaluation score** raised from 2.70 / 5.0 to 5.0 / 5.0 across the 6-week sprint.
- **README** rewritten with "Why This Exists", updated architecture diagram, and Level 5 maturity framing.
- **Architecture diagram** replaced with an approved SVG ([`docs/assets/architecture_v2.2.svg`](docs/assets/architecture_v2.2.svg)).

---

## [2.1.6] — 2026-04-04 — Week 6: Security hardening + async consolidation + RAG pipeline

### Changed

- **`api/auth.py`** — security hardening rewrite. `SECRET_KEY` loaded from environment with a fail-fast `validate_secret_key()` at startup; server refuses to boot with a key shorter than 32 characters. JWT issuance and verification audited end-to-end.
- **Async consolidation across the API and DB layers** — eliminated mixed sync/async paths that surfaced as flake under load.
- **RAG pipeline** — guideline ingestion, embedding, and retrieval consolidated under a single repository pattern.

---

## Earlier history

PACCA's pre-v2.1.6 history (initial commits, JWT login routing fix, Admin Dashboard, end-to-end pipeline) is preserved in the git log. The repository's first commit was [`88332af`](https://github.com/drdgreed/pacca/commit/88332af) on 2026-02-02.

---

[Unreleased]: https://github.com/drdgreed/pacca/compare/v2.3.0...HEAD
[2.3.0]: https://github.com/drdgreed/pacca/releases/tag/v2.3.0
[2.2.0]: https://github.com/drdgreed/pacca/releases/tag/v2.2.0
[2.1.6]: https://github.com/drdgreed/pacca/compare/v2.1.6...v2.2.0
