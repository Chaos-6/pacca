# PACCA Architecture

## System Overview

PACCA (Prior Authorization & Care Coordination Agent Platform) is a multi-agent AI system designed to automate healthcare prior authorization workflows while maintaining human oversight for complex cases.

This document covers: system architecture, data flow, component design, database strategy, and the Architecture Decision Records (ADRs) that explain why key choices were made. ADRs are first-class documentation — a system that cannot explain its own design decisions is a liability in a regulated environment.

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                                 PACCA Platform                                   │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                          React Frontend (SPA)                            │   │
│  │  ┌──────────┐  ┌──────────────┐  ┌────────────┐  ┌────────────────┐    │   │
│  │  │Dashboard │  │Authorization │  │   Detail   │  │  New Request   │    │   │
│  │  │          │  │    List      │  │    View    │  │     Form       │    │   │
│  │  └──────────┘  └──────────────┘  └────────────┘  └────────────────┘    │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                      │                                          │
│                                      ▼                                          │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                         FastAPI Backend (REST)                           │   │
│  │  ┌──────────────────────────────────────────────────────────────────┐   │   │
│  │  │  /api/v1/authorizations  │  /api/v1/metrics  │  /health         │   │   │
│  │  └──────────────────────────────────────────────────────────────────┘   │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                      │                                          │
│                                      ▼                                          │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                     Agent Orchestration Layer                            │   │
│  │                                                                          │   │
│  │  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐                │   │
│  │  │   Evidence   │   │  Clinical    │   │   Decision   │                │   │
│  │  │ Aggregation  │──▶│Classification│──▶│   Support    │                │   │
│  │  │    Agent     │   │    Agent     │   │    Agent     │                │   │
│  │  └──────────────┘   └──────────────┘   └──────────────┘                │   │
│  │         │                  │                  │                         │   │
│  │         └──────────────────┼──────────────────┘                         │   │
│  │                            ▼                                            │   │
│  │               ┌────────────────────────┐                                │   │
│  │               │    Orchestration       │                                │   │
│  │               │        Agent           │                                │   │
│  │               │  (Workflow Manager)    │                                │   │
│  │               └────────────────────────┘                                │   │
│  │                            │                                            │   │
│  └────────────────────────────┼────────────────────────────────────────────┘   │
│                               │                                                 │
│  ┌────────────────────────────┼────────────────────────────────────────────┐   │
│  │                      Data Layer                                          │   │
│  │                            │                                             │   │
│  │  ┌──────────┐  ┌──────────┴───────┐  ┌──────────┐  ┌──────────────┐   │   │
│  │  │PostgreSQL│  │    ChromaDB      │  │  Redis   │  │  Claude API  │   │   │
│  │  │          │  │  (Vector Store)  │  │ (Cache)  │  │  (Anthropic) │   │   │
│  │  │Requests  │  │   Guidelines     │  │          │  │              │   │   │
│  │  │Decisions │  │                  │  │Sessions  │  │    LLM       │   │   │
│  │  │Audit Logs│  │   RAG Search     │  │Rate Limit│  │  Reasoning   │   │   │
│  │  └──────────┘  └──────────────────┘  └──────────┘  └──────────────┘   │   │
│  └───────────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## Component Details

### Frontend (React + TypeScript)

- **Technology**: React 18, TypeScript, Tailwind CSS, Vite
- **Components**:
  - Dashboard with real-time metrics
  - Authorization list with filtering/search
  - Detail view with AI explanation
  - Human review interface
  - New request submission form

### Backend (FastAPI)

- **Technology**: Python 3.12, FastAPI, Pydantic v2
- **Endpoints**:
  - `POST /api/v1/authorizations` - Submit new request
  - `GET /api/v1/authorizations` - List with pagination
  - `GET /api/v1/authorizations/{id}` - Get status
  - `GET /api/v1/authorizations/{id}/explain` - Get decision rationale
  - `POST /api/v1/authorizations/{id}/review` - Submit human review
  - `GET /api/v1/metrics` - System metrics
  - `GET /health` - Health check

