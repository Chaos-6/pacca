"""
Orchestrator — coordinates the multi-agent decision pipeline.

The Orchestrator is the "traffic controller" of PACCA. It decides which
agents run, in what order, and what to do with their outputs. It enforces
the full 7-branch escalation tree specified in PRD SS5.4.

Architecture teaching note — why a separate Orchestrator?
  Each agent (Decision, Medical Director) is a specialist — it knows how to
  do one thing well. The Orchestrator contains ZERO clinical reasoning. Its
  only job is workflow: call this agent, check the result, decide what to
  do next. This separation means:
    - You can change escalation rules without touching clinical prompts
    - You can add new agents (e.g. a Pharmacist Agent) by plugging them
      in here, without rewriting the decision logic
    - You can test escalation logic independently of LLM calls

The 7-branch escalation tree (PRD SS5.4 — now fully implemented, and grown
beyond the original PRD scope on the pre-flight side):

  PRE-FLIGHT CHECKS (deterministic, run before any agent call — 7 checks,
  all evaluated in ClinicalRiskDetector.evaluate()). Any single check
  firing routes the case directly to human review via
  _handle_pre_flight_escalation; the Decision Agent is never called:
    Branch 4: Experimental treatment    → always route to human review
    Branch 5: Rare condition            → always route to human review
    Branch 6: Conflicting guidelines    → always route to human review
    Branch 7: Prior denial same service → always route to human review
    High cost                           → always route to human review
    Pediatric complexity                → always route to human review
    Adult complexity                    → always route to human review

  POST-AGENT CHECKS (run after Decision Agent returns) — thresholds are
  settings-driven via effective_settings(); the values below are the current
  DEFAULTS (auto_approve_confidence_threshold / escalation_confidence_threshold)
  and may be overridden per-deployment or at runtime (PATCH /config):
    Branch 1: confidence >= auto-approve threshold (default 0.95)         → auto-approve
    Branch 2: escalation <= confidence < auto-approve (default 0.90–0.95) → Medical Director Agent
    Branch 3: confidence < escalation threshold (default 0.90)            → human review queue

  The pre-flight checks are the key architectural addition in Week 2, later
  extended (iter-3 chg-1, iter-5 chg-3, iter-6 chg-2) with cost- and
  complexity-based policy checks. They ensure certain cases NEVER reach
  autonomous decision regardless of how confident the AI appears. This is
  not a limitation — it is a deliberate clinical safety property of the
  system.

  Naming note: the three checks added after Week 2 (high cost, pediatric
  complexity, adult complexity) are labeled "Branch 2" in
  ClinicalRiskDetector's own code comments, borrowing the confidence-routing
  branch's name. That label does NOT mean they route to the Medical
  Director Agent — like branches 4-7, any hit short-circuits straight to
  human review, before the Decision Agent ever runs. Treat all seven
  pre-flight checks as one undifferentiated gate; the "Branch 2" name in
  clinical_risk_detector.py is a leftover label, not a routing distinction.

Teaching note — pre-flight before post-flight:
  You might wonder: why not just run the Decision Agent on every case and
  let its confidence score drive everything? Two reasons:
  1. Efficiency: LLM calls cost money and take time. Pre-flight checks are
     pure Python — microseconds, zero cost. Why spend $0.05 on a Claude API
     call for a case we already know must go to human review?
  2. Safety: A highly confident AI is not necessarily a correct AI,
     especially on experimental treatments and rare conditions where the
     model's training data is thin. Pre-flight checks enforce policy
     regardless of AI confidence.
"""

import time

from ..config.settings import effective_settings
from ..db.repository import AuditRepository
from ..models.authorization import AuthorizationDecision, AuthorizationStatus
from ..models.enums import EscalationReason, ReviewTier
from ..models.triage import ClassificationOutput, EvidenceOutput
from .classification_agent import ClinicalClassificationAgent
from .clinical_risk_detector import ClinicalRiskDetector, EscalationFlags
from .decision import DecisionAgent, DecisionContext, MedicalDirectorAgent
from .evidence_agent import EvidenceAggregationAgent
from .evidence_grounding import unresolved_cited_evidence


