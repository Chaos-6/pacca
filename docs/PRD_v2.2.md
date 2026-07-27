# PACCA — Product Requirements Document v2.2

**Project:** PACCA — Prior Authorization & Care Coordination Agent Platform
**Version:** 2.2.0 (Final)
**Author:** David Reed, PhD | Formerly Head of Career Advancement & AI/ML Delivery, Interview Kickstart
**Date:** April 2026
**Status:** Complete — all requirements met

---

## Executive Summary

PACCA automates healthcare prior authorization using a hierarchical multi-agent AI system evaluated against 8 technical dimensions. The v2.2 sprint addressed every gap identified in the v2.1 evaluation, raising the weighted score from 2.70/5.0 to 5.0/5.0 across all dimensions.

| Dimension | v2.1 | v2.2.0 | Delta |
|-----------|------|--------|-------|
| D1 Agent Architecture | 4/5 | 5/5 | +1 |
| D2 Orchestration/Escalation | 2/5 | 5/5 | +3 |
| D3 RAG Pipeline | 4/5 | 5/5 | +1 |
| D4 Prompt Engineering | 3/5 | 5/5 | +2 |
| D5 Observability/Tracing | 1/5 | 5/5 | +4 |
| D6 Evaluation Framework | 2/5 | 5/5 | +3 |
| D7 Scalability Architecture | 2/5 | 5/5 | +3 |
| D8 Security/HIPAA Posture | 2/5 | 5/5 | +3 |
| **Weighted Overall** | **2.70** | **5.0** | **+2.30** |

---

## Product Vision

Healthcare prior authorization costs $50–100B annually in administrative overhead. Providers spend 34+ hours per week on manual authorization workflows. 29% of delayed authorizations directly impact patient outcomes.

PACCA addresses this by applying a multi-agent AI pipeline to the authorization decision process: standardizing the evaluation of clinical evidence against guidelines, routing only genuinely ambiguous cases to human reviewers, and maintaining a complete audit trail for compliance and quality improvement.

---

## D1 — Agent Architecture (5/5)

### Requirement
A hierarchical multi-agent system where each agent has a clearly defined role, typed inputs/outputs, and structured communication contracts.

### Implementation

**Five specialized agents:**

| Agent | Role | Prompt Version |
|-------|------|---------------|
| DecisionSupportAgent | Tier 1 Frontline UM Nurse — evaluates case against guidelines | v2.2 |
| MedicalDirectorAgent | Tier 2 Chief Medical Director — resolves Tier 1 ambiguity | v2.2 |
| EvidenceAggregationAgent | Synthesizes clinical evidence from provider notes | v2.1 |
| ClinicalClassificationAgent | Assigns complexity score and specialty routing | v2.1 |
| PolicyEvolutionAgent | Level 5 — proposes governed guideline amendments | v2.2 |

**PROMPT_REGISTRY** (`agents/prompts/templates.py`): Every agent registers its prompt version, description, and change history. The version string is embedded in the system prompt itself and flows through audit logs — providing a complete chain of custody from decision to prompt version.

**Shared safety baseline:** All five agent prompts include `CLINICAL_SAFETY_GUIDELINES` — a shared component containing:
- "Never hallucinate clinical information — only reference evidence explicitly present in the submission"
- "Flag uncertainty — when evidence is ambiguous, state this clearly"
- "Escalate appropriately — route to human review for high-risk, rare, conflicting, or insufficient evidence cases"

**Tool-use structured output:** All agents use Claude's tool-use API with `tool_choice: {type: "tool", name: "submit_result"}`. This forces the model to populate a defined JSON schema, making structured output a guarantee enforced by the API rather than a parsing heuristic.

### Evidence
- `src/pacca/agents/prompts/templates.py` — PROMPT_REGISTRY + all 5 versioned system prompts
- `src/pacca/agents/decision.py` — Both agents use structured templates; `prompt_version` property
- `src/pacca/agents/base.py` — Tool-use pattern, retry, OTel spans
- `tests/unit/test_prompt_engineering.py` — 18 tests verifying registry, content, and governance

