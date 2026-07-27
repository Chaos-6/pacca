# PACCA — Engineering Reference

The technical companion to the [README](../README.md). That document is written for someone deciding whether this project is worth their attention; this one is written for someone who has decided it is.

**Ground rule for this document: it describes what the code does, not what the design intends.** Where the two differ, it says so — see [Known limitations](#known-limitations). If you find a claim here the code contradicts, the code wins; fix the doc in the same PR.

---

## Contents

- [Stack](#stack)
- [Architecture](#architecture)
- [The governance chain](#the-governance-chain-p-3--p-4--p-5)
- [Harness engineering](#harness-engineering)
- [Safety invariants](#safety-invariants)
- [Testing](#testing)
- [Continuous integration](#continuous-integration)
- [API reference](#api-reference)
- [Configuration](#configuration)
- [Project structure](#project-structure)
- [Known limitations](#known-limitations)
- [Roadmap](#roadmap)
- [Contributing](#contributing)

---

## Stack

| Layer | Technology | Notes |
|---|---|---|
| **LLM** | Claude, `claude-sonnet-4-5-20250929` | Tool-use forced for structured output |
| **Backend** | Python 3.12, FastAPI, Pydantic v2 | Async throughout; no blocking calls in request paths |
| **Production DB** | PostgreSQL 16, SQLAlchemy 2.0, Alembic | JSONB compliance queries, async pool; Alembic is the single schema source — migrations run at deploy (`docker-entrypoint.sh`), CI drift-guarded |
| **Dev DB** | SQLite via `aiosqlite` | Same ORM layer, one env var to switch |
| **Vector store** | ChromaDB, two collections | Separate trust levels — see [Retrieval](#retrieval) |
| **Cache** | Redis 7 | Present in Compose; caching/rate-limiting groundwork |
| **Frontend** | React 18, TypeScript, Vite, Tailwind v4 | Editorial-Clinical design system |
| **Observability** | OpenTelemetry → OTLP/HTTP | One span per agent call; Langfuse-compatible |
| **Testing** | pytest, pytest-asyncio, pytest-cov, hypothesis | 710 collected tests |
| **Security** | python-jose, bcrypt, per-env CSP/HSTS middleware | JWT + timing-safe password comparison |
| **CI/CD** | GitHub Actions | Six jobs — see [CI](#continuous-integration) |

---

## Architecture

### Request lifecycle

A prior-authorization submission (`POST /api/v1/authorizations/`) moves through:

1. **JWT verification** — router-level dependency, before any handler runs
2. **`IntentRecord` minted and declared** — the run's scope contract, emitted as the first audit event
3. **Pre-flight risk gate** — `ClinicalRiskDetector.evaluate()`, seven deterministic checks, no LLM
4. **Retrieval** — scope-guarded ChromaDB query across guidelines + precedents
5. **Tier-1 agent** — `DecisionSupportAgent`, tool-use forced, returns structured output with confidence
6. **Evidence-grounding check** — deterministic; ungrounded citations short-circuit to human review
7. **Confidence routing** — three branches; may escalate to Tier-2 `MedicalDirectorAgent`
8. **Scope-guarded persistence** — two identifier-checked DB writes, audit-flushed first

### Agents

`src/pacca/agents/` — five agents, wired by direct Python import (there is no `agent.yaml` loader):

| Agent | Location | Role |
|---|---|---|
| Decision Support (Tier 1) | `decision_support/` — `system_prompt.md` + `long_term_memory.md` | Guideline-grounded recommendation |
| Medical Director (Tier 2) | `medical_director/` — `system_prompt.md` | Supervising second opinion |
| Evidence Aggregation | `evidence_agent.py` | Narrative synthesis from scattered notes |
| Clinical Classification | `classification_agent.py` | Complexity, specialty, urgency |
| Policy Evolution | `evolution.py` | Amendment proposals from override patterns |

Two agents have file-level component mounts (`system_prompt.md`, `long_term_memory.md`); the other three are flat modules. That asymmetry is deliberate — the mounts exist where the harness discipline needs a one-file rollback surface.

**Prompts are versioned.** `src/pacca/agents/prompts/templates.py` holds `PROMPT_REGISTRY`, the single source of truth. Current: `decision_support` at **v2.7**, `medical_director` at **v2.2**. Versions surface in audit records (`audit_logs.details.prompt_version`) and OTel spans (`agent.prompt_version`), so a decision from six weeks ago can be traced to the exact prompt that produced it. Register a prompt; never hardcode one at a call site.

### The orchestrator

`src/pacca/agents/orchestrator.py` (class `Orchestrator`) owns all safety logic. **This logic overrides model confidence** — treat it as a safety boundary, not a suggestion.

`EscalationReason` in `src/pacca/models/enums.py` has **11 members**, each mapping to a branch or a deterministic short-circuit:

- **Confidence routing (3):** `CONFIDENCE_BELOW_THRESHOLD`, `MEDICAL_DIRECTOR_REQUIRED`, plus the auto-approve path
- **Pre-flight (7):** `EXPERIMENTAL_TREATMENT`, `RARE_CONDITION`, `CONFLICTING_GUIDELINES`, `PRIOR_DENIAL_SAME_SERVICE`, `HIGH_COST`, `PEDIATRIC_COMPLEX`, `ADULT_COMPLEX`
- **Runtime guards (2):** `SCOPE_VIOLATION`, `UNGROUNDED_EVIDENCE`

> The orchestrator's own docstring still calls this "the 7-branch tree" — the name from PRD §5.4. The set has grown since; the enum is authoritative.

### Retrieval

The live vector store is **`src/pacca/integrations/vector_store.py`** (`GuidelineRetriever`), which maintains two ChromaDB collections at different trust levels:

- **`nccn_guidelines`** — authoritative clinical guidance (NCCN, CMS, AHA, ADA, ACR)
- **`case_precedents`** — Medical Director override decisions with rationales, embedded immediately and surfaced in semantically similar future cases

Keeping them apart is the point: an institutional precedent should never be mistaken for published guidance.

> `src/pacca/rag/pipeline.py` is a **legacy single-collection module that does not import** (`ImportError: cannot import name 'ClinicalGuideline'`). It is dead code, referenced by nothing in the API path. Revival or removal is tracked in [Known limitations](#known-limitations).

### Observability

Span emission lives in `src/pacca/agents/base.py` and `src/pacca/config/tracing.py` — one span per agent call, exported over OTLP/HTTP to any compatible collector (Langfuse, Jaeger, Tempo). There is no `src/pacca/observability/` package.

Application logging is structlog-backed: `from pacca.config import get_logger`. Never `logging.getLogger` — see `docs/AGENT_LESSONS.md` P-002.

---

## The governance chain (P-3 → P-4 → P-5)

Three layers that constrain the agent at runtime. Each shipped as its own governed iteration with a manifest and a verdict.

### P-3 — `IntentRecord` (iter-7, chg-7)

`src/pacca/models/intent.py`. A typed, per-run contract declaring allowed collections, allowed actions, an **opaque** subject reference, and expected effects. Emitted as the **first** audit event (`intent.declared`), so the trail opens with what the run was permitted to do rather than what it did. Record-only by itself; P-4 and P-5 read it.

### P-4 — Minimum-necessary scope guard (iter-8 warn → iter-9 enforce)

`src/pacca/agents/scope_guard.py`. `enforce_scope(intent, action, **call_args)` is a fail-closed call-site wrapper expressing the HIPAA minimum-necessary standard in code. It denies:

- an action not in the run's declared set
- an identifier belonging to a different case (cross-case leak)
- a collection outside the declared allow-list

Wired into the submit route in **enforce** mode at three sites: `db.write_request`, `db.write_decision`, and the RAG query. Mode is `settings.scope_guard_mode`.

**In correct operation this never denies** — a run always passes its own scope. Its entire value is what happens when a bug or a leak makes that untrue. It was deliberately shipped in warn mode first (chg-8) and observed before being trusted to deny (chg-9).

### P-5 — Evidence-grounding detector (iter-10, chg-10)

`src/pacca/agents/evidence_grounding.py`, via `unresolved_cited_evidence()`, wired into the orchestrator *after* the decision and *before* confidence routing. The agent must populate `cited_evidence_ids`; any id absent from the submission forces human review with `UNGROUNDED_EVIDENCE`.

This promotes the GC-018/019 anti-hallucination guard from a **test-time** assertion to a **runtime** control. It is deterministic — no second model judges the first.

**Current scope limit:** grounding is checked against submission `EvidenceItem` ids only. The retriever hands the agent concatenated text with no stable chunk ids, so RAG citations can't yet be verified the same way. Threading chunk ids through is a tracked follow-up.

### Honest architecture note

The scope guard is a **call-site wrapper, not framework middleware** — PACCA has no middleware loader. It is the first concrete instance of the intended H3 middleware tier, and it's labeled that way rather than dressed up as more than it is.

---

## Harness engineering

> Every change that alters agent behavior ships as a one-file diff with a recorded, falsifiable prediction. The next evaluation round verifies the prediction. Rejected changes are reverted at file granularity.

"Behavioral" means: how an agent reasons, what tools it can call, what middleware fires, or what memory context it sees.

### The cycle

1. Read [`HARNESS.md`](HARNESS.md) and identify the correct **constraint level**
2. Make the change as a **one-file diff**. Multiple components → multiple commits, one file each
3. Use the `chg-N:` commit prefix (conventional commits otherwise: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`)
4. Add an entry to `harness/manifests/iter-N.json` per `change_manifest.schema.json`, including the healthcare-governance fields `phi_impact` and `audit_relevant`
5. Record a verdict at the next evaluation round

Validate a manifest locally before committing:

```bash
python -m pacca.harness.validate_manifest harness/manifests/iter-10.json
python -m pacca.harness.validate_manifest --all      # what CI runs
```

Exit 0 = valid, 1 = errors (per-error report on stderr). It checks the JSON Schema 2020-12 contract plus the `GC-\d{3}` case-id convention.

### Where the record lives

- `harness/manifests/iter-N.json` — the change manifest **and** its `verdicts` array (verdicts are inside the manifest; there are no separate `iter-N-verdicts.json` files)
- [`DECISIONS.md`](DECISIONS.md) — append-only prose log of every change with prediction and outcome
- [`ITERATIONS.md`](ITERATIONS.md) — narrative per iteration, in the AHE paper's Appendix C format

**State: 11 iterations (`iter-0` … `iter-10`), 25 changes, 0 rollbacks.** Verdict outcomes are `keep` or `improve`; nothing has been rolled back yet, which is worth stating plainly rather than presenting as validation of the method — a method that never rejects anything isn't being tested hard enough.

### Constraint surfaces used so far

`instrumentation` · `system_prompt` · `evaluation_harness` · `escalation_branch` · `long_term_memory` · `tool_implementation` · `audit_schema` · `middleware`

### Governance rollout status

| Step | What it adds | Status |
|---|---|---|
| **P-0** | Doc-truth reconciliation of `CLAUDE.md` + `HARNESS.md` | ✅ Merged |
| **P-1** | Doc-drift guard (`tests/harness/doc_drift_guard.py`), running in the unit suite | ✅ Merged |
| **P-2** | Real `pacca.harness.validate_manifest` CLI | ✅ Merged |
| **P-3** | Per-run `IntentRecord`, first audit event | ✅ Merged (chg-7 / iter-7) |
| **P-4** | Minimum-necessary scope guard, warn → enforce | ✅ Merged (chg-8, chg-9 / iter-8, iter-9) |
| **P-5** | Runtime evidence-grounding detector | ✅ Merged (chg-10 / iter-10) |
| **P-6** | `validate-manifests` + `clinical-gate` CI jobs | ✅ Merged — see the [caveat](#continuous-integration) |

---

## Safety invariants

**Never weaken these.**

- **Anti-hallucination.** Agents may only reference clinical evidence explicitly present in the submission. Enforced at three levels: the prompt guard, golden cases GC-018/GC-019 in the clinical gate, and the P-5 runtime detector.
- **Tool-use forced** for structured output. Never switch an agent to free-text parsing — it is the most common agentic failure mode.
- **Pre-write audit trail.** Correlation-ID-linked event pairs (`AuditLogModel.correlation_id`) are flushed **before** any state change. `tests/unit/test_audit_trail.py` guards the ordering. Don't reorder writes ahead of the audit flush.
- **Append-only policy change log.** Policy changes are never mutated or deleted. *As built this is a prototype* — `PolicyChangeLogEntry` in `agents/evolution.py` is an in-memory dataclass list, not a DB table. Preserve the append-only contract in code; durable persistence is roadmap.
- **PHI/secret pre-commit guard.** `.githooks/pacca_guard.py`, wired via `.pre-commit-config.yaml`, reusing `sme_authoring/validators.py` as the single source of truth and tested in `tests/unit/test_pacca_guard_hook.py`. This is the pattern to imitate when adding any commit-time gate.

---

## Testing

Use the Makefile targets — they encode the correct markers.

| Command | What it runs | Duration |
|---|---|---|
| `make test` | `tests/unit/` + the non-clinical part of the clinical accuracy test, `-m "not clinical"` | ~25 s |
| `make test-all` | Everything non-clinical | ~40 s |
| `make test-cov` | Coverage report | — |
| `make test-clinical` | `tests/clinical/ -m clinical` — **real Claude calls** | 3–5 min |

### Current collection

```
tests/unit/         652     mocked seams, deterministic
tests/clinical/      28     dataset integrity + 5 live LLM-as-judge tests
tests/harness/       27     manifest discipline, doc-drift, schema validation
tests/test_level5_flow.py   3
                    ───
                    710 collected
```

**Only 5 of the 710 tests carry the `clinical` marker.** `pytest -m "not clinical"` collects 705/710. The other 23 tests in `tests/clinical/` are deterministic dataset-integrity checks that run in the normal suite — so "the clinical tier" is far cheaper than its directory size suggests.

`make test-clinical` requires `ANTHROPIC_API_KEY` in the shell environment. Source it from the gitignored `.env`; never hardcode or print it.

> **Do not infer clinical accuracy from `make test`.** The deterministic suite deselects the live LLM tests. They cover different things — see `docs/AGENT_LESSONS.md` P-008.

### The clinical dataset

105 synthetic cases, `GC-001`–`GC-105`, across ~20 thematic suites (oncology, cardiology, pediatric, geriatric, transplant, mental health, denial-class, …), plus `GC-999`, a smoke sentinel used to verify the gate itself runs. `TestGoldenDatasetIntegrity` enforces structural invariants across the set.

The 20-case golden core is the accuracy gate: LLM-as-judge on a 1–5 rubric, threshold ≥80%. Hallucinations score an automatic 1 — there is no acceptable rate of inventing clinical data.

---

## Continuous integration

`.github/workflows/ci.yml` — six jobs:

| Job | Runs |
|---|---|
| **Lint & Type Check** | ruff check, ruff format --check, mypy |
| **Tests** | `pytest tests/unit`, then coverage → Codecov |
| **Build Docker Image** | Buildx |
| **Security Scan** | bandit, dependency vulnerability check |
| **Validate change manifests** | `python -m pacca.harness.validate_manifest --all` — every PR |
| **Clinical gate** | GC-018/019 + accuracy threshold via `make test-clinical` |

### One caveat worth knowing

**The clinical gate is conditional by design.** It runs only when the PR touches `src/pacca/(agents|rag)/` **or** carries a `chg-` commit — and then only if the `ANTHROPIC_API_KEY` repo secret is present. Without the secret it emits a notice and goes inert rather than failing. That keeps API spend proportionate to risk, but it means a green "Clinical gate" check does not by itself prove the gate ran. Check the step outcomes.

---

## API reference

Interactive docs at `/docs` (Swagger UI) when the server is running.

### Submit an authorization request

```http
POST /api/v1/authorizations/
Authorization: Bearer <jwt-token>
Content-Type: application/json

{
  "patient":   { "id": "P12345", "date_of_birth": "1966-05-15", "gender": "M" },
  "diagnosis": { "code": "C34.1", "description": "Malignant neoplasm of upper lobe, lung" },
  "treatment": {
    "code": "J9271", "code_type": "HCPCS",
    "description": "Pembrolizumab injection",
    "category": "medication", "estimated_cost": 15000.00
  },
  "provider":  { "provider_id": "1234567890", "provider_name": "Dr. Jane Smith" },
  "payer":     { "payer_id": "BCBS001", "payer_name": "Blue Cross Blue Shield",
                 "member_id": "MEM123456" },
  "clinical_notes": "Patient with stage IIIA NSCLC, PD-L1 TPS >= 50%...",
  "urgency": "expedited"
}
```

```json
{
  "request_id": "AUTH-01HQXYZ...",
  "status": "approved",
  "decision": "approve",
  "confidence_score": 0.92,
  "decision_summary": "Authorization approved based on NCCN guidelines...",
  "complexity": 3,
  "specialty": "oncology",
  "requires_human_review": false,
  "harness_iteration_tag": "harness-iter-10",
  "prompt_registry_versions": {
    "decision_support": "v2.7",
    "medical_director": "v2.2"
  }
}
```

### Endpoints

| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/v1/register/` | Create a user account |
| POST | `/api/v1/login/` | Exchange credentials for a JWT |
| POST | `/api/v1/authorizations/` | Submit an authorization request |
| POST | `/api/v1/authorizations/feedback` | Director override → vector-store precedent |
| GET / PATCH | `/api/v1/admin/config` | Read / update operational config at runtime |
| GET | `/api/v1/admin/proposals` | Pending policy proposals |
| POST | `/api/v1/admin/proposals/{id}/approve` | Approve and deploy a guideline amendment |
| GET | `/api/v1/admin/change-log` | Policy change audit log |
| GET | `/api/v1/admin/harness/iterations` | Harness iteration tags |
| GET | `/health` | Health check |

**SME Case Authoring**

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/v1/sme-authoring/status` | Dataset state, per-file counts, milestone gaps |
| GET | `/api/v1/sme-authoring/batches` · `/batches/{id}` | Planned authoring batches |
| GET | `/api/v1/sme-authoring/gaps` | Prioritized coverage gaps |
| GET / POST | `/api/v1/sme-authoring/sessions` | List or create a session |
| GET / DELETE | `/api/v1/sme-authoring/sessions/{id}` | Inspect or remove a session |
| POST | `/api/v1/sme-authoring/sessions/{id}/draft` | Generate an LLM draft |
| POST | `/api/v1/sme-authoring/sessions/{id}/validate` | Run six deterministic validators |
| POST | `/api/v1/sme-authoring/sessions/{id}/commit` | Commit with SME attestation |
| WS | `/api/v1/sme-authoring/sessions/{id}/draft-stream` | Live drafting, first-message JWT auth |

Auth is enforced router-wide for `/authorizations` and `/admin`; the SME router enforces per-endpoint because the WebSocket needs its own auth protocol.

---

## Configuration

| Variable | Description | Default | Production |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Claude API key | Required | Required + BAA |
| `SECRET_KEY` | JWT signing key (≥32 chars) | Set explicitly | Rotate quarterly |
| `DATABASE_URL` | Database connection | SQLite | PostgreSQL 16 |
| `TOKEN_EXPIRE_MINUTES` | JWT expiry | 30 | 15–30 |
| `AUTO_APPROVE_CONFIDENCE_THRESHOLD` | Auto-approve threshold | 0.95 | 0.95–0.98 |
| `ESCALATION_CONFIDENCE_THRESHOLD` | MD escalation threshold | 0.90 | 0.90–0.95 |
| `HIGH_COST_THRESHOLD` | Cost escalation trigger (USD) | 100000 | Per payer contract |
| `LLM_RETRY_MAX_ATTEMPTS` | Max LLM retries | 3 | 3–5 |
| `ENABLE_AUTONOMOUS_DECISIONS` | Master autonomy switch | true | true (false for audit) |
| `SCOPE_GUARD_MODE` | `warn` or `enforce` | enforce | enforce |

Thresholds are runtime-adjustable via `PATCH /api/v1/admin/config` without a restart. See [`.env.example`](../.env.example) for the full list.

---

## Project structure

```
pacca/
├── src/pacca/
│   ├── agents/
│   │   ├── decision_support/    # system_prompt.md + long_term_memory.md (H1/H2 mounts)
│   │   ├── medical_director/    # system_prompt.md
│   │   ├── sme_authoring/       # SME authoring library (CLI + Web UI share this)
│   │   ├── prompts/             # PROMPT_REGISTRY — versioned prompts
│   │   ├── orchestrator.py      # escalation tree — the safety boundary
│   │   ├── scope_guard.py       # P-4 minimum-necessary guard
│   │   ├── evidence_grounding.py# P-5 runtime hallucination detector
│   │   ├── clinical_risk_detector.py   # 7 pre-flight gates
│   │   └── evidence_agent.py · classification_agent.py · evolution.py
│   ├── api/                 # FastAPI app, routes, middleware/security_headers.py
│   ├── config/              # settings, structlog, tracing
│   ├── db/                  # models, repository, Alembic migrations
│   ├── harness/             # validate_manifest CLI
│   ├── integrations/        # vector_store.py — the live dual-collection retriever
│   ├── models/              # Pydantic domain models, enums, intent.py
│   └── rag/                 # legacy single-collection pipeline (dead code)
├── frontend/                # React 18 + TypeScript + Vite
├── harness/manifests/       # schema + iter-0 … iter-10 manifests with verdicts
├── tests/
│   ├── unit/                # 652
│   ├── clinical/            # 105-case dataset + LLM-as-judge harness
│   └── harness/             # manifest discipline + doc-drift
├── demo/                    # 53-case synthesized demo dataset
├── docs/
└── docker-compose.yml       # 6 services (frontend not included — run via npm)
```

---

## Known limitations

Stated plainly, because a reference architecture that hides its gaps isn't one.

- **No middleware loader, no `agent.yaml` loader.** Agents are wired by direct Python import. The seven-component per-agent layout is roadmap. The scope guard is a call-site wrapper, not framework middleware.
- **`rag/pipeline.py` is dead code.** It does not import (stale references chain through `models/guidelines.py`). The live retriever is `integrations/vector_store.py`. Revive or delete.
- **Policy change log is in-memory.** `PolicyChangeLogEntry` is a dataclass list, not a table. The append-only contract holds in code but does not survive a restart.
- **Harness tags stop at `harness-iter-6`.** Iterations 7–10 have manifests and verdicts but no git tag.
- **`CHANGELOG.md` is stale** — last entry `[2.3.0]`, 2026-05-09. The manifests and `DECISIONS.md` are the current record.
- **No integration test tier.** `tests/integration/` exists but is empty. See [Roadmap](#roadmap).
- **P-5 grounds against submission evidence only.** RAG chunks carry no agent-visible ids, so retrieved-guideline citations aren't yet verifiable the same way.

---

## Roadmap

- **Integration test tier** — end-to-end coverage across real component boundaries (API → orchestrator → scope guard → repository → audit log) against a live test database, rather than the unit tier's mocked seams. `tests/integration/` is reserved for it.
- **RAG chunk-id grounding** — thread stable chunk ids from retrieval into agent-visible context so P-5 can verify guideline citations, not just submission evidence.
- **Seven-component per-agent layout** — `tool_descriptions/`, `tools/`, `middleware/`, `skills/`, `sub_agents/` declared in a per-agent `agent.yaml` loaded by a framework. Rationale: file-level decoupling with one-file-diff rollback.
- **True middleware loader** — promoting the scope-guard pattern from call-site wrapper to a declared tier.
- **Durable policy change log** — move `PolicyChangeLogEntry` to a table with an append-only constraint.
- **Evaluation expansion (H5)** — unified benchmark with k=2 rollouts, pass@1 / tokens-per-case / Succ-per-Mtok, plus load and adversarial testing.
- **Dataset growth** — 105 → 300 (deployment-grade) → 500+ (SaMD-grade). Statistical grounding in [`DATASET_SUFFICIENCY.md`](DATASET_SUFFICIENCY.md).

---

## Contributing

Two paths, and the PR template forces the choice — every PR is one or the other, never ambiguous:

- **Standard PRs** — refactors, docs, infra, dependency bumps, non-behavioral fixes.
- **Behavioral PRs** — anything changing how an agent reasons, what tools it can call, what middleware fires, or what memory it sees. Requires a one-file diff plus a manifest entry under `harness/manifests/`.

Before opening either, run:

```bash
make test                                        # must be 0 failures
python -m pacca.harness.validate_manifest --all  # behavioral PRs
make test-clinical                               # behavioral PRs, at merge HEAD
```

Full workflow, manifest schema, and the verdict cycle: [`CONTRIBUTING.md`](../CONTRIBUTING.md). Security findings: [`SECURITY.md`](../SECURITY.md) — please don't open a public issue.

### Regenerating the diagrams

The figures in the README are generated, not hand-drawn, so stale numbers are a one-command fix:

```bash
python scripts/generate_assets.py
```

The script fact-checks its own iteration and change counts against `harness/manifests/` and warns if the hardcoded figures have drifted.