### Agent Framework

#### Base Agent
- Abstract class with LLM integration
- Structured output parsing
- Retry logic with exponential backoff
- Confidence scoring
- Error handling

#### Evidence Aggregation Agent
- Gathers clinical data from sources
- Synthesizes clinical narrative
- Identifies missing evidence
- Assesses evidence quality

#### Clinical Classification Agent
- Complexity scoring (1-5 scale)
- Specialty routing
- Urgency assessment
- Escalation triggers

#### Decision Support Agent
- Guideline retrieval via RAG
- Medical necessity evaluation
- Recommendation generation
- Chain-of-thought rationale

#### Orchestration Agent
- Workflow state machine
- Agent coordination
- Escalation logic
- Human-in-the-loop gates

### Data Layer

#### PostgreSQL
- Authorization requests
- Decisions with rationale
- Human reviews
- Complete audit trail
- Guideline metadata

#### ChromaDB (Vector Store)
- Clinical guideline content
- Semantic search
- RAG retrieval

#### Redis (Optional)
- Session management
- Rate limiting
- Caching

## Data Flow

### Authorization Submission Flow

```
1. User submits authorization request via Frontend
   │
2. FastAPI validates input with Pydantic
   │
3. OrchestrationAgent.process_authorization() called
   │
4. Evidence Aggregation Agent
   │  ├─ Gathers clinical data
   │  ├─ Calls Claude API for synthesis
   │  └─ Returns ClinicalEvidence + ClinicalNarrative
   │
5. Clinical Classification Agent
   │  ├─ Receives evidence context
   │  ├─ Calls Claude API for classification
   │  └─ Returns ComplexityLevel, Specialty, Urgency
   │
6. Decision Support Agent
   │  ├─ Retrieves guidelines via RAG (ChromaDB)
   │  ├─ Calls Claude API for recommendation
   │  └─ Returns DecisionOutput with rationale
   │
7. Orchestration Agent evaluates escalation
   │  ├─ Checks confidence thresholds
   │  ├─ Checks complexity thresholds
   │  ├─ Checks high-cost flags
   │  └─ Determines: autonomous vs escalated
   │
8. Decision persisted to database
   │
9. Response returned to user
```

### Human Review Flow

```
1. Case marked as "pending_review"
   │
2. Reviewer sees case in dashboard
   │
3. Reviewer examines:
   │  ├─ Clinical summary
   │  ├─ AI recommendation
   │  ├─ Escalation reasons
   │  └─ Chain-of-thought explanation
   │
4. Reviewer submits decision
   │
5. Decision updated in database
   │
6. Audit log created
```

## Workflow State Machine

```
                    ┌─────────────┐
                    │  SUBMITTED  │
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │  EVIDENCE   │
                    │  GATHERING  │
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │ CLASSIFYING │
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │ EVALUATING  │
                    └──────┬──────┘
                           │
              ┌────────────┴────────────┐
              │                         │
       ┌──────▼──────┐          ┌──────▼──────┐
       │  COMPLETED  │          │   PENDING   │
       │ (Autonomous)│          │   REVIEW    │
       └──────┬──────┘          └──────┬──────┘
              │                        │
    ┌─────────┼─────────┐       ┌──────▼──────┐
    │         │         │       │  IN_REVIEW  │
┌───▼───┐ ┌───▼───┐ ┌───▼───┐   └──────┬──────┘
│APPROVED│ │DENIED │ │ WITH  │          │
│        │ │       │ │ CONDS │   ┌──────▼──────┐
└────────┘ └───────┘ └───────┘   │  COMPLETED  │
                                 │  (Reviewed) │
                                 └─────────────┘
```

## Security Considerations

### Data Protection
- PHI minimization (no SSN, limited demographics)
- Audit logging for all access
- Role-based access control (RBAC)
- Encryption at rest and in transit

