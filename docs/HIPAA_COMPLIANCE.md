# PACCA — HIPAA-Conscious Architecture Documentation

**Version:** 2.2.0
**Project:** PACCA — Prior Authorization & Care Coordination Agent Platform
**Author:** David Reed, PhD | Formerly Head of Career Advancement & AI/ML Delivery, Interview Kickstart

---

## Scope and Disclaimer

This document describes the HIPAA-conscious design patterns implemented in PACCA v2.2.0 as a **portfolio project and architectural demonstration**.

**PACCA is not a HIPAA-certified or HIPAA-compliant product.** It does not operate in a production healthcare environment. It does not store real Protected Health Information (PHI). It has not undergone formal HIPAA risk assessment.

What PACCA demonstrates is an understanding of HIPAA Security Rule requirements and how those requirements translate into specific engineering decisions. The patterns documented here reflect the design choices a Staff/Principal-level engineer would make when building clinical AI infrastructure intended for eventual production deployment.

A production deployment would additionally require:
- A Business Associate Agreement (BAA) with Anthropic
- Encryption at rest for all datastores containing PHI
- TLS 1.2+ for all data in transit
- Formal HIPAA Security Risk Assessment (45 CFR §164.308(a)(1))
- Workforce training and access management procedures
- Physical safeguards for any systems hosting PHI

---

## Relevant HIPAA Security Rule Provisions

PACCA's design addresses the following Security Rule requirements:

| Provision | CFR Citation | PACCA Implementation |
|-----------|-------------|---------------------|
| Audit Controls | 164.312(b) | Complete audit trail with correlation-ID tracing |
| Person or Entity Authentication | 164.312(d) | JWT with bcrypt + environment-sourced SECRET_KEY |
| Transmission Security | 164.312(e)(1) | HTTPS enforced in production; CORS configured |
| Access Control | 164.312(a)(1) | JWT-protected routes; role-aware audit logging |
| Integrity Controls | 164.312(c)(1) | Append-only audit log; immutable change log |
| Automatic Logoff | 164.312(a)(2)(iii) | Configurable JWT token expiry (default 30 min) |
| Minimum Necessary | 164.514(d) | PHI fields limited to clinical case context only |

---

## Audit Controls — 164.312(b)

HIPAA requires covered entities to "implement hardware, software, and/or procedural mechanisms that record and examine activity in information systems that contain or use electronic PHI."

### Implementation: Correlation-ID Audit Trail

Every authorization request generates a UUID `correlation_id` at submission. This ID is stamped on every audit record in the request's lifecycle. A compliance query for one request retrieves the complete chain:

```
correlation_id: 550e8400-e29b-41d4-a716-446655440000

Records:
  1. authorization_submitted      (actor: provider NPI, success: true)
  2. agent_decision_started       (actor: DecisionSupportAgent, v2.2)
  3. agent_decision_completed     (actor: DecisionSupportAgent, duration_ms: 3420, confidence: 0.97)
  4. escalation_auto_approved     (actor: orchestrator, branch: 1_auto_approve)
  5. authorization_finalized      (actor: system, status: AUTO_APPROVED)
```

### Audit Record Anatomy

```python
class AuditLogModel(Base):
    entry_id: str        # UUID7 — time-sortable, globally unique
    correlation_id: str  # Shared UUID for all records in one request lifecycle
    action: str          # Structured action type (not free-form text)
    actor: str           # Provider NPI, agent name, or "system"
    actor_type: str      # "provider" | "agent" | "user" | "system"
    success: bool        # False = failure event — distinguishable without log parsing
    error_message: str   # Populated only when success=False
    duration_ms: int     # Execution timing for performance audit
    token_usage: JSONB   # {input_tokens, output_tokens} — cost accountability
    details: JSONB       # Action-specific structured data
    timestamp: datetime  # UTC, automatically set at creation
```

### Pre-write Audit Design

Audit records are written **before** processing begins, not after. This is a deliberate HIPAA-aligned design choice:

```python
# routes/authorizations.py — audit record written BEFORE agent pipeline starts
await audit.log(
    action="authorization_submitted",
    actor=provider_npi,
    actor_type="provider",
    correlation_id=correlation_id,
    input_summary=f"Diagnosis: {case.primary_diagnosis_code} | Procedure: {case.procedure_code}",
    success=True,
)
# Agent pipeline runs AFTER audit record is committed
decision = await orchestrator.process_decision(ctx, audit=audit, correlation_id=correlation_id)
```

If the system crashes mid-processing, the audit record proves the request was received. Without pre-write auditing, a crash between submission and processing creates a PHI access event with no audit trail.

