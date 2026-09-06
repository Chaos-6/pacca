from datetime import datetime
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from .clinical import ClinicalCase
from .enums import AuthorizationStatus, ReviewTier

# Provenance sentinel for decisions produced by deterministic code rather than a
# model: pre-flight escalations, scope-guard refusals, timeouts, idempotent
# replays. See AuthorizationDecision.model_id (SCHEMA-INV-04).
DETERMINISTIC_PROVENANCE = "none:deterministic"

# iter-5 chg-3: explicit __all__ so mypy strict mode treats ReviewTier as
# a deliberate re-export. decision.py imports ReviewTier from this module
# rather than from .enums directly, which is the project convention.
__all__ = [
    "DETERMINISTIC_PROVENANCE",
    "AuditLogEntry",
    "AuthorizationDecision",
    "AuthorizationRequest",
    "AuthorizationStatus",
    "ClinicalCase",
    "DecisionDraft",
    "ReviewTier",
    "mint_decision_id",
]


def mint_decision_id() -> str:
    """
    Server-side identifier for an authorization decision.

    chg-11 (B6): ``decision_id`` used to arrive in the model's tool-use output,
    and was then written to a ``unique=True`` column. Identifiers are not a
    clinical judgement, and a model that repeats one silently cross-links two
    decisions' audit trails (``audit_logs.decision_id`` and the ``human_reviews``
    FK both reference this value). It is minted here instead.

    Standing rule: no LLM-supplied value may land in a unique, indexed, or
    foreign-keyed column.
    """
    return f"PA-{uuid4().hex[:16]}"


class AuditLogEntry(BaseModel):
    timestamp: datetime = Field(default_factory=datetime.now)
    entry_type: str
    message: str
    agent_id: str | None = None


class DecisionDraft(BaseModel):
    """
    What the LLM is asked for — clinical judgement only.

    chg-11 (B6): this is the ``response_model`` for both agent tiers, replacing
    ``AuthorizationDecision``. The difference is the point: no ``decision_id``,
    no ``review_tier_used``, no ``timestamp``, no ``audit_trail``. Those are
    facts about the *run*, which the server owns; the model contributes only the
    fields it is qualified to produce. ``model_json_schema()`` on this class is
    what becomes the forced tool schema, so anything absent here cannot be
    supplied by the model at all.
    """

    status: AuthorizationStatus
    confidence_score: float
    rationale: str
    cited_evidence_ids: list[str] = Field(default_factory=list)


class AuthorizationDecision(BaseModel):
    # Defaulted, never model-supplied — see mint_decision_id() and chg-11 (B6).
    # Explicit ids are still honoured for the deterministic escape hatches
    # (PREESC-… pre-flight escalations, SCOPE-… scope violations).
    decision_id: str = Field(default_factory=mint_decision_id)
    status: AuthorizationStatus
    confidence_score: float
    rationale: str
    review_tier_used: ReviewTier
    timestamp: datetime = Field(default_factory=datetime.now)
    # We add this field back since the AuditLogEntry exists now
    audit_trail: list[AuditLogEntry] = []
    # Evidence-grounding (P-5 / chg-10): the ids of the submission EvidenceItems
    # the decision relied on. The DecisionAgent populates it (prompt v2.7); the
    # orchestrator's grounding detector requires each id to resolve to a
    # submission EvidenceItem or forces human review. Defaulted (not required) so
    # hand-constructed decisions (pre-flight escalations, tests) still validate.
    cited_evidence_ids: list[str] = Field(default_factory=list)

    # ── Model / prompt provenance (SCHEMA-INV-04, THREAT-04, CHG-02) ──────────
    # Which substrate produced this decision. Recorded because the optimal
    # harness is model-specific and a vendor can change the behaviour behind a
    # pinned identifier: without this, "the model changed" is unanswerable
    # after the fact. docs/DECISIONS.md:136-140 records exactly that situation —
    # an evaluation run on a substituted model, reconstructable only from prose
    # a human typed afterwards.
    #
    # Deterministic decisions (pre-flight escalations, scope-guard refusals,
    # timeouts, idempotent replays) are produced by code, not a model, and carry
    # the DETERMINISTIC_PROVENANCE sentinel rather than a null. A sentinel is
    # used instead of nullable so that "no model was involved" is an assertion
    # the row makes, not an absence a reader has to interpret — and so a real
    # agent decision cannot slip through unrecorded, which _require_agent_provenance
    # below enforces.
    model_id: str = Field(default=DETERMINISTIC_PROVENANCE)
    prompt_version: str = Field(default=DETERMINISTIC_PROVENANCE)

    @model_validator(mode="after")
    def _require_agent_provenance(self) -> "AuthorizationDecision":
        """
        SCHEMA-INV-04: an agent-produced decision must name its substrate.

        The sentinel is correct for a decision code produced on its own. It is
        never correct for one a model produced, so an agent tier carrying the
        sentinel means the provenance wiring was dropped somewhere between the
        API call and here — the exact failure this requirement exists to catch.
        """
        agent_tiers = (ReviewTier.AUTOMATED, ReviewTier.MEDICAL_DIRECTOR_AGENT)
        if self.review_tier_used in agent_tiers:
            missing = [
                name
                for name, value in (
                    ("model_id", self.model_id),
                    ("prompt_version", self.prompt_version),
                )
                if not value or value == DETERMINISTIC_PROVENANCE
            ]
            if missing:
                raise ValueError(
                    f"SCHEMA-INV-04: decision {self.decision_id} was produced by "
                    f"tier {self.review_tier_used.value} but carries no "
                    f"{' and no '.join(missing)}. An agent decision must record the "
                    "model identifier and prompt version that produced it."
                )
        return self


class AuthorizationRequest(BaseModel):
    request_id: str
    patient_id: str
    provider_npi: str
    clinical_case: ClinicalCase
    # Audit log might be attached here in some legacy versions
    audit_log: list[AuditLogEntry] = []
