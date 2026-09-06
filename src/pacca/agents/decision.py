"""
Decision Support Agent (Tier 1) and Medical Director Agent (Tier 2).

These are the two core clinical reasoning agents in PACCA:

  DecisionSupportAgent (Tier 1 — Frontline UM Nurse):
    Handles every case that passes pre-flight checks. Returns a
    confidence score and rationale. High confidence → auto-approve.
    Ambiguous → escalate to Medical Director Agent.

  MedicalDirectorAgent (Tier 2 — Chief Medical Director):
    Handles cases the Tier 1 agent found ambiguous (confidence 0.90–0.95).
    Has clinical authority to resolve nuance and interpret gray areas.
    Produces a definitive determination with high confidence, or routes
    to human review if uncertainty genuinely persists.

Both agents use the full structured prompt template from prompts/templates.py,
including the shared CLINICAL_SAFETY_GUIDELINES anti-hallucination instructions.
Prompt versions are tracked in PROMPT_REGISTRY for audit trail purposes.
"""

from pydantic import BaseModel

from ..models.authorization import AuthorizationDecision, DecisionDraft, ReviewTier
from ..models.clinical import ClinicalCase
from ._prompt_loader import load_agent_prompt
from .base import BaseAgent
from .prompts.templates import (
    get_prompt_version,
)


def _decision_from(
    draft: DecisionDraft,
    tier: ReviewTier,
    *,
    model_id: str,
    prompt_version: str,
) -> AuthorizationDecision:
    """
    Join the model's clinical judgement to the server-owned facts about the run.

    chg-11 (B6): the agents ask Claude for a DecisionDraft — status, confidence,
    rationale, cited evidence — and nothing else. ``decision_id`` is minted here
    (via AuthorizationDecision's default_factory) and ``review_tier_used`` is set
    by whichever tier produced the draft. Neither is negotiable by the model.

    ``model_id`` and ``prompt_version`` are server-owned in the same sense
    (SCHEMA-INV-04 / THREAT-04 / CHG-02): they are read from the agent's own
    config and prompt registry at the call site, never from the draft, so the
    model cannot misreport the substrate that produced it.
    """
    return AuthorizationDecision(
        status=draft.status,
        confidence_score=draft.confidence_score,
        rationale=draft.rationale,
        cited_evidence_ids=list(draft.cited_evidence_ids),
        review_tier_used=tier,
        model_id=model_id,
        prompt_version=prompt_version,
    )


class DecisionContext(BaseModel):
    """
    Input context passed to both Tier 1 and Tier 2 agents.

    Attributes:
        case:                The clinical case being evaluated
        relevant_guidelines: Raw text from the RAG pipeline — guidelines
                             and institutional precedents most relevant to
                             this specific case

    Note: triage output (evidence synthesis + classification) is deliberately
    NOT carried here. Triage feeds the audit trail and routing/queue, not the
    Tier-1 decision — keeping the decision independent of the routing-severity
    signals that biased it toward over-escalation (GC-020). See ADR-020.
    """

    case: ClinicalCase
    relevant_guidelines: str


class DecisionAgent(BaseAgent):
    """
    Tier 1 Frontline UM Nurse Decision Support Agent.

    Evaluates prior authorization cases against clinical guidelines
    using the full DECISION_AGENT_SYSTEM prompt (v2.2). Prompt includes:
      - AGENT_IDENTITY (shared baseline)
      - CLINICAL_SAFETY_GUIDELINES (anti-hallucination, escalation rules)
      - 5-step evaluation framework (guideline alignment, medical necessity,
        step therapy, documentation, precedent)
      - Confidence scoring rubric with explicit thresholds
      - OUTPUT_FORMAT_INSTRUCTIONS (structured output enforcement)

    Prompt version: see PROMPT_REGISTRY['DecisionSupportAgent']
    """

    @property
    def name(self) -> str:
        return "DecisionSupportAgent"

    @property
    def system_prompt(self) -> str:
        return load_agent_prompt("decision_support", "DecisionSupportAgent")

    @property
    def prompt_version(self) -> str:
        return get_prompt_version(self.name)

    async def run(self, context: DecisionContext) -> AuthorizationDecision:
        """
        Evaluate a clinical case and return an authorization decision.

        Builds a structured user-turn prompt from the case and guidelines,
        calls the Claude API via BaseAgent.execute() (with retry + OTel span),
        and returns a typed AuthorizationDecision.

        Args:
            context: Clinical case + retrieved guidelines

        Returns:
            AuthorizationDecision with status, confidence, and rationale
        """
        # Build a clear, structured user-turn prompt
        # The user turn provides the case-specific data; the system prompt
        # provides the role definition, safety guidelines, and rubric.
        user_input = (
            f"## Clinical Case\n"
            f"{context.case.model_dump_json(indent=2)}\n\n"
            f"## Relevant Clinical Guidelines\n"
            f"{context.relevant_guidelines}"
        )

        draft = await self.execute(
            user_input=user_input,
            response_model=DecisionDraft,
        )
        return _decision_from(
            draft,
            ReviewTier.AUTOMATED,
            model_id=self.config.model,
            prompt_version=self.prompt_version,
        )