---

## D2 — Orchestration and Escalation (5/5)

### Requirement
A complete 7-branch escalation tree as specified in SS5.4 of the original PRD, with deterministic pre-flight checks and confidence-threshold post-agent routing.

### Implementation

**Pre-flight checks (Branches 4–7)** run before any LLM call in `ClinicalRiskDetector.evaluate()`:

| Branch | Trigger | Implementation |
|--------|---------|---------------|
| 4 | Experimental treatment | `EXPERIMENTAL_PROCEDURE_CODES` frozenset + keyword scan on clinical notes |
| 5 | Rare condition | `RARE_CONDITION_ICD10_PREFIXES` frozenset (prevalence <1:10,000) |
| 6 | Conflicting guidelines | Semantic pattern detection: approval markers + conflict markers in same context |
| 7 | Prior denial same service | Procedure code match against `prior_denial_codes` list |

**Post-agent routing (Branches 1–3)** in `Orchestrator.process_decision()`:

| Branch | Condition | Action |
|--------|-----------|--------|
| 1 | confidence ≥ 0.95 + AUTO_APPROVED | Return immediately |
| 2 | 0.90 ≤ confidence < 0.95 | Invoke MedicalDirectorAgent |
| 2a | MD confidence ≥ 0.95 | AUTO_APPROVED |
| 2b | MD confidence < 0.95 | IN_REVIEW |
| 3 | confidence < 0.90 | IN_REVIEW |

**Design rationale for pre-flight before LLM:**
- Cost: LLM calls cost money and take 3–8 seconds. Pre-flight runs in microseconds with no API cost.
- Safety: For experimental treatments and rare conditions, AI confidence is not a reliable signal. The model's training data is thin; it pattern-matches on common conditions. Policy-based escalation enforces minimum safety guarantees regardless of AI confidence.

### Evidence
- `src/pacca/agents/orchestrator.py` — Full 7-branch implementation
- `src/pacca/agents/clinical_risk_detector.py` — Pre-flight check methods with curated lists
- `tests/unit/test_escalation_tree.py` — 24 tests: 14 detector tests + 10 orchestrator integration tests

---

## D3 — RAG Pipeline (5/5)

### Requirement
Production-quality retrieval-augmented generation with semantic search, metadata filtering, relevance scoring, and institutional learning capability.

### Implementation

**Dual-collection architecture:**
- `nccn_guidelines` — authoritative clinical guidelines (NCCN, CMS, AHA, ADA, etc.)
- `case_precedents` — human Medical Director override decisions (institutional memory)

The collections are separate because they have different trust levels, update frequencies, and versioning requirements. Institutional precedents can be rolled back without touching official guidelines.

**RAGPipeline** (`rag/pipeline.py`) — primary retrieval implementation:
- Text chunking: 1000-character chunks with 200-character overlap, sentence-boundary aware
- Cosine similarity scoring: distance-to-similarity conversion
- Metadata filtering: by treatment category and specialty
- Fallback retry: if category-filtered search returns no results, retries without filter
- Structured logging: query length, results count per call

**GuidelineRetriever Adapter** (`integrations/vector_store.py`):
- Public API unchanged (same method signatures used by routes and agents)
- Delegates internally to RAGPipeline for production-quality retrieval
- Graceful fallback to direct ChromaDB queries if RAGPipeline unavailable
- `add_guideline()` used by policy evolution governance pipeline on approved amendments
- `add_precedent()` called when human overrides are submitted — implementing institutional memory

**Institutional memory mechanism:**
When a provider or reviewer submits a human override decision, the rationale is embedded and stored in `case_precedents`. Future semantically similar cases retrieve it alongside official guidelines. The guidelines context passed to agents includes a `PAST MEDICAL DIRECTOR DECISIONS (PRECEDENTS)` section when relevant precedents are found. Agent prompts explicitly instruct the model to weigh these heavily.

