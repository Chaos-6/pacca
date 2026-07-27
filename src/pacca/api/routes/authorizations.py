"""
Authorization routes for the PACCA API.

This module handles prior authorization submission, status retrieval,
decision explanation, human review submission, and the learning feedback
loop (Level 4 / Level 5 architecture).

Audit logging is wired at every significant action so that every
authorization event is traceable for HIPAA compliance and debugging.

Teaching note — why audit logging matters here:
  HIPAA Security Rule 164.312(b) requires that any system touching
  Protected Health Information (PHI) maintains audit controls that
  record and examine system activity. Prior authorization requests
  contain PHI (patient IDs, diagnosis codes, clinical notes). Every
  submission, decision, override, and learning event must be logged.
"""

import time
from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

# The agents that do the clinical reasoning
from ...agents.orchestrator import DecisionContext, Orchestrator

# Minimum-necessary scope guard (P-4 / chg-8) + the fail-closed escalation path.
from ...agents.scope_guard import ScopeViolation, enforce_scope

# get_settings() is the cached, process-start snapshot; effective_settings()
# merges the PATCH /config runtime-override store on top of it (the repo
# convention for tunables — see orchestrator.py, clinical_risk_detector.py,
# base.py). rag_degraded_escalates (chg-20) reads effective_settings() so it
# is genuinely flippable via PATCH /config without an env change + restart.
# scope_guard_mode below still reads get_settings() — a pre-existing pattern
# in this file, out of scope for chg-20 to change.
from ...config.settings import effective_settings, get_settings

# The repository that writes audit records.
# A "repository" is a design pattern that wraps all database operations
# for one type of data. AuditRepository handles everything related to
# the audit_logs table. You call audit.log(...) and it handles the SQL.
from ...db.repository import AuditRepository, AuthorizationRepository, DecisionRepository

# The proper async database session — this replaces the sync api/database.py session.
# Teaching note: get_session is a FastAPI "dependency". When you write
#   session: AsyncSession = Depends(get_session)
# FastAPI automatically opens a session before your route runs,
# passes it in as the `session` argument, and closes + commits it
# after your route returns. You never have to remember to close it.
from ...db.session import get_session

# The RAG engine that retrieves relevant guidelines from ChromaDB
from ...integrations.vector_store import (
    PRECEDENT_COLLECTION,
    RAG_COLLECTIONS,
    GuidelineRetriever,
)

# Our domain models (Pydantic schemas for request/response shapes)
from ...models.authorization import AuthorizationDecision, AuthorizationRequest
from ...models.enums import AuthorizationStatus, EscalationReason, ReviewTier

# The per-run intent contract (P-3 / chg-7): declared and audited at run start.
from ...models.intent import IntentRecord
from ..auth import verify_token
from ..models.user import User
from ..rbac import Role, require_min_role

router = APIRouter()

# These are module-level singletons — created once when the server starts,
# reused for every request. Creating them once is efficient because
# initializing the LLM client and RAG engine has overhead.
orchestrator = Orchestrator()
rag_engine = GuidelineRetriever()

# require_min_role(...) is a dependency FACTORY — bound once here (rather than
# called inline inside each Depends(...) default below) so it is evaluated a
# single time at import, not once per route definition.
_require_medical_director = require_min_role(Role.MEDICAL_DIRECTOR)


class FeedbackRequest(BaseModel):
    """
    Schema for the Level 4 learning loop endpoint.

    When a human reviewer overrides an AI decision, they submit feedback
    here. The system stores this as a "case precedent" in ChromaDB so
    future similar cases benefit from the institutional knowledge.

    Attributes:
        case_summary: Brief description of the clinical scenario
        decision:     The correct outcome (e.g. "AUTO_APPROVED")
        rationale:    Why the human made this decision (stored in vector DB)
    """

    case_summary: str
    decision: str
    rationale: str


class ReviewQueueItem(BaseModel):
    """
    One case awaiting Medical Director review.

    Field names mirror what the queue UI renders, so the typed frontend boundary
    is a direct translation of this model rather than a reshaping layer.
    """

    request_id: str
    decision_id: str
    patient_ref: str
    diagnosis_code: str
    procedure_code: str
    clinical_notes: str
    ai_status: str
    ai_rationale: str
    confidence_score: float
    escalated_at: datetime


