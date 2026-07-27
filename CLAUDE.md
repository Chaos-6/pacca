# CLAUDE.md — PACCA Agent Context

Project memory for any coding agent (Claude Code or otherwise) working in this repo.
Read this before making changes. It is the machine-facing counterpart to `CONTRIBUTING.md`.

> **Docs describe reality, not aspiration.** This file was reconciled against the
> working tree (2026-07-21). Where the intended design is not yet built, it is called
> out in **Limitations** or **Target architecture (roadmap)** below rather than stated
> as fact. If you find a claim here that the code contradicts, the code wins — fix the
> doc in the same PR. A doc-drift guard (`tests/harness/doc_drift_guard.py`) exists to
> catch this class of drift; wiring it into CI and widening its scope to this file is
> harness change P-1.

## What PACCA is

PACCA (Prior Authorization & Care Coordination Agent Platform) is a multi-agent AI
system that automates healthcare prior-authorization decisions. It is RAG-grounded,
harness-engineered, and observability-first. Healthcare domain: PHI handling and audit
integrity are non-negotiable, not nice-to-haves.

Stack: Python 3.12 + FastAPI (async), Pydantic v2, PostgreSQL 16 / SQLite (dev),
ChromaDB (**dual-collection**: `nccn_guidelines` + `case_precedents`, via the live
`integrations/vector_store.py` — see the RAG note below), React 18 + Vite frontend,
OpenTelemetry spans (Langfuse export intended), Claude (`claude-sonnet-4`) with
tool-use forced for structured output.

## The one rule that governs every behavioral change

**Every change that alters agent behavior ships as a one-file diff with a recorded,
falsifiable prediction.** Behavioral = how an agent reasons, what tools it can call,
what middleware fires, or what memory context it sees. This discipline is the point of
the project; do not bypass it to "save time."

Before a behavioral change:
1. Read `docs/HARNESS.md` and identify the correct constraint level (one of the
   currently-built harness surfaces — see the PR template enum for the canonical list).
2. Make the change as a one-file diff. If multiple components are touched, use multiple
   commits — one file per commit.
3. Use the `chg-N:` commit prefix for behavioral changes (conventional commits otherwise:
   `feat:`, `fix:`, `docs:`, `refactor:`, `test:`).
4. Add a matching entry to `harness/manifests/iter-N.json` per
   `harness/manifests/change_manifest.schema.json`. Include `phi_impact` and
   `audit_relevant` fields — they are required for healthcare governance.
5. Record a verdict in `docs/DECISIONS.md` at the next evaluation round.

> **Enforcement status (P-6 landed).** CI now runs a **`validate-manifests`** job
> (`python -m pacca.harness.validate_manifest --all`, every PR) and a **`clinical-gate`**
> job (GC-018/019 + golden-set accuracy via `make test-clinical`, on `chg-`/agent-rag PRs
> and nightly). The PR template still forces the Standard-vs-Behavioral choice. **Two
> things remain David's manual steps (CI cannot self-impose them):** (1) add the
> `ANTHROPIC_API_KEY` repo secret — the clinical-gate is inert until then; (2) enable
> branch protection requiring `validate-manifests` + `test` + `clinical-gate`, which is
> what makes these checks *blocking* rather than advisory. Until both are done, a missing
> manifest fails its job but can still be merged past.