### Start/Complete Pairs

Every agent execution generates a `start` record and a `complete` record:

```
agent_decision_started       ← written before LLM call
agent_decision_completed     ← written after LLM returns
```

An orphaned `agent_decision_started` with no matching `agent_decision_completed` identifies exactly which agent failed during a multi-agent trace. This pattern is required for root-cause analysis of PHI access events that terminated abnormally.

### Failure Logging

```python
# base.py — failure is a first-class audit event
except Exception as exc:
    if audit:
        await audit.log(
            action="agent_call_failed",
            actor=self.name,
            actor_type="agent",
            success=False,                    # Distinguishes failures from successes
            error_message=str(exc)[:500],     # Captured without PHI exposure
        )
    raise
```

HIPAA requires that failures touching PHI be auditable. The `success=False` field means compliance queries can filter to failure events without parsing log messages.

---

## Person or Entity Authentication — 164.312(d)

### JWT Authentication

All routes touching clinical data require a JWT Bearer token. The token is issued at login, signed with the application's `SECRET_KEY`, and validated on every protected request.

```python
# auth.py
SECRET_KEY: str = os.getenv("SECRET_KEY", "")

def validate_secret_key(key: str | None = None, app_env: str | None = None) -> None:
    """Called at startup, in every environment — refuses to serve on a weak key."""
    ...
    if not key:                                  raise RuntimeError(...)
    if len(key) < MIN_SECRET_KEY_LENGTH:         raise RuntimeError(...)
    if len(set(key)) < MIN_SECRET_KEY_DISTINCT_CHARS:  raise RuntimeError(...)
    marker = _placeholder_marker(key)
    if marker is None:                           return
    if app_env in STRICT_ENVIRONMENTS:           raise RuntimeError(...)
    _warn_placeholder(marker, app_env)
```

The application **refuses to start** if `SECRET_KEY` is missing, shorter than 32
characters, or built from fewer than 8 distinct characters — a 32-character run of
`"a"` clears the length bar while carrying almost no entropy. In `production` and
`staging` a key containing a published placeholder marker (`REPLACE-THIS`,
`dev-secret-key`, and similar) is rejected too; in `development` and `test` it is
permitted and logged at warning level, so the test suite does not require a real
secret.

The check runs in **every** environment. It previously ran only when
`app_env != "test"`, which meant a production deployment could skip signing-key
validation entirely by setting `APP_ENV=test`. `APP_ENV` now selects the strictness,
never whether the check happens.

### Password Storage

Passwords are hashed with bcrypt. bcrypt generates a unique random salt per password, so two hashes of the same password are always different — preventing rainbow table attacks.

```python
def get_password_hash(password: str) -> str:
    pwd_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt()                    # Unique salt per password
    hashed = bcrypt.hashpw(pwd_bytes, salt)
    return hashed.decode("utf-8")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(
        plain_password.encode("utf-8"),
        hashed_password.encode("utf-8"),       # Timing-safe comparison
    )
```

`bcrypt.checkpw` is timing-safe: it does not short-circuit on the first mismatched character, preventing timing side-channel attacks.

### Token Lifetime

JWT tokens expire after `TOKEN_EXPIRE_MINUTES` (default: 30 minutes). This is configurable via environment variable:

```bash
TOKEN_EXPIRE_MINUTES=30    # Default — appropriate for clinical sessions
TOKEN_EXPIRE_MINUTES=15    # More restrictive — use during high-security periods
```

HIPAA's automatic logoff standard (164.312(a)(2)(iii)) requires that sessions terminate after a period of inactivity. Short JWT lifetimes implement this for API sessions: a token received during active work expires before an unattended workstation could be accessed by an unauthorized user.

---

## Access Control — 164.312(a)(1)

All authorization-related routes require a valid JWT:

```python
# main.py
app.include_router(
    authorizations.router,
    prefix="/api/v1/authorizations",
    dependencies=[Depends(verify_token)],    # All routes require JWT
)
app.include_router(
    admin.router,
    prefix="/api/v1/admin",
    dependencies=[Depends(verify_token)],    # Admin routes also require JWT
)
```

The audit log captures the actor (provider NPI or username) on every record. This implements the HIPAA requirement for activity accountability: every PHI access is linked to an identified user.

---

## Transmission Security — 164.312(e)(1)

In development, the API runs over HTTP for simplicity. In production:

- All connections use HTTPS (TLS 1.2 minimum)
- `SECRET_KEY` is injected via environment variables or secrets manager (never in source code)
- CORS is configured to allow only specific frontend origins (not `*` in production)