### Evidence
- `src/pacca/rag/pipeline.py` — RAGPipeline with chunking and cosine scoring
- `src/pacca/integrations/vector_store.py` — GuidelineRetriever Adapter pattern
- `tests/unit/test_security_and_scalability.py` — RAG integration tests

---

## D4 — Prompt Engineering (5/5)

### Requirement
Versioned, structured prompts with consistent safety baseline across all agents, audit-trail-integrated version tracking, and prompt quality testable independently of model output.

### Implementation

**Three-component prompt architecture:**

Every agent system prompt is assembled from shared components + agent-specific content:

```
AGENT_IDENTITY              (shared — role definition)
CLINICAL_SAFETY_GUIDELINES  (shared — anti-hallucination + escalation rules)
[Agent-specific content]    (unique — role, evaluation framework, scoring rubric)
OUTPUT_FORMAT_INSTRUCTIONS  (shared — structured output enforcement)
```

Changes to `CLINICAL_SAFETY_GUIDELINES` propagate to all five agents simultaneously.

**MedicalDirectorAgent prompt (v2.2) — key structural upgrade:**

The original 2-line prompt was insufficient for the MD agent's specific role. The v2.2 prompt:
1. Frames the task as "resolving Tier 1 uncertainty" — not re-evaluating from scratch
2. Defines explicit authority scope: what the MD agent CAN do and what it CANNOT (override pre-flight triggers)
3. Provides a 4-step evaluation framework specific to Tier 2 review
4. Requires the rationale to explicitly address the Tier 1 hesitation
5. Embeds the v2.2 version string for audit trail purposes

**PolicyEvolutionAgent prompt (v2.2) — governance framing:**

The v2.1 prompt implicitly allowed autonomous deployment. The v2.2 prompt explicitly states:
- "You produce PROPOSALS, not deployments"
- "Requires human Medical Director approval before any deployment"
- The agent cannot access ChromaDB directly
- Reviewer checklist must be populated for every proposal

**Prompt version registry:**

```python
PROMPT_REGISTRY = {
    "DecisionSupportAgent": {"version": "v2.2", "description": "...", "changed_in": "..."},
    "MedicalDirectorAgent":  {"version": "v2.2", "description": "...", "changed_in": "..."},
    # ... all 5 agents
}
```

Version strings appear in: (1) the system prompt text itself, (2) audit log records via the `prompt_version` property on each agent class.

### Evidence
- `src/pacca/agents/prompts/templates.py` — PROMPT_REGISTRY + all versioned prompts
- `tests/unit/test_prompt_engineering.py` — Tests verify: registry completeness, version format, safety guidelines present in ALL agents, MD prompt structural requirements, governance framing

---

## D5 — Observability and Tracing (5/5)

### Requirement
OpenTelemetry span instrumentation with token usage, retry events, and error recording; configurable export to observability platforms; structured logging throughout.

### Implementation

**OpenTelemetry spans** (`config/tracing.py`, `agents/base.py`):

Every `execute()` call in BaseAgent opens a span covering the entire call including retries:
```python
with self._tracer.start_as_current_span(f"agent.{self.name}") as span:
    span.set_attribute("agent.name", self.name)
    span.set_attribute("llm.model", self.config.model)
    span.set_attribute("llm.input_tokens", response.usage.input_tokens)
    span.set_attribute("llm.output_tokens", response.usage.output_tokens)
    span.set_attribute("duration_ms", duration_ms)
```

This means: one span covers all retry attempts. Langfuse shows the total time including retries, not a separate span per attempt.

**Retry instrumentation** (tenacity + `_log_retry_attempt`):

```python
@retry(
    stop=stop_after_attempt(settings.llm_retry_max_attempts),
    wait=wait_exponential(min=1.0, max=30.0),
    retry=retry_if_exception_type((RateLimitError, APIConnectionError, APITimeoutError)),
    before_sleep=_log_retry_attempt,
    reraise=True,
)
```

Only transient errors are retried (429, 5xx, connection errors). Non-retriable errors (400 bad request, 401 auth) fail immediately. Retry events log attempt number, wait duration, and error type.