class MedicalDirectorAgent(BaseAgent):
    """
    Tier 2 Chief Medical Director Agent.

    Handles cases escalated by the Tier 1 agent when confidence is in
    the 0.90–0.95 ambiguous zone. Uses the full MEDICAL_DIRECTOR_AGENT_SYSTEM
    prompt (v2.2). Prompt includes:
      - AGENT_IDENTITY (shared baseline)
      - CLINICAL_SAFETY_GUIDELINES (same anti-hallucination rules)
      - Explicit framing as Tier 2 authority with specific scope
      - 4-step evaluation framework (understand Tier 1 uncertainty,
        apply clinical authority, evaluate medical necessity, make
        definitive determination)
      - Confidence scoring rules specific to MD tier
      - Required output structure (must address Tier 1 hesitation explicitly)

    This is a significant upgrade from the original 2-line prompt.
    The MD Agent is the highest-stakes agent in the system — the one
    making decisions about genuinely ambiguous cases — and requires
    the most specific, structured guidance.

    Prompt version: see PROMPT_REGISTRY['MedicalDirectorAgent']
    """

    @property
    def name(self) -> str:
        return "MedicalDirectorAgent"

    @property
    def system_prompt(self) -> str:
        return load_agent_prompt("medical_director", "MedicalDirectorAgent")

    @property
    def prompt_version(self) -> str:
        return get_prompt_version(self.name)

    async def run(
        self,
        context: DecisionContext,
        previous_decision: AuthorizationDecision,
    ) -> AuthorizationDecision:
        """
        Apply Medical Director authority to resolve Tier 1 uncertainty.

        Receives both the original case and the Tier 1 decision so the
        MD Agent can explicitly address why the Frontline Agent was uncertain.

        Teaching note: the key difference from Tier 1 is the FRAMING.
        The MD Agent's prompt tells it: "you are NOT re-evaluating from scratch.
        You are specifically resolving the uncertainty the Tier 1 agent had."
        This produces better outputs than asking the MD Agent to evaluate
        the case independently, because it focuses the model's attention
        on the actual ambiguity rather than starting over from the top.

        Args:
            context:           Original clinical case + guidelines
            previous_decision: The Tier 1 agent's decision and rationale

        Returns:
            AuthorizationDecision with final status and MD-level rationale
        """
        # Build a structured prompt that puts the Tier 1 decision front and center.
        # The MD Agent must address the Tier 1 hesitation — not ignore it.
        tier1_rationale = (
            previous_decision.rationale
            if isinstance(previous_decision.rationale, str)
            else str(previous_decision.rationale)
        )

        user_input = (
            f"## Tier 1 Agent Decision (Escalated)\n"
            f"- Status: {previous_decision.status.value}\n"
            f"- Confidence: {previous_decision.confidence_score:.2f}\n"
            f"- Tier 1 Rationale: {tier1_rationale}\n\n"
            f"## Original Clinical Case\n"
            f"{context.case.model_dump_json(indent=2)}\n\n"
            f"## Clinical Guidelines\n"
            f"{context.relevant_guidelines}"
        )

        draft = await self.execute(
            user_input=user_input,
            response_model=DecisionDraft,
        )
        return _decision_from(
            draft,
            ReviewTier.MEDICAL_DIRECTOR_AGENT,
            model_id=self.config.model,
            prompt_version=self.prompt_version,
        )