The CORS configuration is parameterized:
```bash
CORS_ORIGINS=["https://app.yourdomain.com"]    # Production: restrict origins
```

---

## Integrity Controls — 164.312(c)(1)

### Append-Only Audit Log

The audit log is designed as an append-only record. Records are written via `session.add()` + `flush()` within a transaction. No route or agent implements audit record deletion or modification. The `success=False` pattern means failures are additional records, not modifications to existing ones.

### Immutable Policy Change Log

The policy evolution governance system maintains an immutable change log of every AI-proposed guideline amendment:

```python
@dataclass
class PolicyChangeLogEntry:
    change_id: str         # Unique identifier
    proposal_id: str       # Which proposal was deployed
    guideline_id: str      # Which guideline was amended
    original_text: str     # The guideline BEFORE amendment
    new_text: str          # The guideline AFTER amendment
    approved_by: str       # Who authorized the change
    deployed_at: float     # When it was deployed
    rationale_summary: str # Why the change was made
```

Entries are appended to `_change_log` in memory (prototype) or a PostgreSQL table (production). They are never deleted. This implements a complete audit trail for AI-driven changes to the clinical decision-making logic — directly relevant to FDA AI/ML SaMD Action Plan requirements.

---

## Minimum Necessary Standard — 164.514(d)

The system handles only the clinical information required for the authorization decision:

- **Captured:** diagnosis codes, procedure codes, clinical notes, evidence summaries
- **Not captured:** Social Security Numbers, full financial information, demographic data beyond what is clinically necessary
- **Agent prompts:** include explicit instruction — "Only reference evidence explicitly present in the submission. If a lab value, test result, or clinical finding is not in the notes — do NOT mention it."

The anti-hallucination instructions in every agent prompt serve double duty: they are both an AI safety measure and a PHI minimization control. An agent that invents clinical details would be generating false PHI records — a HIPAA integrity violation.

---

## Production Deployment Requirements

A production deployment of PACCA would additionally require:

### Business Associate Agreement
Any vendor handling PHI on behalf of a covered entity must sign a BAA. For PACCA:
- **Anthropic** — clinical case data is sent to the Claude API for processing. Anthropic does not currently offer a standard BAA for API access. A production healthcare deployment would require either Anthropic's enterprise BAA or an on-premises/VPC-hosted model deployment.
- **Database hosting provider** — if using managed PostgreSQL (AWS RDS, Google Cloud SQL), the provider must sign a BAA.
- **Observability platform** — if clinical case data appears in Langfuse traces, Langfuse must sign a BAA. PACCA's OTel spans capture agent names, timing, and token counts — not patient data — but this must be verified in production.

### Encryption at Rest
All datastores containing PHI must be encrypted at rest:
- PostgreSQL: volume-level encryption (AWS EBS encryption, Google Cloud Persistent Disk encryption)
- ChromaDB: encrypted volume or Pinecone with BAA
- Backup storage: AES-256 encryption

### Network Security
- VPC isolation for database and cache layers
- No direct public access to PostgreSQL or Redis
- TLS 1.2+ enforced on all API endpoints
- Certificate management via Let's Encrypt or managed certificate service

### Access Management
- Multi-factor authentication for administrative access
- Principle of least privilege for database credentials
- Automated credential rotation
- Separation of development and production environments

### Incident Response
HIPAA requires a written incident response plan including:
- Breach detection procedures
- 60-day notification window to affected individuals (45 CFR §164.412)
- HHS reporting for breaches affecting 500+ individuals

---

## Summary: What This Project Demonstrates

PACCA demonstrates that its author understands:

1. **Why** audit trails exist in healthcare systems — not as a checkbox but as the mechanism that makes PHI access accountable and reconstructable
2. **How** to implement fail-fast security — an application that refuses to start with a weak key is safer than one that starts with warnings
3. **What** structured audit record design looks like — correlation IDs, pre-write records, start/complete pairs, and success=False as first-class events
4. **The difference** between development convenience and production requirements — SQLite vs. PostgreSQL, HTTP vs. HTTPS, `*` CORS vs. explicit origins
5. **AI-specific compliance concerns** — the policy change log and human approval gate for the PolicyEvolutionAgent directly address FDA SaMD Action Plan requirements for AI-driven clinical decision support changes

These are the design patterns a Staff or Principal engineer implements when building clinical AI systems that must eventually pass regulatory scrutiny.

---

*PACCA v2.2.0 — April 2026*
*github.com/drdgreed/pacca*