class ReviewQueueResponse(BaseModel):
    """
    The review queue, plus the honest statement of what it cannot do yet.

    `resolution_supported` is False because no endpoint records a Medical
    Director's disposition of a case: `/feedback` writes a precedent to the
    institutional-memory collection (a real effect on future reasoning) but does
    not write a HumanReviewModel row or change the decision outcome. A reviewed
    case therefore reappears in this queue on the next fetch. The flag is part of
    the response, not a UI-side constant, so the UI cannot claim resolution works
    while the backend says otherwise.
    """

    items: list[ReviewQueueItem]
    resolution_supported: bool = False


@router.get("/review-queue", response_model=ReviewQueueResponse)
async def get_review_queue(
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
    _user: str = Depends(verify_token),
    # Per-endpoint medical_director floor, IN ADDITION to the router-wide
    # clinician guard in main.py (§2 of the RBAC design) — a clinician token
    # passes the router-wide check but must still 403 here.
    _role: User = Depends(_require_medical_director),
) -> ReviewQueueResponse:
    """
    List the cases the agent escalated to a human Medical Director.

    Reads real persisted decisions (outcome IN_REVIEW) joined to their requests.
    The queue UI previously rendered a hardcoded single-element array, so it
    showed the same synthetic case whether the system had escalated nothing or a
    hundred cases.

    An empty list is a real answer — it means nothing has been escalated — and is
    not padded with sample data.
    """
    rows = await AuthorizationRepository(session).list_escalated_for_review(limit=limit)

    items = [
        ReviewQueueItem(
            request_id=request.request_id,
            decision_id=decision.decision_id,
            # Opaque reference only. Age is included when the submission carried
            # it; there is no name or date of birth in this payload.
            patient_ref=(
                f"Patient {request.patient_id} · {request.patient_age}yo"
                if request.patient_age is not None
                else f"Patient {request.patient_id}"
            ),
            diagnosis_code=request.primary_diagnosis_code,
            procedure_code=request.treatment_code,
            clinical_notes=request.clinical_notes or "",
            ai_status=decision.outcome,
            ai_rationale=(decision.rationale_data or {}).get("text", ""),
            confidence_score=decision.confidence_score,
            escalated_at=decision.decided_at,
        )
        for decision, request in rows
    ]

    return ReviewQueueResponse(items=items)