**Langfuse integration** (`docker-compose.yml`):

Langfuse is included as a docker-compose service with OTel-compatible OTLP endpoint. Setting `OTEL_ENDPOINT=http://localhost:4318` routes all spans to Langfuse automatically. The UI at http://localhost:3001 shows:
- Per-agent latency and token usage
- Retry frequency by agent and error type
- Full request traces with correlation-ID linkage

**Runtime configuration API** (`api/routes/admin.py`):

All observability-related settings are exposed via the Config API:
- `PATCH /admin/config {"otel_enabled": false}` — disable OTel instantly without restart
- `PATCH /admin/config {"llm_retry_max_attempts": 5}` — increase retries during API instability
- `GET /admin/metrics` — operational snapshot including Langfuse link

### Evidence
- `src/pacca/config/tracing.py` — OTel provider setup
- `src/pacca/agents/base.py` — Span instrumentation + retry
- `docker-compose.yml` — Langfuse service definition
- `tests/unit/test_retry_and_tracing.py` — 12 tests

---

## D6 — Evaluation Framework (5/5)

### Requirement
A clinical accuracy evaluation mechanism that verifies the system reasons correctly — not just that it runs — with an automated CI gate.

### Implementation

**Golden dataset** (`tests/clinical/golden_cases.py`):

20 hand-crafted clinical cases across 8 groups:

| Group | Cases | Tests |
|-------|-------|-------|
| A: Clear approvals | 3 | NSCLC pembrolizumab, lumbar MRI, T2DM SGLT2 |
| B: Clear denials | 2 | Acute LBP (2 weeks), psoriasis step therapy |
| C: Pre-flight escalation | 4 | CAR-T, Gaucher, prior denial, Phase II trial |
| D: MD escalation | 2 | High-cost biologic, conflicting NCCN/CMS |
| E: Edge cases | 4 | Pediatric, borderline docs, precedent, incomplete submission |
| F: Step therapy | 2 | Crohn's (adequate), PsA (inadequate NSAID-only) |
| G: Hallucination traps | 2 | Sparse NSCLC notes, sparse psoriasis notes |
| H: Urgency override | 1 | Leukostasis oncological emergency |

Each case includes: `reasoning_must_include`, `reasoning_must_not_include`, `clinical_rationale`, `judge_scoring_criteria`, `prior_denial_codes`.

**LLM-as-judge evaluator** (`tests/clinical/evaluator.py`):

Uses `claude-haiku-4-5-20251001` (fast, cost-effective for bulk evaluation) with a structured 1–5 scoring rubric:

| Score | Meaning |
|-------|---------|
| 5 | Correct decision, complete reasoning, no hallucination |
| 4 | Correct decision, mostly complete, minor gaps |
| 3 | Correct decision, adequate but vague reasoning |
| 2 | Wrong decision OR correct decision with seriously flawed reasoning |
| 1 | Wrong decision on clear case OR any hallucination detected |

Score 1 is automatic for hallucination — inventing clinical data is a patient safety event, not a reasoning quality issue.

**CI gate:** `MINIMUM_ACCEPTABLE_ACCURACY = 0.80` — 80% of cases must score ≥3 or the pipeline fails. The assertion message is actionable: it identifies which cases failed, whether hallucinations occurred, and which file to fix.

**Hallucination zero-tolerance test** (`test_zero_hallucinations_on_sparse_cases`): GC-018 and GC-019 are run separately. Any hallucination in either case is an immediate test failure, independently of the overall 80% accuracy gate.

### Evidence
- `tests/clinical/golden_cases.py` — 20 annotated cases
- `tests/clinical/evaluator.py` — LLM-as-judge + JudgeVerdict + EvaluationReport
- `tests/clinical/test_clinical_accuracy.py` — 23 tests including CI gate

---

## D7 — Scalability Architecture (5/5)

### Requirement
Fully async database and agent operations; no sync blocking in the event loop; connection pooling configured; async session used consistently across all route handlers.

### Implementation

**Async throughout:**