Non-behavioral changes (refactors, docs, test additions that don't change behavior)
follow the standard PR flow and skip the manifest. The PR template forces the choice —
every PR is one path or the other, never ambiguous.

## Where things live (don't reorganize without reason)

- `src/pacca/agents/` — the agents. **As built**, two agents have their own directory:
  `decision_support/` (`system_prompt.md` + `long_term_memory.md`) and `medical_director/`
  (`system_prompt.md`). The other three are flat modules: `evidence_agent.py`,
  `classification_agent.py`, and `evolution.py`. Wiring is by direct
  Python import, not an `agent.yaml` loader. The per-agent seven-component layout
  (`tool_descriptions/`, `tools/`, `middleware/`, `agent.yaml`) is **roadmap** — see below.
- `src/pacca/agents/prompts/templates.py` — shared `PROMPT_REGISTRY`. Prompts are
  versioned (`v{MAJOR}.{MINOR}`, surfaced to audit logs and OTel spans). Register a
  prompt; don't hardcode one at a call site.
- `src/pacca/agents/orchestrator.py` (class `Orchestrator`) — the 7-branch escalation
  tree (4 pre-flight deterministic checks + 3 post-agent). This logic OVERRIDES model
  confidence. Treat it as a safety boundary, not a suggestion.
- `src/pacca/models/intent.py` — the per-run **`IntentRecord`** (governance rollout P-3):
  a typed, record-only contract (allowed collections/actions, opaque subject_ref,
  expected effects) that the submit route emits as the FIRST audit event
  (`intent.declared`). It is the SSOT for a run's declared scope; P-4/P-5 read it.
- `src/pacca/agents/scope_guard.py` — the **minimum-necessary scope guard** (P-4):
  `enforce_scope(intent, action, **call_args)`, a fail-closed call-site *wrapper*
  (there is no middleware loader) that denies out-of-scope tool/DB/RAG calls against
  the `IntentRecord` and raises `ScopeViolation` → `EscalationReason.SCOPE_VIOLATION`.
  **As built (chg-8 → chg-9):** wired into the submit route in **enforce** mode at three
  sites — the two identifier-checked DB writes (`db.write_request`, `db.write_decision`)
  and the RAG query. A cross-case leak fail-closes to human review. In correct operation
  the run always passes its own scope, so it does not deny in normal flow — its value is
  fail-closed defense against a leak/bug. Mode is `settings.scope_guard_mode`.
- `src/pacca/integrations/vector_store.py` — `GuidelineRetriever`, the **live**
  RAG interface. It is **dual-collection and built**: `nccn_guidelines` (authoritative)
  and `case_precedents` (institutional memory — Medical Director overrides written by
  `/feedback`, retrieved on submit under a "PAST MEDICAL DIRECTOR DECISIONS" header the
  DecisionAgent prompt weighs). Both collections are governed by the P-4 scope guard
  (iter-13): `RAG_COLLECTIONS` is the SSOT the `IntentRecord` allow-list mirrors.
- `src/pacca/rag/pipeline.py` — the OLD single-collection `GuidelineVectorStore`
  (`clinical_guidelines`). **Dead code:** it does not import cleanly (stale references in
  `models/guidelines.py`) and the live path (`integrations/vector_store.py`) falls back
  past it. Revival or removal is a tracked follow-up; do not add to it.
- Span emission lives in `src/pacca/agents/base.py` + `src/pacca/config/tracing.py`
  (one span per agent call). There is **no** `src/pacca/observability/` package.
- `src/pacca/api/`, `src/pacca/db/`, `src/pacca/models/`, `src/pacca/config/` — standard.
- `src/pacca/db/migrations/` — Alembic migrations are the **single source of truth** for
  the schema (C5). The app no longer calls `create_all` at startup; `docker-entrypoint.sh`
  runs `alembic upgrade head` before uvicorn (run `make db-upgrade` for local dev). `env.py`
  combines BOTH declarative Bases' metadata (`db.models.Base` + `api.database.Base`) so
  `users` is migration-covered (migration 004). A `migration-drift` CI job asserts models ≡
  migrations on Postgres. Don't reintroduce `create_all` on a runtime path.
- `.githooks/pacca_guard.py` — the **PHI/secret pre-commit guard** (wired via
  `.pre-commit-config.yaml`, reusing `sme_authoring/validators.py` as SSOT, tested in
  `tests/unit/test_pacca_guard_hook.py`). This is PACCA's strongest existing example of a
  deterministic commit-time gate — the pattern to imitate when adding enforcement.
- `harness/manifests/` — change manifests + verdicts. The decision record of iteration.
- `docs/` — `ARCHITECTURE.md`, `HARNESS.md`, `DECISIONS.md`, `ITERATIONS.md`,
  `EVALUATION.md`, consolidated PRD.

## Safety invariants — never weaken these

- **Anti-hallucination guards.** Agents may only reference clinical evidence explicitly
  present in the submission. Golden cases **GC-018** and **GC-019** (in
  `tests/clinical/golden_cases.py`) assert zero score-1 hallucination. As of P-6 the
  `clinical-gate` CI job runs these (on `chg-`/agent-rag PRs and nightly) — so they fail
  the *job*; making a job failure *block merge* is David's branch-protection step. P-5
  (chg-10) also promotes this guard to a **runtime** detector (`evidence_grounding.py`):
  a decision citing an evidence id absent from the submission is forced to human review.
- **Tool-use forced** for structured output. Don't switch an agent to free-text parsing.
- **Pre-write audit trail.** Correlation-ID-linked event pairs (`AuditLogModel` carries
  `correlation_id`) are flushed BEFORE any state change; `tests/unit/test_audit_trail.py`
  guards the ordering. Don't reorder writes ahead of the audit flush.
- **Append-only policy change log.** The intent is that policy changes are never mutated
  or deleted. **As built** this is a prototype: `PolicyChangeLogEntry` (in
  `agents/evolution.py`) is an in-memory dataclass list, not a DB table
  (see Limitations). Preserve the append-only *contract* in code; the durable-persistence
  piece is roadmap.

## Testing

Use the Makefile targets (they encode the correct markers):

- **Deterministic suite (routine):** `make test` — runs `pytest tests/unit/` plus the
  non-clinical part of the clinical accuracy test, with `-m "not clinical"`. Fast (~25s).
  Run before every commit; expect 0 failures. (Sizes drift — `pytest --collect-only -q`
  reports the current count rather than a number baked into this doc.)
- **Everything non-clinical:** `make test-all` (`pytest tests/ -m "not clinical"`).
- **Coverage:** `make test-cov`.
- **Clinical / LLM-as-judge gate:** `make test-clinical` (`pytest tests/clinical/ -m clinical`).
  Makes real Claude calls (~3–5 min); requires `ANTHROPIC_API_KEY` in the shell env —
  source it from the gitignored `.env`, never hardcode or print it. This is the golden-set
  accuracy gate (incl. GC-018/019); run it at the final merge HEAD for any behavior change.

> **Do not infer clinical accuracy from `make test`.** The deterministic suite deselects
> the live LLM tests — they cover different things (see `docs/AGENT_LESSONS.md` P-008).
>
> **Manifest validation:** `python -m pacca.harness.validate_manifest harness/manifests/iter-N.json`
> (or `--all`) validates a change manifest against `change_manifest.schema.json` plus the
> `GC-\d{3}` case-id convention; exit 0 = valid, 1 = errors (per-error report on stderr).
> CI does not yet run it as a gate — that is harness change P-6.

## Limitations (what the design intends but the code does not yet do)

- **No middleware layer** and **no `agent.yaml` loader** — agents are wired by direct
  Python import. The seven-component per-agent harness layout is roadmap. The P-4
  scope guard (`agents/scope_guard.py`) is the first middleware-*pattern* component — a
  call-site wrapper, not a framework middleware — now wired into the submit route in
  enforce mode (chg-9). A true middleware loader remains roadmap.
- **Dead `rag/pipeline.py`.** The dual-collection RAG is **built and governed** in the
  live `integrations/vector_store.py` (see Where-things-live). What remains dead is the
  OLD `rag/pipeline.py` single-collection store — it does not import cleanly (stale
  `uuid7` / missing-enum references in `models/guidelines.py`) and nothing on the live
  path depends on it. Reviving or removing it is a tracked follow-up.
- **Precedent grounding (P-5 policy, iter-13).** Retrieved precedents are institutional
  *context*, not citable evidence: the DecisionAgent still populates
  `cited_evidence_ids` from **submission** `EvidenceItem`s only, so the P-5
  evidence-grounding detector stays submission-scoped. A precedent influences reasoning
  (and is captured in the rationale + the retrieved-context audit), but is not a
  grounded-evidence id. Threading stable precedent/chunk ids into the grounding set is
  the roadmap "RAG chunk-id grounding" follow-up.
- **Clinical gate in CI, blocking pending David.** As of P-6 `ci.yml` adds
  `validate-manifests` (every PR) and `clinical-gate` (GC-018/019 + accuracy, on
  `chg-`/agent-rag PRs and nightly). They run and can fail, but *blocking merge* on them
  requires David's branch-protection setting + the `ANTHROPIC_API_KEY` repo secret (the
  clinical-gate is inert without it). Doc-drift already runs inside `tests/unit` (P-1).