### AI Safety
- Confidence thresholds for autonomous decisions
- Mandatory human review for denials
- Escalation for high-risk cases
- Chain-of-thought explanations for transparency

### Compliance
- HIPAA-ready architecture
- Complete audit trail
- Decision explainability
- Human oversight mechanisms

## Scalability

### Horizontal Scaling
- Stateless API servers (scale behind load balancer)
- Database connection pooling
- Redis for distributed caching
- Async processing throughout

### Performance Targets
- API response: < 200ms (excluding LLM calls)
- End-to-end authorization: < 30s
- Throughput: 100+ authorizations/minute

## Deployment Options

### Development
```bash
# Start all services
docker-compose up -d

# Run API only (with SQLite)
uvicorn pacca.api.main:app --reload
```

### Production
- Kubernetes deployment
- Managed PostgreSQL (RDS, Cloud SQL)
- Managed Redis (ElastiCache, Cloud Memorystore)
- CI/CD via GitHub Actions

---

## Database Strategy

### Development vs. Production

PACCA uses a single codebase across both environments. The database engine is selected via one environment variable:

```bash
# Local development — SQLite, zero infrastructure required
DATABASE_URL=sqlite+aiosqlite:///./pacca.db

# Production — PostgreSQL 16, full feature set
DATABASE_URL=postgresql+asyncpg://pacca:password@db:5432/pacca
```

This works because the entire data layer is abstracted behind two layers:
1. **SQLAlchemy 2.0 ORM** — translates Python model operations to correct SQL dialect automatically
2. **Repository pattern** (`db/repository.py`) — routes and agents call `await audit.log(...)`, never touching a database engine directly

No route, agent, or business logic function imports from `sqlalchemy.dialects.postgresql` directly. Only `db/models.py` does, and SQLAlchemy handles the fallback to SQLite-compatible types transparently.

### Why PostgreSQL in Production

**JSONB columns.** `db/models.py` defines `audit_logs.details`, `audit_logs.token_usage`, and `decisions.rationale_data` as `JSONB`. In PostgreSQL this enables indexed queries inside JSON fields:
```sql
-- Find all decisions below 85% confidence for oncology cases
SELECT * FROM authorization_decisions
WHERE rationale_data->>'confidence_score' < '0.85'
AND request_id IN (
    SELECT request_id FROM authorization_requests
    WHERE assigned_specialty = 'oncology'
);
```
In SQLite, JSONB falls back to TEXT — queries still work, but without JSON-path indexing. Suitable for development; not for compliance reporting at scale.

**Concurrent write safety.** SQLite serializes all writes through a file lock. PostgreSQL uses row-level locking and MVCC (Multi-Version Concurrency Control). For a system claiming 500+ concurrent users, SQLite's file lock is a hard architectural limit.

**Connection pooling.** `db/session.py` configures `pool_size`, `max_overflow`, `pool_timeout`, and `pool_pre_ping`. These settings are live with PostgreSQL and no-ops with SQLite.

**High availability.** PostgreSQL on managed infrastructure (AWS RDS, Cloud SQL) provides automated backups, point-in-time recovery, and streaming replication. For a healthcare audit trail, losing `pacca.db` to a disk failure is a HIPAA incident.

---

## Architecture Decision Records (ADRs)

### ADR-001: Custom Agent Framework vs. LangChain / CrewAI

**Status:** Accepted

**Decision:** Custom agent base class (`agents/base.py`)

**Context:** Healthcare prior authorization requires deterministic escalation logic. Specific clinical conditions must trigger specific routing paths regardless of LLM output confidence.

**Rationale:** Framework abstractions (LangChain chains, CrewAI crews) obscure the control flow that compliance auditors need to inspect. A 150-line custom base class gives explicit, readable control over every agent handoff. The trade-off is maintenance ownership; the benefit is that every escalation decision is a readable conditional, not a framework callback.