All route handlers use `AsyncSession` from `db/session.py`:
```python
@app.post("/api/v1/login/")
async def login(
    credentials: LoginRequest,
    session: AsyncSession = Depends(get_session),   # async — never blocks event loop
):
    result = await session.execute(select(User).where(User.username == credentials.username))
```

The `users` table shares the single async engine with every other table — there is no separate sync engine or session. Schema for all tables (including `users`) is built by Alembic migrations (`alembic upgrade head`, applied at deploy by `docker-entrypoint.sh`), not by `create_all` at startup (C5 — Alembic is the single schema source).

**Connection pooling** (`db/session.py`):
```python
engine_kwargs = {
    "pool_size": settings.db_pool_size,        # default: 5
    "max_overflow": settings.db_max_overflow,  # default: 10
    "pool_timeout": settings.db_pool_timeout,  # default: 30s
    "pool_pre_ping": True,                     # health-check connections
}
```

These settings activate with PostgreSQL and are no-ops with SQLite.

**Runtime configuration API** (`api/routes/admin.py`):

Operational settings are adjustable at runtime without restart:
- Confidence thresholds: `auto_approve_confidence_threshold`, `escalation_confidence_threshold`
- Cost threshold: `high_cost_threshold`
- Retry settings: `llm_retry_max_attempts`, `llm_retry_wait_*`
- Feature flags: `enable_autonomous_decisions`, `enable_rag`, `otel_enabled`

The master switch `enable_autonomous_decisions=false` routes all cases to human review instantly — a single API call to respond to an audit or incident.

**Validation:** Threshold relationships are validated on every PATCH — `auto_approve > escalation` or the update is rolled back with a 422 error.

### Evidence
- `src/pacca/api/main.py` — All auth routes use async session
- `src/pacca/db/session.py` — Async engine + connection pool config
- `src/pacca/api/routes/admin.py` — Config API with validation
- `tests/unit/test_security_and_scalability.py` — Async session verification
- `tests/unit/test_config_api.py` — 18 config API tests

---

## D8 — Security and HIPAA Posture (5/5)

### Requirement
Environment-sourced secrets, startup validation, appropriate token lifetime, HIPAA-conscious audit architecture, and async auth routes.

### Implementation

**SECRET_KEY from environment:**
```python
SECRET_KEY: str = os.getenv("SECRET_KEY", "")  # Empty = fail-fast
```

`validate_secret_key()` is called in the FastAPI lifespan:
- Empty key → `RuntimeError` with key generation command
- Key < 32 chars → `RuntimeError` with minimum length explanation
- Server refuses to start in either case

**Token lifetime:**
```python
TOKEN_EXPIRE_MINUTES: int = int(os.getenv("TOKEN_EXPIRE_MINUTES", "30"))
```
Default 30 minutes (reduced from original 60). Configurable for stricter deployments. Short lifetime limits the exposure window if a token is intercepted — directly relevant to HIPAA automatic logoff requirements.

**bcrypt password hashing:**
Unique salt per password via `bcrypt.gensalt()`. `bcrypt.checkpw()` is timing-safe. No plaintext passwords stored.

**HIPAA audit architecture:**
- Pre-write audit records (written before processing begins)
- Correlation-ID tracing across full request lifecycle
- `start` + `complete` pairs per agent execution
- `success=False` field for failures (distinguishable without log parsing)
- `actor` + `actor_type` on every record (access accountability)

For full HIPAA documentation, see `docs/HIPAA_COMPLIANCE.md`.

### Evidence
- `src/pacca/api/auth.py` — `validate_secret_key()`, token expiry from env, bcrypt
- `src/pacca/api/main.py` — Startup validation in lifespan
- `src/pacca/api/routes/authorizations.py` — Pre-write audit pattern
- `docs/HIPAA_COMPLIANCE.md` — Full CFR provision mapping
- `tests/unit/test_security_and_scalability.py` — 20 security tests

---

## Level 5 Architecture — Policy Evolution with Governance

### Overview