- **No SECRET_KEY fail-fast.** `config/settings.py` ships a weak default `secret_key`
  (and a placeholder `anthropic_api_key`) with **no** startup validator rejecting weak or
  missing values. The server will start with insecure defaults. Add a fail-fast validator
  before relying on the earlier "refuses to start" behavior.
- **Policy change log is in-memory.** See the safety-invariants note above.

## Target architecture (roadmap)

These are the intended end-states, moved here so they are not mistaken for current fact:

- **Seven-component per-agent layout** (NexAU-style): each agent as
  `system_prompt.md` + `long_term_memory.md` + `tool_descriptions/*.tool.yaml` +
  `tools/*.py` + `middleware/*.py` + `skills/<name>/SKILL.md` +
  `sub_agents/<name>/agent.yaml`, declared in a per-agent `agent.yaml` loaded by a
  framework. Rationale: file-level component decoupling + one-file-diff rollback.
- **Dual-collection RAG:** separate `nccn_guidelines` (authoritative) and
  `case_precedents` (institutional memory) stores, kept apart for their different trust
  levels.
- **RAG chunk-id grounding (P-5 follow-up):** thread stable chunk ids from
  `retrieve_relevant_guidelines` through into the agent-visible context and expose the
  retrieved-id set to the orchestrator, so the P-5 evidence-grounding detector
  (`agents/evidence_grounding.py`) can also verify citations against *retrieved RAG
  chunks*. Today the retriever hands the agent concatenated text with no ids, so P-5
  grounds only against submission `EvidenceItem` ids. Pairs naturally with the
  dual-collection RAG work above.