@router.post("/", response_model=AuthorizationDecision)
async def submit_authorization(
    request: AuthorizationRequest,
    # FastAPI injects a fresh async database session here for this request.
    # The 'Depends(get_session)' tells FastAPI: before running this function,
    # call get_session() and pass the result in as 'session'.
    session: AsyncSession = Depends(get_session),
):
    """
    Submit a new prior authorization request for AI evaluation.

    This is the primary entry point for the PACCA workflow:
      1. Retrieve relevant clinical guidelines from ChromaDB (RAG)
      2. Pass case + guidelines to the Orchestrator
      3. Orchestrator runs Decision Agent (and Medical Director Agent if needed)
      4. Return the authorization decision with confidence score + rationale
      5. Write a full audit record of the submission and decision

    Args:
        request: The authorization request (patient, diagnosis, treatment, notes)
        session: Async database session (injected by FastAPI)

    Returns:
        AuthorizationDecision with status, confidence score, and rationale

    Raises:
        HTTPException(500): If the AI pipeline fails for any reason
    """
    # AuditRepository takes a session and knows how to write to audit_logs.
    # We create it fresh per-request (it's cheap — just stores the session ref).
    audit = AuditRepository(session)

    # correlation_id ties together ALL audit records for a single request.
    # Teaching note: imagine a request that touches 4 agents. Without a
    # correlation_id, the 4 audit records look like 4 unrelated events.
    # With a correlation_id, you can filter the audit log to "show me
    # everything that happened for request AUTH-12345" in one query.
    correlation_id = str(uuid4())

    # Record the wall-clock time so we can measure total processing time.
    # time.time() returns seconds as a float; multiplying by 1000 gives ms.
    start_time = time.time()

    # ── AUDIT RECORD 0: Declare the run's intent (P-3 / chg-7) ───────────────
    # A typed, record-only contract of what this run may touch and what effect it
    # should have, appended as the FIRST audit event so every trail begins with
    # `intent.declared`. P-4 (scope guard) and P-5 (evidence grounding) read it
    # and cite it. Record-only — no enforcement here. It lives in the route, not
    # the orchestrator, because the route owns event #0 and holds the
    # correlation_id / request_id / patient_id at pre-flight.
    intent = IntentRecord.for_prior_auth(
        correlation_id=correlation_id,
        request_id=request.request_id,
        subject_ref=request.patient_id,
    )
    await audit.log(
        action="intent.declared",
        actor="orchestrator",
        actor_type="system",
        request_id=request.request_id,
        correlation_id=correlation_id,
        details=intent.model_dump(mode="json"),
    )

    # ── AUDIT RECORD 1: Log the incoming submission ──────────────────────────
    # This creates a record BEFORE we do any AI processing, so if the system
    # crashes mid-flight, we still have evidence that the request was received.
    await audit.log(
        action="authorization_submitted",
        actor=request.provider_npi,  # Who submitted it (provider NPI)
        actor_type="provider",  # Category of actor
        request_id=request.request_id,  # Links to authorization_requests table
        correlation_id=correlation_id,
        input_summary=(
            f"Diagnosis: {request.clinical_case.primary_diagnosis_code} | "
            f"Procedure: {request.clinical_case.procedure_code} | "
            f"Patient: {request.patient_id}"
        ),
        details={
            "patient_id": request.patient_id,
            "diagnosis_code": request.clinical_case.primary_diagnosis_code,
            "procedure_code": request.clinical_case.procedure_code,
            "evidence_count": len(request.clinical_case.evidence),
        },
    )

    try:
        # ── PERSIST + SCOPE GUARD: write the incoming request (P-4 / chg-9) ────
        # This is a db.write_request. Guard it against the run's declared intent
        # first — the request_id / patient_ref MUST match the IntentRecord, so a
        # cross-case leak fail-closes to human review — then persist. Now that the
        # persistence layer is repaired, this is a REAL deny-capable call site
        # (unlike the always-allow single-collection RAG guard below).
        await enforce_scope(
            intent,
            "db.write_request",
            audit=audit,
            mode=get_settings().scope_guard_mode,
            request_id=request.request_id,
            patient_ref=request.patient_id,
        )
        await AuthorizationRepository(session).create(request)

        # ── RAG: Retrieve relevant guidelines from ChromaDB ───────────────────
        # Teaching note: RAG = Retrieval-Augmented Generation.
        # Instead of asking the LLM to rely on its training data for clinical
        # guidelines (which may be outdated or hallucinated), we:
        #   1. Build a query string from the case details
        #   2. Search our ChromaDB vector store for the most relevant guideline chunks
        #   3. Inject those chunks into the LLM prompt as context
        # This grounds the AI's reasoning in real, up-to-date guidelines.
        case = request.clinical_case
        query = f"Guidelines for {case.primary_diagnosis_code} and {case.procedure_code}"

        # ── SCOPE GUARD: RAG query (P-4 / chg-8) ─────────────────────────────
        # Minimum-necessary check against the run's declared IntentRecord. The
        # retriever reads BOTH real collections (guidelines + institutional-memory
        # precedents), so guard each — a query to any collection outside the run's
        # declared scope fail-closes to human review. This replaced a phantom
        # `clinical_guidelines` check that named a collection never queried and so
        # governed nothing (#2).
        for _collection in RAG_COLLECTIONS:
            await enforce_scope(
                intent,
                "rag.query",
                audit=audit,
                mode=get_settings().scope_guard_mode,
                collection_name=_collection,
            )
        retrieval = rag_engine.query(query)
        context_text = retrieval.text

        # ── RAG DEGRADATION VISIBILITY (chg-19/chg-20) ────────────────────────
        # query() now reports whether it served the governed RAGPipeline or fell
        # back to the legacy direct-ChromaDB path (RetrievalOutcome, chg-19).
        # Every degradation is audited BEFORE we decide whether to proceed, so
        # the trail always shows exactly what context the DecisionAgent reasoned
        # over — this closes the "silent fallback into a clinical decision"
        # pattern iter-14's root-cause note called out.
        #
        # `rag_degraded_escalates` mirrors the P-4 scope guard's own iter-8 ->
        # iter-9 rollout: land in warn mode (audit + proceed), observe how often
        # degradation actually fires in production, and only then promote to
        # fail-closed enforcement in a later iteration. Nobody has data yet on
        # how often this fires, and jumping straight to enforce risks flooding
        # the human review queue — its own safety problem.
        if retrieval.degraded:
            # Covers BOTH degradation axes: mode != "pipeline" (guideline
            # retrieval fell back) and precedents_degraded (institutional
            # memory failed during an otherwise-healthy pipeline call) —
            # RetrievalOutcome.degraded is True for either, and the audit
            # record always carries both so a reader can tell which one fired.
            await audit.log(
                action="rag.degraded",
                actor="rag",
                actor_type="system",
                request_id=request.request_id,
                correlation_id=correlation_id,
                success=False,
                output_summary=f"RAG retrieval degraded (mode={retrieval.mode})",
                details={
                    "mode": retrieval.mode,
                    "reason": retrieval.reason,
                    "precedents_degraded": retrieval.precedents_degraded,
                    "precedents_reason": retrieval.precedents_reason,
                },
            )

            # effective_settings() (not get_settings()) so this flag is
            # genuinely tunable via PATCH /config at runtime, not just an env
            # var read once at process start.
            if effective_settings().rag_degraded_escalates:
                duration_ms = int((time.time() - start_time) * 1000)
                await audit.log(
                    action="escalation_human_review_required",
                    actor="rag",
                    actor_type="system",
                    request_id=request.request_id,
                    correlation_id=correlation_id,
                    duration_ms=duration_ms,
                    success=False,
                    output_summary=(
                        f"RAG retrieval degraded (mode={retrieval.mode}); routed to human review"
                    ),
                    details={
                        "escalation_reason": EscalationReason.RAG_DEGRADED.value,
                        "mode": retrieval.mode,
                        "reason": retrieval.reason,
                        "precedents_degraded": retrieval.precedents_degraded,
                        "precedents_reason": retrieval.precedents_reason,
                    },
                )
                escalated_decision = AuthorizationDecision(
                    decision_id=f"RAGDEGRADED-{request.request_id}-{int(time.time())}",
                    status=AuthorizationStatus.IN_REVIEW,
                    confidence_score=0.0,
                    rationale=(
                        "RAG retrieval degraded to the direct-ChromaDB fallback; "
                        "case routed to human review rather than deciding on "
                        "unverified guideline context."
                    ),
                    review_tier_used=ReviewTier.HUMAN,
                )
                # Persist the escalated decision — without this, the case is
                # routed to human review but never lands in the
                # authorization_decisions table, so GET /review-queue (which
                # reads persisted IN_REVIEW rows) can never surface it: a
                # human review nobody can see. Same fix as any other IN_REVIEW
                # outcome further down this function.
                await DecisionRepository(session).create(
                    escalated_decision,
                    request_id=request.request_id,
                    processing_time_ms=duration_ms,
                )
                return escalated_decision

        # ── ORCHESTRATOR: Run the AI decision pipeline ────────────────────────
        # DecisionContext bundles the case + retrieved guidelines together.
        # The Orchestrator will run the Decision Agent, and if confidence is
        # ambiguous, escalate to the Medical Director Agent.
        decision_ctx = DecisionContext(
            case=case,
            relevant_guidelines=context_text,
        )
        # Pass audit + correlation_id into the orchestrator so that
        # per-agent records share the same correlation_id as the
        # submission record above. This is what makes the full trace
        # queryable by a single ID.
        decision = await orchestrator.process_decision(
            decision_ctx,
            audit=audit,
            correlation_id=correlation_id,
        )

        # Calculate how long the full AI pipeline took (in milliseconds)
        duration_ms = int((time.time() - start_time) * 1000)

        # ── AUDIT RECORD 2: Log the AI decision ──────────────────────────────
        # This creates a permanent, tamper-evident record of what the AI decided,
        # at what confidence level, and how long it took. This is the record
        # a compliance officer would pull during a HIPAA audit.
        await audit.log(
            action="authorization_decision_made",
            actor=decision.review_tier_used.value,  # Which agent made the decision
            actor_type="agent",
            request_id=request.request_id,
            correlation_id=correlation_id,
            duration_ms=duration_ms,
            output_summary=(
                f"Status: {decision.status.value} | "
                f"Confidence: {decision.confidence_score:.2f} | "
                f"Tier: {decision.review_tier_used.value}"
            ),
            details={
                "status": decision.status.value,
                "confidence_score": decision.confidence_score,
                "review_tier": decision.review_tier_used.value,
                "decision_id": decision.decision_id,
            },
        )

        # ── PERSIST + SCOPE GUARD: write the decision (P-4 / chg-9) ────────────
        # db.write_decision, guarded against the run's intent, then persisted.
        await enforce_scope(
            intent,
            "db.write_decision",
            audit=audit,
            mode=get_settings().scope_guard_mode,
            request_id=request.request_id,
        )
        await DecisionRepository(session).create(
            decision, request_id=request.request_id, processing_time_ms=duration_ms
        )

        return decision

    except ScopeViolation as sv:
        # ── FAIL-CLOSED: minimum-necessary scope violation → human review ─────
        # An out-of-scope operation never continues silently and never 500s: it
        # routes to human review (EscalationReason.SCOPE_VIOLATION). In warn mode
        # enforce_scope does not raise; enforce mode (chg-9, now the default)
        # activates this path. It becomes reachable via the identifier-checked DB
        # writes above if a cross-case leak ever occurs (correct operation always
        # passes the run's own identifiers, so it does not fire in normal flow).
        duration_ms = int((time.time() - start_time) * 1000)
        await audit.log(
            action="escalation_human_review_required",
            actor="scope_guard",
            actor_type="system",
            request_id=request.request_id,
            correlation_id=correlation_id,
            duration_ms=duration_ms,
            success=False,
            output_summary=f"Scope violation on '{sv.action}'; routed to human review",
            details={
                "escalation_reason": EscalationReason.SCOPE_VIOLATION.value,
                "guarded_action": sv.action,
                "violations": sv.violations,
            },
        )
        return AuthorizationDecision(
            decision_id=f"SCOPE-{request.request_id}-{int(time.time())}",
            status=AuthorizationStatus.IN_REVIEW,
            confidence_score=0.0,
            rationale=(
                "Minimum-necessary scope guard denied an out-of-scope operation; "
                "case routed to human review without a completed AI decision."
            ),
            review_tier_used=ReviewTier.HUMAN,
        )

    except Exception as e:
        # ── AUDIT RECORD 3: Log failures ─────────────────────────────────────
        # If anything goes wrong in the AI pipeline, we still log it.
        # This is critical for debugging production failures — you need to know
        # WHICH request failed, WHEN it failed, and WHY.
        duration_ms = int((time.time() - start_time) * 1000)
        await audit.log(
            action="authorization_processing_failed",
            actor="system",
            actor_type="system",
            request_id=request.request_id,
            correlation_id=correlation_id,
            duration_ms=duration_ms,
            success=False,
            error_message=str(e),
        )
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/feedback")
async def learn_from_feedback(
    feedback: FeedbackRequest,
    session: AsyncSession = Depends(get_session),
    # Per-endpoint medical_director floor, IN ADDITION to the router-wide
    # clinician guard in main.py (§2 of the RBAC design).
    _role: User = Depends(_require_medical_director),
):
    """
    Level 4 Learning Loop: teach the system from human override decisions.

    When a human reviewer disagrees with an AI decision and provides a
    corrected outcome + rationale, this endpoint stores that decision as
    a "case precedent" in the ChromaDB vector store.

    The next time a semantically similar case is submitted, the RAG pipeline
    will retrieve this precedent alongside the official guidelines, and the
    Decision Agent's prompt explicitly instructs it to weigh precedents heavily.

    This is how PACCA's institutional memory works without retraining the model.

    Args:
        feedback: The human reviewer's corrected decision and rationale
        session:  Async database session (injected by FastAPI)
    """
    audit = AuditRepository(session)
    correlation_id = str(uuid4())
    request_id = f"FB-{uuid4().hex[:12]}"

    # ── GOVERNANCE (#3): a precedent write is a state change and is governed like
    # the submit path — a narrow institutional-learning IntentRecord declared as
    # the FIRST audit event, a scope-guarded write (case_precedents only), and the
    # learning event audited BEFORE the ChromaDB write (pre-write-audit).
    intent = IntentRecord.for_feedback(
        correlation_id=correlation_id,
        request_id=request_id,
        subject_ref="institutional_learning",
    )
    await audit.log(
        action="intent.declared",
        actor="feedback",
        actor_type="system",
        request_id=request_id,
        correlation_id=correlation_id,
        details=intent.model_dump(mode="json"),
    )

    try:
        await enforce_scope(
            intent,
            "rag.write_precedent",
            audit=audit,
            mode=get_settings().scope_guard_mode,
            collection_name=PRECEDENT_COLLECTION,
        )
    except ScopeViolation as sv:
        # Fail-closed: a precedent write outside the declared scope is refused and
        # audited. Never fires in correct operation (case_precedents is always the
        # feedback scope); it is defense against a leak/bug.
        raise HTTPException(
            status_code=403, detail="precedent write outside declared scope"
        ) from sv

    # Pre-write audit: record the learning event before the state change.
    await audit.log(
        action="precedent_learned",
        actor="human_reviewer",
        actor_type="user",
        request_id=request_id,
        correlation_id=correlation_id,
        input_summary=f"Case: {feedback.case_summary[:100]}",
        output_summary=f"Outcome stored: {feedback.decision}",
        details={
            "outcome": feedback.decision,
            "rationale_length": len(feedback.rationale),
        },
    )

    rag_engine.add_precedent(
        case_summary=feedback.case_summary,
        rationale=feedback.rationale,
        outcome=feedback.decision,
    )

    return {"status": "learned"}