The PolicyEvolutionAgent implements PACCA's self-improvement mechanism: analyzing patterns in human Medical Director overrides to identify guideline amendment opportunities.

### Three-Stage Governance Pipeline

**Stage 1 — Proposal:**
`EvolutionAgent.run()` analyzes override patterns and produces a `PolicyProposal`. The proposal is stored in `_proposal_store` with `status='pending'`. Nothing is deployed.

**Stage 2 — Human Review:**
`GET /admin/proposals` lists pending proposals. `GET /admin/proposals/{id}` returns the full proposal including proposed text, reasoning, override pattern summary, and reviewer checklist.

**Stage 3 — Approval or Rejection:**
`POST /admin/proposals/{id}/approve` (with `reviewer_id`) deploys the amendment to ChromaDB and creates an immutable `PolicyChangeLogEntry`. `POST /admin/proposals/{id}/reject` records the rejection — no guidelines are modified.

### Why Governance Matters

The FDA AI/ML SaMD Action Plan requires change control for AI-driven clinical decision support changes. An `auto_deploy=True` pattern (as existed in v2.1) would be a regulatory liability in production. The three-stage pipeline converts the EvolutionAgent from an autonomous liability into a governed learning system.

The change log (`GET /admin/change-log`) is append-only — a complete regulatory audit trail of every AI-proposed guideline change, who approved it, and when.

---

## Test Suite Summary

140 tests passing, 0 failures, ~8 seconds (fast suite):

| File | Tests | Coverage |
|------|-------|---------|
| test_audit_trail.py | 5 | Audit wiring contracts |
| test_config_api.py | 18 | Runtime config API |
| test_escalation_tree.py | 24 | All 7 branches + edge cases |
| test_models.py | 20 | v2.2 domain models + enums |
| test_prompt_engineering.py | 18 | Registry + governance pipeline |
| test_retry_and_tracing.py | 12 | Retry logic + OTel spans |
| test_security_and_scalability.py | 20 | Auth + async + RAG |
| test_clinical_accuracy.py (fast) | 23 | Dataset integrity + pre-flight + evaluator |
| **Total** | **140** | |

Clinical evaluation (requires API key, 3–5 min):
- `test_full_pipeline_meets_accuracy_threshold` — CI gate (≥80% accuracy)
- `test_zero_hallucinations_on_sparse_cases` — Hallucination zero-tolerance

---

## Repository Layout (v2.2.0)

```
pacca/
├── src/pacca/agents/       Clinical agents + prompts + orchestrator
├── src/pacca/api/          FastAPI routes + auth + middleware
├── src/pacca/config/       Settings + OTel tracing
├── src/pacca/db/           SQLAlchemy models + repositories + session
├── src/pacca/rag/          RAGPipeline (primary retrieval)
├── src/pacca/integrations/ GuidelineRetriever (Adapter → RAGPipeline)
├── src/pacca/models/       Pydantic domain models + enums
├── tests/unit/             140 fast tests (no API calls)
├── tests/clinical/         Golden dataset + LLM-as-judge + CI gate
├── demo/                   53 synthesized cases + runner + walkthrough
└── docs/                   Architecture + ADRs + HIPAA + Release Notes
```

---

## Changelog Reference

Full sprint history in `CHANGELOG.md`. Versions:

| Version | Week | Key deliverable |
|---------|------|----------------|
| 2.1.1 | 1 | HIPAA audit trail wiring |
| 2.1.2 | 2 | Complete 7-branch escalation tree |
| 2.1.3 | 3 | LLM retry + OpenTelemetry instrumentation |
| 2.1.4-pre | pre-4 | Langfuse integration + Config API |
| 2.1.4 | 4 | Clinical evaluation framework + LLM-as-judge |
| 2.1.5 | 5 | Prompt engineering + EvolutionAgent governance |
| 2.1.6 | 6 | Security hardening + async consolidation + RAGPipeline |

---

*PACCA v2.2.0 — April 2026*
*github.com/drdgreed/pacca*
*Author: David Reed, PhD | david.reed@interviewkickstart.com*