- **CI enforcement (P-6):** `validate-manifests` + `clinical-gate` jobs that make the
  manifest and GC-018/019 gates build-blocking.
- **Integration test tier:** `tests/integration/` exists but is **empty** (an
  `__init__.py` and nothing else) — `pytest tests/integration` collects 0 items. The
  intent is a tier that exercises the submit route end-to-end across real component
  boundaries (API → orchestrator → scope guard → repository → audit log) against a
  live test DB, rather than the unit tier's mocked seams. Until it holds tests, do
  **not** list it as a test tier in user-facing docs — the README project-structure
  tree drops it, and it comes back to the tree when it has coverage. Current real
  tiers: `tests/unit` (652), `tests/clinical` (28), `tests/harness` (27).

## Canonical repo

`github.com/drdgreed/pacca` is canonical. If you find `Chaos-6/pacca` anywhere
(old clone URLs, citation), it is stale — update it to `drdgreed`.

## House style

- Async throughout the backend. Don't introduce blocking calls in request paths.
- Pydantic v2 models for all request/response and domain objects.
- Keep retrieval (rag), reasoning (agents), and safety (orchestrator) separable —
  a change to one should not force edits to the others.
- Logging: `from pacca.config import get_logger` (structlog-backed) — never
  `logging.getLogger` (see `docs/AGENT_LESSONS.md` P-002).