def select_confidence_branch(
    confidence: float,
    status: "AuthorizationStatus",
    auto_approve_threshold: float,
    escalation_threshold: float,
) -> str:
    """Select the Tier-1 confidence routing branch (PRD §5.4 Branches 1-3).

    Returns one of: "auto_approve", "medical_director", "human_review".
    Pure function of the agent's (confidence, status) and the effective thresholds.
    """
    if confidence >= auto_approve_threshold and status == AuthorizationStatus.AUTO_APPROVED:
        return "auto_approve"
    if escalation_threshold <= confidence < auto_approve_threshold:
        return "medical_director"
    return "human_review"


class Orchestrator:
    """
    Coordinates the full 7-branch prior authorization decision pipeline.

    Responsibilities:
      - Run pre-flight clinical risk checks (7 deterministic checks: PRD
        branches 4-7 plus the cost/complexity checks added in iter-3/5/6)
      - Run the Frontline Decision Agent on cases that pass pre-flight
      - Apply confidence thresholds (Branches 1-3)
      - Escalate to Medical Director Agent when confidence is ambiguous
      - Route to human review queue when required
      - Write per-agent audit records for observability and HIPAA compliance

    The Orchestrator is intentionally thin on clinical reasoning — it only
    applies policy rules. All clinical judgment lives in the agents.
    """

    def __init__(self) -> None:
        self.decision_agent = DecisionAgent()
        self.medical_director_agent = MedicalDirectorAgent()
        # ClinicalRiskDetector is a stateless utility — safe to instantiate once
        self.risk_detector = ClinicalRiskDetector()
        self.evidence_agent = EvidenceAggregationAgent()
        self.classification_agent = ClinicalClassificationAgent()

    async def process_decision(
        self,
        context: DecisionContext,
        audit: AuditRepository | None = None,
        correlation_id: str | None = None,
        prior_denial_codes: list[str] | None = None,
    ) -> AuthorizationDecision:
        """
        Run the full 7-branch decision pipeline for a prior authorization request.

        Args:
            context:             The clinical case + retrieved guidelines
            audit:               AuditRepository for writing event records.
                                 Optional so the Orchestrator can be called
                                 from tests or scripts without a database.
            correlation_id:      UUID shared across all audit records for this
                                 request. Pass the same ID used in the route.
            prior_denial_codes:  Procedure codes previously denied for this
                                 patient. Used for Branch 7 (prior denial check).
                                 Defaults to empty list if not provided.

        Returns:
            AuthorizationDecision with final status, confidence, and rationale
        """
        prior_denial_codes = prior_denial_codes or []

        # ═════════════════════════════════════════════════════════════════════
        # PRE-FLIGHT: 7 deterministic checks — run BEFORE any LLM call
        # ═════════════════════════════════════════════════════════════════════
        #
        # Teaching note: these checks are deterministic policy rules. They
        # do not involve any AI reasoning. A case that triggers a pre-flight
        # flag goes directly to the human review queue — the LLM is never
        # consulted. This is intentional: for these case types, AI confidence
        # is not a reliable signal.

        flags = self.risk_detector.evaluate(
            case=context.case,
            guidelines_context=context.relevant_guidelines,
            prior_denial_codes=prior_denial_codes,
        )

        if flags.should_pre_escalate:
            return await self._handle_pre_flight_escalation(
                context=context,
                flags=flags,
                audit=audit,
                correlation_id=correlation_id,
            )

        # ── Triage (advisory): runs for the audit trail + routing/queue metadata.
        #    DELIBERATELY NOT fed into the Tier-1 decision. Clinical severity
        #    (complexity / urgency) is not the same as decision-uncertainty;
        #    injecting routing-severity language biased the DecisionAgent toward
        #    over-escalation and regressed GC-020 (oncological emergency:
        #    auto-approve -> wrongly escalated to human review). The decision
        #    stays independent of triage. See docs/ENGINEERING_DECISIONS.md
        #    (ADR-020). Triage output is captured in _run_triage's audit records.
        await self._run_triage(context, audit, correlation_id)

        # ═════════════════════════════════════════════════════════════════════
        # TIER 1: Decision Agent (Branch evaluation: 1, 2, or 3)
        # ═════════════════════════════════════════════════════════════════════
        #
        # Teaching note: every agent call is wrapped in a start/complete
        # audit pair. This pattern gives you:
        #   - Proof that the agent was called (start record)
        #   - Proof of what it returned and how long it took (complete record)
        # An orphaned "started" record with no "completed" pair tells you
        # exactly where a failure occurred — invaluable for debugging.

        tier1_start = time.time()

        if audit:
            await audit.log(
                action="agent_decision_started",
                actor="DecisionSupportAgent",
                actor_type="agent",
                correlation_id=correlation_id,
                input_summary=(
                    f"Diagnosis: {context.case.primary_diagnosis_code} | "
                    f"Procedure: {context.case.procedure_code}"
                ),
            )

        decision = await self.decision_agent.run(context)
        tier1_ms = int((time.time() - tier1_start) * 1000)

        if audit:
            await audit.log(
                action="agent_decision_completed",
                actor="DecisionSupportAgent",
                actor_type="agent",
                correlation_id=correlation_id,
                duration_ms=tier1_ms,
                output_summary=(
                    f"Status: {decision.status.value} | Confidence: {decision.confidence_score:.2f}"
                ),
                details={
                    "confidence_score": decision.confidence_score,
                    "status": decision.status.value,
                    "review_tier": decision.review_tier_used.value,
                },
            )

        # ── P-5 evidence-grounding detector (chg-10) ───────────────────────────
        # Deterministic safety short-circuit BEFORE confidence routing: if the
        # decision cited an evidence id absent from the submission, it may rest on
        # fabricated or misattributed evidence — force human review regardless of
        # confidence. This is the production-path equivalent of the GC-018/019
        # anti-hallucination eval gate. Grounds against submission evidence ids
        # only (RAG chunks carry no agent-visible ids — see evidence_grounding.py).
        unresolved = unresolved_cited_evidence(decision, context.case)
        if unresolved:
            decision.status = AuthorizationStatus.IN_REVIEW
            decision.review_tier_used = ReviewTier.HUMAN
            if audit:
                await audit.log(
                    action="finding.ungrounded_evidence",
                    actor="orchestrator",
                    actor_type="system",
                    correlation_id=correlation_id,
                    output_summary=(
                        f"{len(unresolved)} cited evidence id(s) did not resolve to the "
                        "submission — routed to human review"
                    ),
                    details={
                        "escalation_reason": EscalationReason.UNGROUNDED_EVIDENCE.value,
                        "unresolved_ids": unresolved,
                        "branch": "evidence_grounding",
                    },
                )
            return decision

        # ── Tier-1 confidence routing (PRD §5.4 Branches 1-3) ──────────────────
        s = effective_settings()

        # Kill switch: when autonomous decisions are disabled, every case that
        # reached the Tier-1 routing stage goes to human review, regardless of
        # confidence (operator control for incidents / audits / regulatory holds).
        if not s.enable_autonomous_decisions:
            decision.status = AuthorizationStatus.IN_REVIEW
            if audit:
                await audit.log(
                    action="autonomous_decisions_disabled",
                    actor="orchestrator",
                    actor_type="system",
                    correlation_id=correlation_id,
                    details={
                        "confidence_score": decision.confidence_score,
                        "branch": "autonomous_disabled",
                    },
                )
            return decision

        branch = select_confidence_branch(
            decision.confidence_score,
            decision.status,
            s.auto_approve_confidence_threshold,
            s.escalation_confidence_threshold,
        )

        if branch == "auto_approve":
            if audit:
                await audit.log(
                    action="escalation_auto_approved",
                    actor="orchestrator",
                    actor_type="system",
                    correlation_id=correlation_id,
                    details={
                        "confidence_score": decision.confidence_score,
                        "branch": "1_auto_approve",
                    },
                )
            return decision

        if branch == "medical_director":
            return await self._run_medical_director(
                context=context,
                tier1_decision=decision,
                audit=audit,
                correlation_id=correlation_id,
            )

        # branch == "human_review"
        decision.status = AuthorizationStatus.IN_REVIEW
        if audit:
            await audit.log(
                action="escalation_human_review_required",
                actor="orchestrator",
                actor_type="system",
                correlation_id=correlation_id,
                details={
                    "escalation_reason": EscalationReason.CONFIDENCE_BELOW_THRESHOLD.value,
                    "confidence_score": decision.confidence_score,
                    "threshold": s.escalation_confidence_threshold,
                    "branch": "3_low_confidence",
                },
            )
        return decision

    # ── Helper: Triage enrichment (advisory) ─────────────────────────────────

    async def _run_triage(
        self,
        context: DecisionContext,
        audit: AuditRepository | None,
        correlation_id: str | None,
    ) -> tuple[EvidenceOutput | None, ClassificationOutput | None]:
        """Run the triage agents (Evidence -> Classification) for the audit trail
        and routing/queue metadata. Best-effort: on any failure, logs it and
        returns (None, None). Triage output does NOT feed the Tier-1 decision
        (decoupled — see process_decision and ADR-020)."""
        try:
            if audit:
                await audit.log(
                    action="agent_evidence_started",
                    actor="EvidenceAggregationAgent",
                    actor_type="agent",
                    correlation_id=correlation_id,
                )
            evidence = await self.evidence_agent.run(context)
            if audit:
                await audit.log(
                    action="agent_evidence_completed",
                    actor="EvidenceAggregationAgent",
                    actor_type="agent",
                    correlation_id=correlation_id,
                    output_summary=f"findings={len(evidence.key_findings)} conf={evidence.confidence_score:.2f}",
                )
                await audit.log(
                    action="agent_classification_started",
                    actor="ClinicalClassificationAgent",
                    actor_type="agent",
                    correlation_id=correlation_id,
                )
            classification = await self.classification_agent.run(context, evidence)
            if audit:
                await audit.log(
                    action="agent_classification_completed",
                    actor="ClinicalClassificationAgent",
                    actor_type="agent",
                    correlation_id=correlation_id,
                    output_summary=f"complexity={classification.complexity} specialty={classification.primary_specialty} urgency={classification.urgency.value}",
                )
            return evidence, classification
        except (
            Exception
        ) as exc:  # broad catch is intentional: advisory layer must never break the decision
            if audit:
                await audit.log(
                    action="triage_enrichment_failed",
                    actor="orchestrator",
                    actor_type="system",
                    correlation_id=correlation_id,
                    details={"error_type": type(exc).__name__},
                )
            return None, None

    # ── Helper: Pre-flight escalation handler ────────────────────────────────

    async def _handle_pre_flight_escalation(
        self,
        context: DecisionContext,
        flags: EscalationFlags,
        audit: AuditRepository | None,
        correlation_id: str | None,
    ) -> AuthorizationDecision:
        """
        Handle a case that triggered one or more pre-flight risk checks.

        Creates a decision record that routes directly to human review,
        with all triggered escalation reasons recorded in the audit log.
        This decision is NOT produced by an LLM — it is a policy decision
        by the Orchestrator.

        The decision's rationale explains exactly why it was pre-escalated,
        so the human reviewer immediately knows what to focus on.
        """
        # Build a clear rationale from all triggered flags
        reasons_text = "; ".join(
            f"{reason.value}: {detail}"
            for reason, detail in zip(flags.reasons, flags.details.values(), strict=False)
        )

        # Construct the decision as a policy outcome, not an AI outcome
        decision = AuthorizationDecision(
            decision_id=f"PREESC-{context.case.procedure_code}-{int(time.time())}",
            status=AuthorizationStatus.IN_REVIEW,
            confidence_score=0.0,  # Not applicable — this is a policy decision
            rationale=(
                f"Pre-flight escalation triggered by clinical risk checks. "
                f"Case routed directly to human review without AI evaluation. "
                f"Triggered checks: {reasons_text}"
            ),
            review_tier_used=ReviewTier.HUMAN,
        )

        if audit:
            await audit.log(
                action="escalation_pre_flight_triggered",
                actor="orchestrator",
                actor_type="system",
                correlation_id=correlation_id,
                output_summary=(
                    f"Pre-flight escalation: {len(flags.reasons)} trigger(s) fired. "
                    f"Reasons: {[r.value for r in flags.reasons]}"
                ),
                details={
                    "escalation_reasons": [r.value for r in flags.reasons],
                    "escalation_details": flags.details,
                    "branch": "4_7_pre_flight",
                    "llm_consulted": False,
                },
            )

        return decision

    # ── Helper: Medical Director Agent (Branch 2) ─────────────────────────────

    async def _run_medical_director(
        self,
        context: DecisionContext,
        tier1_decision: AuthorizationDecision,
        audit: AuditRepository | None,
        correlation_id: str | None,
    ) -> AuthorizationDecision:
        """
        Run the Tier 2 Medical Director Agent for ambiguous cases.

        The Medical Director Agent receives both the original case AND the
        Tier 1 decision so it can evaluate exactly why the Frontline Agent
        was uncertain. This is different from just running a second
        independent evaluation — it is a supervised second opinion.
        """
        tier2_start = time.time()

        if audit:
            await audit.log(
                action="agent_medical_director_started",
                actor="MedicalDirectorAgent",
                actor_type="agent",
                correlation_id=correlation_id,
                input_summary=(
                    f"Escalating from Tier 1. "
                    f"Tier 1 confidence: {tier1_decision.confidence_score:.2f}"
                ),
                details={
                    "branch": "2_medical_director",
                    "tier1_confidence": tier1_decision.confidence_score,
                    "tier1_status": tier1_decision.status.value,
                },
            )

        md_decision = await self.medical_director_agent.run(context, tier1_decision)
        tier2_ms = int((time.time() - tier2_start) * 1000)

        if audit:
            await audit.log(
                action="agent_medical_director_completed",
                actor="MedicalDirectorAgent",
                actor_type="agent",
                correlation_id=correlation_id,
                duration_ms=tier2_ms,
                output_summary=(
                    f"Status: {md_decision.status.value} | "
                    f"Confidence: {md_decision.confidence_score:.2f}"
                ),
                details={
                    "confidence_score": md_decision.confidence_score,
                    "status": md_decision.status.value,
                },
            )

        # The agent's determination decides the outcome; confidence only gates
        # whether an approval may be granted without a human. The two are
        # different quantities and they come apart on a denial: this branch
        # previously read confidence alone, so a Medical Director that denied a
        # case *and was sure of it* had the denial rewritten into an autonomous
        # approval, while the same denial held with less conviction went to a
        # human. Doubt was the only thing routing these cases safely, and the
        # inversion was worst where the stakes are highest -- every case here is
        # one Tier 1 already found ambiguous.
        #
        # select_confidence_branch is Tier 1's rule, reused rather than restated:
        # auto-approve requires high confidence AND an AUTO_APPROVED status.
        # Anything else goes to a human. Preserving DENIED here instead would
        # make Tier 2 more autonomous than Tier 1, which is backwards. The
        # denial is not discarded -- its rationale rides on the decision and the
        # agent_medical_director_completed record above captures the status the
        # agent returned -- but the adverse determination is issued by a person.
        settings = effective_settings()
        if (
            select_confidence_branch(
                md_decision.confidence_score,
                md_decision.status,
                settings.auto_approve_confidence_threshold,
                settings.escalation_confidence_threshold,
            )
            == "auto_approve"
        ):
            md_decision.status = AuthorizationStatus.AUTO_APPROVED
        else:
            md_decision.status = AuthorizationStatus.IN_REVIEW

        return md_decision
