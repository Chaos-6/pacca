"""Per-run IntentRecord (harness change P-3 / chg-7).

A typed, audited contract declared at the very start of every prior-authorization
run: what the run is permitted to touch and what effect it should have. It is
**record-only** — no enforcement lives here. The orchestrator/route appends it as
the first audit event (`action="intent.declared"`); later governance changes
(P-4 minimum-necessary scope guard, P-5 evidence-grounding detector) read it and
cite it in their findings.

Concept imported from CausalGate's typed intent contract, scoped to what is
meaningful for PACCA: the per-request purpose is fixed (prior-auth adjudication),
so the informative fields are the *scope* (collections/actions) and the
*expected effects*.

The `allowed_*` / `expected_effects` / `limits` values have no runtime capability
source today, so they are declared constants here (this module is their SSOT).
When a real capability/RBAC source exists, `for_prior_auth` reads from it instead.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# ── Declared scope constants (SSOT until a capability source exists) ──────────
# The two real ChromaDB collections the retriever reads on a prior-auth run:
# nccn_guidelines (authoritative) and case_precedents (institutional memory /
# Medical Director overrides). Literals to keep the models layer from importing
# integrations; a drift test asserts they equal vector_store's RAG_COLLECTIONS.
# Previously this named a phantom "clinical_guidelines" that was never queried,
# so the real collections were ungoverned (#2).
PRIOR_AUTH_ALLOWED_COLLECTIONS: list[str] = ["nccn_guidelines", "case_precedents"]
# Abstract capabilities a prior-auth run legitimately exercises. The submit flow
# CREATES a request and persists its decision, so the DB actions are writes
# (P-4 wires + guards these): db.write_request (persist the incoming request)
# and db.write_decision (persist the adjudication outcome).
PRIOR_AUTH_ALLOWED_ACTIONS: list[str] = [
    "rag.query",
    "db.write_request",
    "db.write_decision",
    # chg-32: Branch 7's data source. This is the first READ in the prior-auth
    # scope, and the first action that touches rows outside the current request
    # -- so declaring it is the point. Branch 7 (prior_denial_same_service) has
    # never been able to fire in production because nothing populated
    # `prior_denial_codes`; wiring it means one run now reads that patient's
    # earlier authorization history.
    #
    # Minimum-necessary holds because the widening is bounded to the SAME
    # patient: `get_denied_procedure_codes` takes patient_id as its sole
    # selector and returns procedure codes only -- no diagnoses, no notes, no
    # other patient's rows. A leak across patients would not just expose data,
    # it would escalate a clean case on someone else's record, which is why the
    # guard sits on this call rather than trusting the query.
    "db.read_prior_denials",
    "audit.append",
]
PRIOR_AUTH_EXPECTED_EFFECTS: list[str] = [
    "one AuthorizationDecision row",
    "audit entries",
    "zero external calls",
]
# Agents in one run: evidence + classification + decision (+ medical director on
# escalation); 6 leaves headroom without being unbounded.
PRIOR_AUTH_LIMITS: dict[str, Any] = {"max_agent_calls": 6, "escalation_allowed": True}

# ── Institutional-learning (/feedback) scope ──────────────────────────────────
# A human-override precedent write touches ONLY the case_precedents collection
# and writes no SQL business rows — a deliberately narrower scope than a
# prior-auth run. This is what brings the previously-ungoverned precedent write
# under the same minimum-necessary guard (#3).
FEEDBACK_ALLOWED_COLLECTIONS: list[str] = ["case_precedents"]
FEEDBACK_ALLOWED_ACTIONS: list[str] = ["rag.write_precedent", "audit.append"]
FEEDBACK_EXPECTED_EFFECTS: list[str] = [
    "one case_precedents entry (upserted)",
    "audit entries",
    "zero external calls",
]
FEEDBACK_LIMITS: dict[str, Any] = {"max_agent_calls": 0, "escalation_allowed": False}


class IntentRecord(BaseModel):
    """The declared intent for a single prior-authorization run.

    Serialize with ``model_dump(mode="json")`` into the ``details`` JSON of the
    ``intent.declared`` audit event — no DB schema/migration change is required.
    """

    correlation_id: str
    request_id: str
    purpose: Literal["prior_auth_adjudication", "institutional_learning"] = (
        "prior_auth_adjudication"
    )
    subject_ref: str  # the case's patient/member opaque reference (no PHI)
    allowed_collections: list[str] = Field(
        default_factory=lambda: list(PRIOR_AUTH_ALLOWED_COLLECTIONS)
    )
    allowed_actions: list[str] = Field(default_factory=lambda: list(PRIOR_AUTH_ALLOWED_ACTIONS))
    expected_effects: list[str] = Field(default_factory=lambda: list(PRIOR_AUTH_EXPECTED_EFFECTS))
    limits: dict[str, Any] = Field(default_factory=lambda: dict(PRIOR_AUTH_LIMITS))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def for_prior_auth(
        cls, *, correlation_id: str, request_id: str, subject_ref: str
    ) -> IntentRecord:
        """Build the standard prior-auth intent from the declared scope constants."""
        return cls(
            correlation_id=correlation_id,
            request_id=request_id,
            subject_ref=subject_ref,
        )

    @classmethod
    def for_feedback(
        cls, *, correlation_id: str, request_id: str, subject_ref: str
    ) -> IntentRecord:
        """Build an institutional-learning intent for the /feedback precedent write.

        Narrower than a prior-auth run: it may only write to case_precedents and
        append audit — no guideline read, no DB business writes (#3).
        """
        return cls(
            correlation_id=correlation_id,
            request_id=request_id,
            subject_ref=subject_ref,
            purpose="institutional_learning",
            allowed_collections=list(FEEDBACK_ALLOWED_COLLECTIONS),
            allowed_actions=list(FEEDBACK_ALLOWED_ACTIONS),
            expected_effects=list(FEEDBACK_EXPECTED_EFFECTS),
            limits=dict(FEEDBACK_LIMITS),
        )