**Consequence:** Agent framework updates require internal changes. This is acceptable given the compliance requirement for inspectable decision paths. The full 7-branch escalation tree lives in `agents/orchestrator.py` (workflow) and `agents/clinical_risk_detector.py` (policy rules), with one unit test per branch in `tests/unit/test_escalation_tree.py`.

---

### ADR-002: PostgreSQL as Primary Database

**Status:** Accepted

**Decision:** PostgreSQL 16 (production) with SQLite fallback (development)

**Context:** The data model has hard relational constraints and requires JSON-path querying of audit records for compliance reporting.

**Rationale:** PostgreSQL's native JSONB type, row-level locking, connection pooling, and replication support are requirements at production scale. SQLite is retained for local development because the ORM abstraction makes it a zero-cost option.

**Consequence:** `docker-compose.yml` requires a PostgreSQL service. Local development requires either Docker or explicitly setting `DATABASE_URL` to the SQLite option, then `make db-upgrade` (`alembic upgrade head`) to build the schema.

**Schema management (C5).** Alembic migrations are the single source of truth for the schema on both backends; the app no longer calls `create_all` at startup. Containers apply migrations via `docker-entrypoint.sh` (`alembic upgrade head`) before uvicorn accepts traffic, and a `migration-drift` CI job asserts the ORM models and the migrations agree (empty `autogenerate`) on Postgres. `audit_logs.request_id` carries **no foreign key** on either backend (migration 007, chg-23) — it did briefly, made deferrable on Postgres only (B3), but that depended on the audit write and its parent `authorization_requests` row sharing one commit. Once `AuditRepository.log()` started committing each audit row on an independent session (chg-23, so a business-transaction rollback can't erase an already-logged row), the deferred check ran before the parent row existed and rejected every request. Migration 007 dropped the FK outright; `request_id` remains a plain, indexed column, and correlation via `correlation_id` (never FK'd) is unaffected.

---

### ADR-003: ChromaDB Dual-Collection RAG

**Status:** Accepted

**Decision:** Two ChromaDB collections — `nccn_guidelines` (official rules) + `case_precedents` (human overrides)

**Context:** Clinical guidelines and institutional override decisions have different trust levels, update frequencies, and rollback requirements.

**Rationale:** Separating them into two collections allows independent versioning, different relevance-weighting in LLM prompts, and rollback of institutional learning without touching the authoritative guideline store. The precedents collection is the system's institutional memory — human overrides are embedded and stored; future similar cases retrieve them alongside official guidelines.

**Consequence:** The `integrations/vector_store.py` `GuidelineRetriever` maintains two collection references and queries both on every RAG call.

---

### ADR-004: Tool-Use API for Structured Agent Output

**Status:** Accepted

**Decision:** Force Claude's tool-use API for all agent responses

**Context:** Agent responses must be parseable Pydantic models. Free-form JSON parsing from LLM output fails unpredictably.

**Rationale:** Claude's tool-use API with `tool_choice: {type: "tool", name: "submit_result"}` forces the model to populate a JSON schema rather than generating free-form text. This makes structured output a guarantee enforced by the API, not a regex or `json.loads()` that can fail.

**Consequence:** All agent interactions require a tool definition derived from the response model's JSON schema. `base.py` handles this automatically via `response_model.model_json_schema()`.

---

### ADR-005: Repository Pattern for Data Access

**Status:** Accepted

**Decision:** Repository pattern (`AuthorizationRepository`, `DecisionRepository`, `AuditRepository`)

**Context:** Routes and agents need to read/write database records without coupling business logic to SQL.

**Rationale:** The repository pattern decouples business logic from database mechanics. `await audit.log(...)` is a stable interface regardless of whether the underlying engine is SQLite or PostgreSQL. This makes unit testing clean (mock the repository, not the database) and makes engine swaps a configuration change.

**Consequence:** New database operations require a corresponding repository method. Direct ORM access from routes is prohibited by convention.
