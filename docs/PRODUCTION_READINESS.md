# PACCA — Production Readiness & Known Issues

**Status:** Pre-customer. Do **not** load real PHI or demo to customers with data until the P0 items below are resolved.
**Last updated:** 2026-07-23 (Group B reconciled against `main`).
**How these were found:** Bringing the full stack up locally (Docker Compose: FastAPI + Postgres + Redis + ChromaDB + frontend) and exercising the live **register → login → submit** path end-to-end for the first time. Every issue below is reproduced, not theoretical.

> **Reconciliation note (2026-07-23).** All of Group B (B1–B6, the persistence-layer bugs) is now **FIXED** and verified — B1/B2 by earlier PRs (#43, #19), B3–B6 in iterations 11–12 plus standard fixes this pass. The original entries below (dated 2026-05-21) are kept for provenance with updated **Status** columns. Group A and Group C have **not** been re-verified in this pass. The one residual that is DB state rather than code: B3's dropped-FK local hack must be manually reverted (re-add the constraint via migration 003).

---

## Why these were invisible until now

The existing test suite (120 unit + clinical tests) exercises the **orchestrator and clinical reasoning in-process**, plus structural unit behavior. It never ran the **FastAPI app against a real PostgreSQL database**. Two consequences:

1. **SQLite masks Postgres-only failures.** SQLite does not enforce foreign keys by default and has no `JSONB` type, so referential-integrity bugs and Postgres-specific column types never surfaced.
2. **No full-stack / contract / compose smoke test.** The register/login/submit HTTP path, settings-from-env parsing, and container wiring were never asserted in CI.

The reasoning engine is solid. The **web-app and persistence plumbing around it is half-finished** — that is the entire theme of the issues below.

---

## Severity legend

| Level | Meaning |
|-------|---------|
| **P0 — Critical** | Blocks correct operation or breaks data integrity / auditability. Fix before any customer-facing use. |
| **P1 — High** | Blocks startup or runtime in a realistic deployment, or a security/compliance smell. |
| **P2 — Medium** | Works but is fragile, inconsistent, or a maintainability/clarity problem. |
| **P3 — Low** | Cosmetic or environment-specific. |

---

## 1. Issue log

### Group A — Environment & deployment configuration

| ID | Sev | Issue | Status |
|----|-----|-------|--------|
| A1 | P3 | Host port collisions (5432, 5433, 6379) | Worked around (remap) |
| A2 | P2 | Langfuse `:latest` crashes; blocked the whole stack | Worked around (decoupled) |
| A3 | P3 | OTLP port mapping `4318:4317` likely inverted | Open |
| A4 | P3 | Docs claim `docker-compose up` starts the frontend; it doesn't | Open |
| A5 | P1 | `SECRET_KEY` never passed to the API container → fail-fast crash | Worked around (hardcoded dev key) |
| A6 | P1 | `CORS_ORIGINS` env value crashes settings parse | Worked around (JSON value) |

**A1 — Host port collisions.** `docker-compose.yml` published Postgres on 5432, Langfuse-db on 5433, Redis on 6379. A locally-installed Postgres, a locally-installed Redis (`redis-ser`), and a stray 3-week-old container (`cf-pg-temp`, unrelated project) already held those ports → `address already in use`.
*Fix applied:* remapped host ports to 5434 / 5435 / 6380; container ports unchanged (the app talks to containers over the internal network, so functionality is unaffected).
*Proper fix:* in dev, don't publish DB/Redis ports to the host at all (nothing external needs them), or make them configurable, e.g. `"${PACCA_PG_PORT:-5432}:5432"`. Document required ports.

**A2 — Langfuse blocks the entire backend.** `langfuse/langfuse:latest` pulls a version requiring backing services not present in this compose; the container exits(1). Because `pacca-api` had `depends_on: langfuse: { condition: service_healthy }`, an **optional observability sink took down the core API**.
*Fix applied:* removed the hard dependency, set `OTEL_ENABLED=false`, excluded Langfuse from the brought-up services.
*Proper fix:* pin a known-good Langfuse image with a complete compose, **or** use a lighter local OTel backend (e.g. Jaeger all-in-one). Observability must be a **soft** dependency — never block the API on a telemetry sink.

**A3 — OTLP port mapping.** `"4318:4317"` maps host 4318 → container 4317; OTLP/HTTP is conventionally 4318. Verify against the pinned Langfuse version and correct.

**A4 — Frontend not in compose.** The compose header comment and README Quick Start list the frontend among `docker-compose up` services, but there is no frontend service. The frontend runs separately via Vite (`npm run dev`, port 3000). Either add a frontend service or fix the docs.

**A5 — Missing `SECRET_KEY`.** The `pacca-api` service listed no `SECRET_KEY` and had no `env_file`. Docker Compose's host `.env` is only used for `${VAR}` substitution, not injected wholesale — so the container received no key, and the app (correctly) fail-fasts when `SECRET_KEY` is missing/<32 chars.
*Fix applied:* added a hardcoded dev `SECRET_KEY` in compose (local only).
*Proper fix:* inject via `env_file:` or a secrets manager; never hardcode beyond local dev; document the requirement and the fail-fast behavior.

**A6 — `CORS_ORIGINS` parse crash.** `cors_origins: list[str]` in `config/settings.py`. pydantic-settings v2 attempts to **JSON-decode complex (list) fields from the env source before field validators run**, so the comma-separated value `http://localhost:3000,http://localhost:5173` raised `SettingsError`. The `@field_validator("cors_origins", mode="before")` meant to split commas **never fires for env input** — a false sense of safety.
*Fix applied:* pass a JSON array string in compose: `'CORS_ORIGINS=["http://localhost:3000","http://localhost:5173"]'`.
*Proper fix:* annotate the field with `NoDecode` (pydantic-settings v2) so the validator handles the raw string, or document that list-typed env vars must be JSON. Add a settings test that loads from env exactly as deployed.

### Group B — Persistence-layer bugs (the serious ones)

| ID | Sev | Issue | Status |
|----|-----|-------|--------|
| B1 | **P0** | Dual-database split: `users` table created in SQLite, queried in Postgres | **FIXED** PR #43 — sync engine deleted; `users` created on the async `get_engine()` the handlers query |
| B2 | P1 | `postgresql.JSONB` hardcoded → app cannot run on SQLite despite docs | **FIXED** PR #19 — `JSON().with_variant(JSONB(),"postgresql")` on all 15 JSON cols; `create_all` succeeds on SQLite |
| B3 | **P0** | Audit rows referencing `request_id` flush before the parent row → FK violation on Postgres | **FIXED** iter-12 (chg-13) — `audit_logs.request_id` FK now `DEFERRABLE INITIALLY DEFERRED`; verified on Postgres 16. **Dropped-FK local hack still needs manual revert** (re-add via migration 003) |
| B4 | P1 | ChromaDB guideline collections not seeded → empty guideline context | **FIXED** — crash gone (idempotent `get_or_create_collection` + B5); seeding wired: `make seed` + populated-collections test |
| B5 | **P0** | Stdlib loggers called with structured-logging kwargs (`error=`, `db_path=`) → `TypeError` 500s every request; **defeats an intentional graceful fallback** | **FIXED** — `vector_store.py` on structlog `get_logger`; `agents/base.py` migrated + logging smoke test added |
| B6 | **P0** | `decision_id` is **generated by the LLM** but stored in a `unique=True` column → repeat submissions of the same case collide → `IntegrityError` → 500. Session not rolled back, so the failure is masked by a `PendingRollbackError` | **FIXED** iter-11 (chg-11 server-side id, chg-12 rollback); verified live 2026-07-23 |

**B1 — Dual-database split for auth (P0).** `api/database.py` hardcodes a **synchronous SQLite engine** (`sqlite:///./pacca.db`) used to create the `users` table at startup, while `db/session.py` uses the **async engine bound to `DATABASE_URL`** (Postgres). `register`/`login` query `users` through the async (Postgres) session, where the table doesn't exist → `relation "users" does not exist` → HTTP 500.
*Workaround applied (now removed):* the `users` table was created in Postgres via a local, untracked helper (`_init_users.py`). Superseded by C5 — `users` is now covered by Alembic migration 004 and applied by `docker-entrypoint.sh`; the helper has been deleted.
*Proper fix:* migrate `User` onto the async `db/models.py` schema and the single async engine; delete the legacy sync engine in `api/database.py`. The code's own comment flags this ("Week 6 consolidation note: move User to the async schema"). **One database, one engine, one declarative Base.**

**B2 — JSONB hardcoded; SQLite path is non-functional (P1).** Async models use `postgresql.JSONB` (e.g. `authorization_requests.secondary_diagnoses`, `audit_logs.details`). SQLite cannot compile these (`CompileError: can't render element of type JSONB`). `.env.example` claims *"JSONB columns fall back to TEXT in SQLite — queries still work"* — this is **false**; the documented "one env-var switch to SQLite" does not work.
*Fix / decision:* commit to **Postgres as the source of truth** (correct for JSONB + FKs + HIPAA) and remove the SQLite-parity claims; **or** use `JSONB().with_variant(JSON(), "sqlite")` if SQLite dev is genuinely wanted. Recommended: Postgres-only; update docs.

**B3 — Orphaned audit writes / FK violation (P0, integrity).** `submit_authorization` writes `audit_logs` rows referencing `request_id` (FK → `authorization_requests`) and `decision_id` (FK → `authorization_decisions`), but **never inserts those parent rows**. Postgres rejects the orphaned write (`ForeignKeyViolationError: audit_logs_request_id_fkey`). Hidden because SQLite doesn't enforce FKs.
*Temporary local hack:* dropped the audit FK constraints in the local Postgres **only** to capture a screenshot. **This degrades the audit trail and MUST be reverted** (see §3, integrity recommendations). To restore:
```sql
ALTER TABLE audit_logs
  ADD CONSTRAINT audit_logs_request_id_fkey
  FOREIGN KEY (request_id) REFERENCES authorization_requests(request_id);
ALTER TABLE audit_logs
  ADD CONSTRAINT audit_logs_decision_id_fkey
  FOREIGN KEY (decision_id) REFERENCES authorization_decisions(decision_id);
```
*Proper fix:* within a single transaction, persist the `AuthorizationRequest`, run the decision, persist the `AuthorizationDecision`, **then** write audit logs — so FKs are satisfied and the unit commits atomically. **Keep the FK constraints; they are part of the auditability guarantee.**

**B4 — ChromaDB not seeded (P1, confirmed).** The RAG path (`RAGPipeline` / `GuidelineRetriever`) expects the `nccn_guidelines` / `case_precedents` collections to exist and be populated. On a fresh ChromaDB they are empty. Add an idempotent seeding/bootstrap step (startup hook or `make seed`) and a test asserting the collections are populated before serving traffic.

> *Reconciled 2026-07-25.* Two of the compounding causes are fixed. `RAGPipeline` init no longer fails on import: `models/guidelines.py` referenced a `uuid7` module name the installed distribution does not provide and two enums that did not exist, so `from pacca.rag.pipeline import …` raised `ImportError` on every start — and the bare `except` around it turned that into a permanent, unlogged fallback. The pipeline also no longer names a `clinical_guidelines` collection of its own; `GuidelineRetriever` injects the governed collection, so there is no ungoverned third store to seed. The seeding gap itself is unchanged — an empty collection still returns no guidelines.

**B5 — Structured-logging calls on a standard-library logger (P0).** `integrations/vector_store.py` (and likely the agents/orchestrator) create a stdlib logger via `logging.getLogger(__name__)` but call it with structured keyword arguments, e.g. `logger.warning("rag_pipeline_init_failed", error=str(e), fallback="direct_chromadb")`. Stdlib loggers reject unknown kwargs → `Logger._log() got an unexpected keyword argument 'error'`, raised as a `TypeError` that 500s the request in ~27 ms, before any AI call. **Worse: this fires inside the `except` block meant to log-and-fall-back, so a recoverable RAG-init failure (B4) becomes a hard crash — a bug in the observability path defeats the resilience the code was designed to have.** The pattern is pervasive (every `logger.*` call in `vector_store.py`). *Fix:* either adopt `structlog` (`logger = structlog.get_logger(__name__)` with a configured processor chain) or convert all calls to stdlib-safe form (f-strings or `extra={...}`). Requires a code change across modules + image rebuild. Add a logging smoke test that actually exercises each log path (these never fire in the current unit tests, which is why this shipped).

**B6 — The LLM generates a uniqueness-constrained business key (P0, integrity).** `DecisionAgent.run()` calls `self.execute(..., response_model=AuthorizationDecision)` (`agents/decision.py:107`), so the **model's forced tool-use output is the decision object itself — including `decision_id`**. That value is then persisted into `authorization_decisions.decision_id`, declared `String(50), unique=True, index=True` (`db/models.py:114`). Nothing in `src/` ever constructs a decision id for the normal path; grepping for the observed prefixes (`PA-`, `dec_`) returns **no matches in the codebase**, because they were invented by Claude. The only server-generated ids are the escape hatches: `PREESC-…` (`orchestrator.py:394`) and `SCOPE-…` (`api/routes/authorizations.py:300`).

*Reproduction (2026-07-22, fresh SQLite DB, real API key).* Submitting the built-in provider demo case succeeds the first time — the model returned `dec_p_demo_72148`. **Every subsequent submission of the same case 500s**, because the model deterministically returns the same id and the unique index rejects it:

```
sqlite3.IntegrityError: UNIQUE constraint failed: authorization_decisions.decision_id
[parameters: ('dec_p_demo_72148', 'demo_1784780627924', 'AUTO_APPROVED', 0.98, ...)]
```

Note the `request_id` (`demo_1784780627924`) *is* unique per submission — only the model-supplied `decision_id` collides.

*Why this is P0, not a demo annoyance.* A primary business key that an LLM invents is not a key. Three distinct failure modes follow: (1) **collision** — legitimate resubmission of the same case (an appeal, a corrected submission) is rejected at the database layer; (2) **non-determinism** — the observed ids had three different shapes (`PA-SYN-2048-001`, `PA-SYN-3117-72148`, `dec_p_demo_72148`), so no downstream system can parse or index on the format; (3) **auditability** — `audit_logs.decision_id` (`db/models.py:218`) and the FK in `human_reviews` (`db/models.py:166-167`) both point at this value, so a model that reuses an id silently cross-links two decisions' audit trails. That last one directly undermines the correlation-id guarantee the audit design is built on.

*Secondary defect exposed by the same trace.* After the `IntegrityError`, the async session is not rolled back, so the following audit write raises `sqlalchemy.exc.PendingRollbackError` — which is what actually surfaces in the log. **The original integrity error is masked by a second, less informative one.** Same family as B3: the failure path is not exercised by tests.

*Fix.* Generate `decision_id` server-side and **remove it from the model's response schema** — the agent should return clinical judgment (outcome, confidence, rationale, cited evidence), never identifiers. Mint it next to `request_id` (a UUID, or `PA-{request_id}` which is already unique and is the natural key), and let the model populate only the fields it is qualified to. Add (a) a regression test that submits the same case twice and asserts two distinct persisted decisions, and (b) a `session.rollback()` in the persistence error path so integrity failures report themselves. Broader rule worth adopting: **no LLM-supplied value may be stored in a unique, indexed, or foreign-keyed column.**

### Group C — Code quality, frontend, security smells

| ID | Sev | Issue |
|----|-----|-------|
| C1 | P2 | `api/main.py` CORS: `allow_origins=["*"]` **with** `allow_credentials=True` (invalid/insecure), and it **ignores** `settings.cors_origins` entirely |
| C2 | P2 | Frontend hardcodes `http://127.0.0.1:8000` in `LoginScreen.tsx` — breaks any non-localhost deployment |
| C3 | P3 | `App.tsx` rendered `<main>` twice → form rendered twice. **Fixed** — removed the duplicate `<main>` |
| C4 | P3 | Two login components (`Login.tsx`, `LoginScreen.tsx`); only `LoginScreen` is wired (dead code) |
| C5 | P2 | ~~`alembic.ini` present but schema is built via `create_all` across **two** Bases/engines; no migrations applied.~~ **FIXED** — Alembic is now the single schema source: `env.py` combines both Bases' metadata, migration 004 adds `users`, migration 001's `JSONB` was made dialect-agnostic (so `upgrade head` runs on SQLite too), `create_all` removed from app startup, `docker-entrypoint.sh` runs `alembic upgrade head` before uvicorn, and a `migration-drift` CI job asserts models ≡ migrations on Postgres. |
| C6 | P2 | Hardcoded dev secrets in compose (added `SECRET_KEY`, plus pre-existing Langfuse `NEXTAUTH_SECRET`/`SALT`/passwords) |

---

## 2. Recommended test suite (to ring out further issues)

The single highest-value change: **run the existing and new tests against a real PostgreSQL instance** (via [Testcontainers](https://testcontainers-python.readthedocs.io/)), not SQLite. That alone would have caught B1, B2, B3, and C1.

Rough estimates assume one engineer familiar with the codebase. "Catches" = which issues above the phase would have surfaced.

| Phase | Scope | Effort | Catches |
|-------|-------|--------|---------|
| **1. Real-DB integration** | Spin Postgres in CI (Testcontainers). Re-run repository/DB tests against Postgres. Add FK + transaction-integrity tests (assert no orphaned audit rows possible). | 2–3 d | B1, B2, B3, B4 |
| **2. API contract / E2E (backend)** | `httpx.AsyncClient` against the app on a Postgres container: register → login → submit. Assert `authorization_requests`, `authorization_decisions`, **and** `audit_logs` rows all exist and reference correctly. | 2–3 d | B1, B3, C1, A6 |
| **3. Settings-from-env** | Construct `Settings()` from env vars exactly as compose/prod pass them (comma vs JSON, missing `SECRET_KEY`, short key). Assert no crash + correct types. | 0.5–1 d | A5, A6 |
| **4. Migrations** ✅ **DONE** | Alembic is the single schema source. `migration-drift` CI job: `upgrade head` on fresh Postgres, `autogenerate` shows no drift, `downgrade`→`upgrade` roundtrips. `create_all` removed from startup; `docker-entrypoint.sh` applies migrations before serving. | 1–2 d | C5, B1 |
| **5. Compose / container smoke** | CI job: `docker compose up`; wait for `/health`; run one golden submit; assert HTTP 200 + persisted rows. | 1 d | A2, A5, A6, B* |
| **6. Frontend E2E** | Playwright: login → submit → decision renders. Catches contract drift and hardcoded-URL breakage. | 2–3 d | C2, C3, contract drift |
| **7. Security / HIPAA-oriented** | Authz on protected routes (no token → 401); CORS behavior; **audit completeness** (every state change emits a linked audit row); **audit immutability** (UPDATE/DELETE on `audit_logs` denied). | 1–2 d | C1, B3, integrity |
| **8. Load / latency / adversarial** | The README's own "not measured yet" list: sustained-load latency, cost-per-decision at volume, prompt-injection resistance. Ongoing. | (larger) | perf/safety gaps |

**Total for Phases 1–7: ~2–3 engineer-weeks** to reach a demonstrably-correct full-stack path with CI gates. Phase 8 is an ongoing track.

**Tooling:** `pytest`, `pytest-asyncio`, `testcontainers[postgres]`, `httpx`, `alembic`, `playwright`, plus the existing `ruff`/`mypy`. Add a coverage gate on the API + persistence layers specifically (the current 80% gate is on units that bypass this path).

---

## 3. Database integrity & HIPAA-aligned auditability

> Engineering recommendations "in the spirit of" HIPAA. This is **not legal advice** — real compliance requires a formal risk assessment, security review, BAAs, and counsel. (HIPAA §164.312(b) audit controls, §164.312(c)(1) integrity, §164.312(d) authentication, §164.312(e) transmission security.)

The most important conceptual point surfaced by this session: **the foreign-key constraint we had to drop is exactly the mechanism that makes the audit trail trustworthy.** An audit record that points at a `request_id` with no corresponding request is a *broken* audit trail. The constraints are not in the way of compliance — they *are* the compliance control. So:

1. **Keep and rely on FK constraints. Revert the local FK drop** (SQL in B3). Referential integrity is an auditability guarantee, not overhead. (§164.312(c)(1))
2. **Atomic unit-of-work.** Persist request → decision → audit inside one transaction; commit or roll back together. No decision without its request; no audit row without its parent. This fixes B3 *and* makes the audit trail provably consistent.
3. **Append-only, tamper-evident audit log.** Grant the application DB role `INSERT`/`SELECT` on `audit_logs` but **revoke `UPDATE`/`DELETE`** (enforce at the DB, not just the ORM). Consider hash-chaining each entry (store a hash of the previous row) for tamper-evidence. (§164.312(b), (c)(1))
4. **Enforce invariants at the DB, not just in Python.** `NOT NULL`, `CHECK` (e.g. `confidence_score BETWEEN 0 AND 1`), enum/domain types for status. The DB should reject impossible states even if app code regresses.
5. **Versioned schema via Alembic.** Replace `create_all` with migrations. Auditable, reversible schema change-control directly supports both HIPAA documentation requirements and the FDA SaMD change-control narrative in the README. (Currently C5.)
6. **Encryption at rest + in transit.** Disk-level or `pgcrypto` for PHI columns; `sslmode=require` for all connections; documented key management.
7. **Least-privilege roles.** App role: insert/select audit, no update/delete. Migrations run as a separate role. Consider Row-Level Security if multi-tenant (payer/provider isolation).
8. **Time integrity.** Server-side `now()` timestamps (already used). Move off the deprecated `datetime.utcnow()` to timezone-aware UTC; trust a synced clock.
9. **Retention & recovery.** WAL archiving + point-in-time recovery; retention aligned to applicable record-retention rules (commonly up to 6 years for certain records).
10. **Secrets & BAA.** No hardcoded `SECRET_KEY`/passwords beyond local; rotate; use a secrets manager. Execute a **Business Associate Agreement with Anthropic** (or use an on-prem model) before any real PHI touches the system.
11. **Keep demos synthetic.** `DEMO_MODE` data stays synthetic. Do not load real PHI into this stack until items 1–10 plus a formal risk assessment are complete.

---

## 4. Prioritized remediation roadmap

**P0 — before any customer demo *with data*:**
- ~~Revert the local FK drop (B3) and implement the atomic request→decision→audit transaction.~~ → **DONE** (iter-12/chg-13): the FK is now deferrable (checked at commit), which fixes the violation without reordering the audit writes. *Remaining manual step:* re-add the constraint on any local Postgres where it was dropped (run migration 003).
- ~~Consolidate auth onto the single async Postgres engine (B1).~~ → **DONE** (PR #43).
- ~~Fix the logging-API mismatch that 500s every submission (B5) and seed ChromaDB (B4).~~ → **DONE**: B5 on structlog; B4 seeding wired (`make seed` + test).
- Real-database integration + API contract tests (test Phases 1–2). *(B3 now has a real-Postgres reproduction + a CI DDL guard; broader Postgres integration coverage still open.)*
- Secrets out of source (A5/C6); audit-log immutability (§3.3).

**P1:**
- ~~Commit to Postgres-only; fix/remove SQLite claims (B2).~~ → **DONE** (PR #19): dialect variant means SQLite dev works; no Postgres-only forcing needed.
- `NoDecode`/JSON settings fix + settings tests (A6).
- CORS driven by settings, no wildcard-with-credentials (C1).
- ~~Alembic migrations replacing `create_all` (C5).~~ → **DONE**: Alembic is the single schema source; `create_all` removed; `migration-drift` CI guard added.

**P2:**
- Observability as a soft dependency; pin Langfuse or swap to Jaeger (A2/A3).
- Frontend configurable API base URL (C2).
- Compose/frontend docs accuracy (A4); dead-code + double-render cleanup (C3/C4).
- ~~ChromaDB seeding + test (B4).~~ → **DONE** (`make seed` + `test_seed_guidelines.py`).
- Compose smoke test + frontend E2E (test Phases 5–6).

**P3 / ongoing:**
- Load, cost-at-volume, and prompt-injection testing (test Phase 8).

---

## Appendix — local workarounds currently in place (must be reconciled)

These were applied to bring the stack up locally and **are not the production fixes**:

- `docker-compose.yml`: host ports remapped (5434/5435/6380); `SECRET_KEY` hardcoded; `CORS_ORIGINS` as JSON; `OTEL_ENABLED=false`; Langfuse dependency removed from `pacca-api`; `DATABASE_URL` on Postgres.
- ~~`_init_users.py` (untracked, local only): one-off creation of the `users` table in Postgres.~~ → **RESOLVED** (C5): `users` is covered by Alembic migration 004 and applied by `docker-entrypoint.sh`; the helper has been deleted.
- Local Postgres: `audit_logs` FK constraints dropped (**revert per B3**).
