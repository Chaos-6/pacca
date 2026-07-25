# PACCA — Specification-Driven Design Document
## Agentic AI System Specification for Clinical Prior Authorization

**System:** PACCA — Prior Authorization & Care Coordination Agent Platform
**Version:** 2.3.0
**Author:** David Reed, PhD, MBA, PMP | Executive Fellow, Wharton
**Date:** July 2026
**Repository:** github.com/drdgreed/pacca

**Revision history:** v2.3 is a methodological-currency update, not a structural revision. It self-classifies this document's rigor tier against Piskala's (arXiv:2602.00180, Jan 2026) three-tier spec-driven-development taxonomy, published after v2.2, and updates the GitHub Spec-Kit provenance note to reflect the ecosystem's current maturity. No contract, invariant, or governance content changed.

---

## Methodological Framing

This document applies specification-driven design (SDD) as it has emerged in the context of agentic AI systems. Unlike traditional IEEE 830/29148 specifications written for deterministic software, agentic AI systems introduce novel specification challenges: agents exhibit probabilistic behavior, can deviate from intended behavior over time (behavioral drift), and interact through emergent multi-agent dynamics. A specification framework adequate for agentic systems must address not only what the system shall do, but also what the agents must not do, what invariants must hold across agent interactions, and how the system governs its own evolution.

**Self-classification against Piskala's SDD rigor tiers.** Piskala ("Spec-Driven Development: From Code to Contract in the Age of AI Coding Assistants," arXiv:2602.00180, January 2026) defines three rigor tiers: *spec-first* (specs guide development but drift from code over time), *spec-anchored* (specs continuously validate and gate code), and *spec-as-source* (specs generate code directly). Part II's Agent Behavioral Contracts sit in the **spec-anchored** tier: PRE/POST/INVARIANT and GIVEN/WHEN/THEN clauses are written before implementation and act as validation gates — enforced by §C7.2's drift-detection CI gate and the P7 compositionality proofs — that catch contract drift a conventional unit-test suite would miss. (This document's "specification-driven *design*" was coined independently of Piskala's "spec-driven *development*"; the taxonomy still applies.) Against Piskala's general-purpose framework, PACCA's contribution is narrower: multi-agent, clinical-domain contracts with an explicit drift-detection and rollback protocol (C7, C8).

This document synthesizes four complementary traditions, each applied where it adds the most clarity:

**Part I — Intent and Stakeholder Specification**
Drawn from GitHub's Spec-Kit methodology (initially released September 2024; now at v0.11.0 as of June 2026, with 30+ agent integrations, 111k+ GitHub stars, and a 70+-extension community ecosystem that now includes quality gates for security and drift detection — external validation that the spec-first approach has scaled well beyond a single-project experiment) and the SPARC framework (Specification, Pseudocode, Architecture, Refinement, Completion). Captures *why* the system exists, *who* interacts with it, and *what* success looks like before any implementation detail. Specifications at this level are user-journey-oriented and written to be readable by non-engineers.

**Part II — Agent Behavioral Contracts**
Drawn from Agent Behavioral Contracts (ABC) research (arXiv 2602.22302, February 2026) and Design-by-Contract theory (Bertrand Meyer). Defines each agent's *preconditions*, *postconditions*, *invariants*, and *resource bounds* using plain-English contracts with pseudo-formal structure. These contracts are specification-first: they define expected behavior before deployment, not inferred from execution traces after the fact.

**Part III — Orchestration Protocol Invariants**
Drawn from multi-agent architecture research (arXiv 2512.09458) and Protocol Invariants theory for bounded, observable, and governable multi-agent systems. Specifies the *message schemas*, *role-capability scopes*, *termination conditions*, and *compositionality properties* of the multi-agent pipeline.

**Part IV — Compliance and Governance Specification**
Drawn from AGENTSAFE and POLARIS governance frameworks (2025–2026). Specifies the *design-time*, *runtime*, and *audit controls* governing the system's compliance envelope, including HIPAA provisions, behavioral drift detection, and the governed policy evolution pipeline.

**Traceability Layer**
A full traceability matrix mapping every requirement identifier (REQ-ID) to its source, implementing artifact, and verification test — an adaptation of IEEE 830 conventions applied as a traceability layer over the agentic specification.

---

## Table of Contents

**Part I — Intent and Stakeholder Specification**
- S1. System Purpose and Scope
- S2. Stakeholder Identification
- S3. User Journeys
- S4. Success Criteria
- S5. System Constraints and Assumptions

**Part II — Agent Behavioral Contracts**
- C1. Contract Framework and Notation
- C2. DecisionSupportAgent Contract
- C3. MedicalDirectorAgent Contract
- C4. EvidenceAggregationAgent Contract
- C5. ClinicalClassificationAgent Contract
- C6. PolicyEvolutionAgent Contract
- C7. Behavioral Drift Specification
- C8. Resource Contracts

**Part III — Orchestration Protocol Invariants**
- P1. Message Schema Specification
- P2. Role-Capability Scope Assignments
- P3. The 7-Branch Escalation Protocol
- P4. Pre-Flight Invariants
- P5. Post-Agent Routing Invariants
- P6. Termination Conditions
- P7. Multi-Agent Compositionality

**Part IV — Compliance and Governance Specification**
- G1. HIPAA Audit Control Specification
- G2. Authentication and Access Control Specification
- G3. Policy Evolution Governance Specification
- G4. Clinical Evaluation and Drift Detection Specification
- G5. Runtime Governance Controls

**Traceability Matrix**

---

# PART I — INTENT AND STAKEHOLDER SPECIFICATION

---

## S1. System Purpose and Scope

### S1.1 Problem Statement

Prior authorization processing in U.S. healthcare consumes an estimated $50–100 billion annually in administrative overhead. Providers spend 34+ hours per week on manual authorization workflows. 29% of delayed or denied authorizations directly harm patient outcomes. Reviewer decision quality varies 18–35% depending on the individual — a structural inconsistency with no solution in a purely manual process.

### S1.2 System Purpose

PACCA automates prior authorization decisions by evaluating clinical cases against current guidelines using a hierarchical multi-agent AI pipeline. The system makes autonomous decisions on well-documented routine cases, routes genuinely complex cases to human reviewers, and maintains a complete audit trail for every decision at the level required by HIPAA Security Rule 164.312(b).

The system's safety property is this: *the human review burden shall be reduced without reducing the quality of human oversight on cases that require it.* Every design decision in this specification flows from this property.

### S1.3 Scope

**In scope:**
- Prior authorization submission, evaluation, and decision
- Confidence-tiered escalation to AI Tier 2 review or human review
- Clinical guideline retrieval via vector database
- Institutional memory from human override decisions
- HIPAA-conscious audit trail
- Runtime operational configuration
- Governed policy evolution via human-approved guideline amendments
- Clinical accuracy evaluation framework

**Out of scope (deferred to production release):**
- EHR integration via FHIR APIs
- Automated provider communication
- Appeal narrative generation
- OAuth 2.0 / SAML federated identity
- Redis semantic caching
- Kubernetes auto-scaling

### S1.4 Compliance Context

The system is designed for eventual production deployment subject to:
- HIPAA Security Rule (45 CFR Part 164)
- FDA AI/ML SaMD Action Plan (change control for clinical decision support)
- CMS coverage determination integrity requirements

---

## S2. Stakeholder Identification

| Stakeholder | Role | Primary Interaction | Quality Attribute Priority |
|---|---|---|---|
| Treating Physician / Provider | Submits authorization requests | React dashboard, REST API | Speed, transparency of rationale |
| Utilization Management Nurse | Reviews human-routed cases | Review interface, audit trail | Completeness of AI reasoning, clear escalation rationale |
| Medical Director | Approves policy amendments, reviews Tier 2 escalations | Governance API, proposal interface | Trust in AI reasoning quality, audit completeness |
| Healthcare Payer (Organization) | Deploys and operates PACCA | All | Consistency, audit compliance, cost control |
| Compliance Officer | Audits PHI access and decision trail | Audit log queries | Completeness, tamper-evidence, correlation-ID traceability |
| HIPAA Security Officer | Evaluates security posture | Security configuration, logs | Authentication, access control, encryption path |
| Platform Engineer | Operates and maintains PACCA | Docker Compose, runtime config API | Observability, operational control, restart-free config |

---

## S3. User Journeys

### S3.1 Journey: Routine Authorization (Automated Path)

*Actor: Treating Physician*

1. Physician submits authorization request including ICD-10 diagnosis code, CPT/HCPCS procedure code, clinical notes, and urgency level.
2. System validates JWT and logs submission to audit trail before any processing.
3. System retrieves relevant guidelines via semantic search.
4. Orchestrator runs pre-flight checks. No flags triggered.
5. DecisionSupportAgent evaluates case. All criteria documented. Confidence: 0.97.
6. Orchestrator routes: confidence ≥ 0.95 → AUTO_APPROVED without human touch.
7. Physician receives JSON decision with rationale within 30 seconds.

**Success:** Physician receives an explainable authorization decision referencing specific guideline criteria. No human reviewer time consumed.

### S3.2 Journey: Complex Authorization (MD Escalation Path)

*Actor: Treating Physician, then Medical Director Agent*

1–4. Same as S3.1.
5. DecisionSupportAgent evaluates case. Criteria partially met but cost exceeds $100K threshold. Confidence: 0.92.
6. Orchestrator routes: 0.90 ≤ confidence < 0.95 → MedicalDirectorAgent invoked.
7. MedicalDirectorAgent receives Tier 1 decision and addresses the specific confidence gap. Returns confidence 0.96.
8. Decision: AUTO_APPROVED at Tier 2.

**Success:** High-cost case receives a second-opinion evaluation. Medical Director Agent explicitly addresses why Tier 1 was uncertain.

### S3.3 Journey: Safety-Critical Escalation (Pre-Flight Path)

*Actor: Treating Physician, then Human Reviewer*

1–3. Same as S3.1.
4. Orchestrator runs pre-flight checks. Procedure code Q2041 (CAR-T) detected in EXPERIMENTAL_PROCEDURE_CODES.
5. Case routes immediately to IN_REVIEW. No LLM call made. Rationale logged: "Procedure Q2041 is on the experimental treatment list. No autonomous AI decision appropriate."
6. Human reviewer receives case with pre-flight rationale.

**Success:** Experimental treatment never receives an autonomous AI decision regardless of documentation quality. Zero LLM cost incurred on pre-escalated cases.

### S3.4 Journey: Institutional Learning (Institutional Memory Path)

*Actor: Human Reviewer, then Future Physician*

1. Human reviewer receives case for MRI authorization. Patient has only 4 weeks of conservative therapy (CMS rule requires 6), but has developing foot drop.
2. Reviewer approves with documented rationale: "Foot drop constitutes neurological emergency — 6-week rule overridden. This exception should apply to future similar cases."
3. Rationale is embedded into `case_precedents` ChromaDB collection.
4. Future physician submits identical scenario (4 weeks conservative therapy, foot drop).
5. RAG pipeline retrieves the precedent alongside CMS guidelines.
6. DecisionSupportAgent cites both the exception clause AND the precedent. Approves with confidence 0.96.

**Success:** System improves from human judgment without model retraining, without code changes, without engineering intervention.

### S3.5 Journey: Governed Policy Evolution

*Actor: PolicyEvolutionAgent, then Medical Director*

1. Over 10 cases, foot-drop MRI approvals below 6-week threshold recur consistently.
2. Platform Engineer triggers `POST /admin/optimize_policies`.
3. PolicyEvolutionAgent analyzes pattern: 12 cases over 6 months, all approved by Medical Directors, all citing foot drop.
4. Agent produces PolicyProposal: "Amend CMS LCD L34976 to add: 'Exception: progressive motor weakness (foot drop) constitutes neurological emergency and overrides the 6-week conservative therapy requirement.'"
5. Medical Director reviews at `GET /admin/proposals/{id}`. Reviews pattern evidence. Approves.
6. Amendment deployed to `nccn_guidelines` ChromaDB collection. PolicyChangeLogEntry created.
7. Future similar cases now auto-approve on Branch 1 without requiring human review.

**Success:** Clinical policy evolves from evidence. Human maintains control. Complete regulatory audit trail preserved.

---

## S4. Success Criteria

These are the top-level verifiable outcomes against which the system is evaluated:

| ID | Criterion | Verification Method | Target |
|---|---|---|---|
| SC-01 | Routine cases (complete documentation, clear guideline alignment) are auto-approved without human touch | Demo Group A execution | ≥90% of Group A cases AUTO_APPROVED |
| SC-02 | Experimental treatment cases never receive autonomous AI decisions | Pre-flight unit tests | 100% of Group D cases IN_REVIEW |
| SC-03 | Sparse-documentation cases never produce hallucinated clinical values | LLM-as-judge zero-tolerance test | 0 hallucinations on GC-018, GC-019 |
| SC-04 | Overall clinical reasoning quality meets threshold | CI accuracy gate | ≥80% of golden cases score ≥3 |
| SC-05 | Every PHI access produces a correlation-ID-linked audit record | Audit trail unit tests | 100% of test cases produce complete audit chain |
| SC-06 | System refuses to start with weak SECRET_KEY | Security unit tests | RuntimeError on key < 32 chars |
| SC-07 | No guideline amendment deploys without human approval | Governance unit tests | 0 amendments deployed without reviewer_id |
| SC-08 | Runtime config changes take effect without server restart | Config API unit tests | Config change verified in same session |

---

## S5. System Constraints and Assumptions

### S5.1 Hard Constraints

**C-HARD-01.** The system shall never make an autonomous approval or denial decision for cases involving experimental treatments as defined in EXPERIMENTAL_PROCEDURE_CODES or identified by experimental keywords in clinical notes.

**C-HARD-02.** The system shall never make an autonomous decision for cases with ICD-10 diagnosis codes matching RARE_CONDITION_ICD10_PREFIXES.

**C-HARD-03.** No clinical guideline shall be modified in ChromaDB without a PolicyChangeLogEntry recording the change ID, original text, new text, approving reviewer ID, and timestamp.

**C-HARD-04.** The application shall refuse to serve requests if SECRET_KEY is absent or shorter than 32 characters.

**C-HARD-05.** All routes accessing clinical data or operational configuration shall require a valid JWT.

### S5.2 Soft Constraints

**C-SOFT-01.** The system should complete routine authorization decisions within 30 seconds end-to-end.

**C-SOFT-02.** The system should support 500+ concurrent authorization requests via the async architecture.

**C-SOFT-03.** The system should produce decision rationales that cite specific guideline criteria by name rather than offering vague summaries.

### S5.3 Assumptions

**A-01.** The Anthropic Claude API is available with sufficient rate limits for the expected authorization volume. The retry mechanism handles transient unavailability up to `llm_retry_max_attempts` attempts.

**A-02.** Clinical guidelines in the `nccn_guidelines` ChromaDB collection are current at the time of retrieval. The system does not verify guideline publication dates — this is an operational responsibility.

**A-03.** Provider NPIs supplied in JWT claims are valid and have been verified by the authentication system. PACCA does not re-verify NPI validity.

**A-04.** The `prior_denial_codes` list supplied with each request accurately reflects the patient's denial history. PACCA does not maintain its own longitudinal patient record — this is an integration responsibility.

---

# PART II — AGENT BEHAVIORAL CONTRACTS

---

## C1. Contract Framework and Notation

This Part defines behavioral contracts for each of PACCA's five agents. Contracts follow the Agent Behavioral Contracts (ABC) framework (arXiv 2602.22302, 2026), which extends Design-by-Contract to multi-session, multi-turn LLM agent behavior.

Each contract is structured as:

```
AGENT CONTRACT: <AgentName>

PRECONDITIONS       — what must be true before the agent is invoked
POSTCONDITIONS      — what must be true after the agent returns
INVARIANTS          — properties that must hold throughout execution
PROHIBITIONS        — what the agent must never do (hard negative constraints)
RESOURCE BOUNDS     — maximum resources the agent may consume per invocation
ESCALATION TRIGGERS — conditions under which the agent must recommend escalation
```

**Notation convention:**

- `shall` — mandatory requirement, no exceptions
- `shall not` — hard prohibition, no exceptions
- `should` — strong recommendation, exceptions require documented justification
- `may` — permitted but not required

Pseudo-formal structure uses the form:
```
GIVEN [context condition]
WHEN [triggering event]
THEN [required outcome]
```

This structure is equivalent to a precondition (GIVEN), activation condition (WHEN), and postcondition (THEN) without requiring formal temporal logic notation.

---

## C2. DecisionSupportAgent Contract

**Role:** Tier 1 Frontline Utilization Management Nurse. Evaluates clinical cases against retrieved guidelines and returns a confidence-scored recommendation.

**Invocation context:** Called by the Orchestrator for every case that passes pre-flight checks.

```
PRECONDITIONS
  PRE-DSA-01: A ClinicalCase object with non-empty patient_id,
              primary_diagnosis_code, and procedure_code shall exist.
  PRE-DSA-02: A non-empty guidelines_context string shall be provided,
              containing at least one clinical guideline relevant to the
              procedure code.
  PRE-DSA-03: A valid ANTHROPIC_API_KEY shall be set in the environment.

POSTCONDITIONS
  POST-DSA-01: The agent shall return an AuthorizationDecision containing:
               - status: one of AUTO_APPROVED | IN_REVIEW | DENIED
               - confidence_score: float in [0.0, 1.0]
               - rationale: non-empty string
               - review_tier_used: AUTOMATED
  POST-DSA-02: The rationale shall reference specific clinical criteria
               from the provided guidelines_context using the terms
               "MET", "NOT MET", or "UNCLEAR" for each evaluated criterion.
  POST-DSA-03: If documentation is absent for a required criterion,
               the rationale shall name the missing element explicitly.
               The agent shall not infer, assume, or fabricate values
               for missing clinical data.
  POST-DSA-04: confidence_score shall reflect genuine uncertainty.
               A case with one or more UNCLEAR criteria shall not receive
               confidence_score ≥ 0.90.

INVARIANTS
  INV-DSA-01: The agent shall not access external systems, databases, or APIs
              beyond the Claude API call itself.
  INV-DSA-02: The agent shall not modify the ClinicalCase or the
              guidelines_context it receives.
  INV-DSA-03: prompt_version shall equal "v2.2" throughout the agent's
              lifetime in this deployment.

PROHIBITIONS
  PROHIB-DSA-01: The agent shall not invent, assume, or infer clinical
                 values not explicitly present in the case submission.
                 GIVEN a clinical submission missing a lab value,
                 WHEN the evaluation requires that lab value,
                 THEN the agent shall state the value is absent and mark
                 the criterion as UNCLEAR.
  PROHIB-DSA-02: The agent shall not override pre-flight escalation
                 triggers. A case pre-escalated for experimental treatment
                 shall not be re-routed to auto-approval by this agent.
  PROHIB-DSA-03: The agent shall not produce a confidence_score ≥ 0.95
                 when the rationale contains UNCLEAR criteria.

RESOURCE BOUNDS
  RES-DSA-01: max_tokens per response: 4096
  RES-DSA-02: max_retry_attempts: settings.llm_retry_max_attempts (default: 3)
  RES-DSA-03: max_wait_between_retries: settings.llm_retry_wait_max_seconds (default: 30s)
  RES-DSA-04: temperature: 0.0 (deterministic sampling — same input shall
              produce consistent outputs across invocations)

ESCALATION TRIGGERS
  ESC-DSA-01: confidence_score < 0.90 → Orchestrator routes to IN_REVIEW
  ESC-DSA-02: 0.90 ≤ confidence_score < 0.95 → Orchestrator routes to
              MedicalDirectorAgent
  ESC-DSA-03: Any UNCLEAR criterion on a case with estimated cost > $100,000
              → agent should set confidence_score < 0.90
```

---

## C3. MedicalDirectorAgent Contract

**Role:** Tier 2 Chief Medical Director. Resolves Tier 1 ambiguity by explicitly addressing the source of Tier 1 uncertainty.

**Invocation context:** Called by the Orchestrator only when DecisionSupportAgent returns confidence in [0.90, 0.95).

```
PRECONDITIONS
  PRE-MDA-01: A Tier 1 AuthorizationDecision with confidence in [0.90, 0.95)
              shall exist.
  PRE-MDA-02: The original ClinicalCase and guidelines_context shall be
              provided alongside the Tier 1 decision.
  PRE-MDA-03: PRE-DSA-03 (valid API key) applies.

POSTCONDITIONS
  POST-MDA-01: The agent shall return an AuthorizationDecision containing:
               - status: AUTO_APPROVED | IN_REVIEW
               - confidence_score: float in [0.0, 1.0]
               - rationale: non-empty string
               - review_tier_used: MEDICAL_DIRECTOR_AGENT
  POST-MDA-02: The rationale shall explicitly identify the Tier 1 source
               of uncertainty and state whether it is resolved or unresolved.
               A rationale that does not address Tier 1 uncertainty shall be
               treated as a contract violation.
  POST-MDA-03: POST-DSA-03 (hallucination prohibition) applies with equal
               force to this agent.
  POST-MDA-04: confidence_score ≥ 0.95 → Orchestrator sets
               status = AUTO_APPROVED.
               confidence_score < 0.95 → Orchestrator sets
               status = IN_REVIEW.

INVARIANTS
  INV-MDA-01: The agent shall not modify the Tier 1 decision record.
              It produces a new AuthorizationDecision — it does not
              overwrite the existing one.
  INV-MDA-02: INV-DSA-01, INV-DSA-02, INV-DSA-03 apply.

PROHIBITIONS
  PROHIB-MDA-01: The agent shall not override pre-flight escalation triggers
                 under any circumstances. The MDA has no authority over
                 Branches 4–7.
  PROHIB-MDA-02: PROHIB-DSA-01 (hallucination prohibition) applies.

RESOURCE BOUNDS
  Same as DecisionSupportAgent (RES-DSA-01 through RES-DSA-04).
```

---

## C4. EvidenceAggregationAgent Contract

**Role:** Synthesizes submitted clinical data into a structured narrative for downstream agents.

**Invocation context:** Called once per authorization request before clinical evaluation agents.

```
PRECONDITIONS
  PRE-EAA-01: A ClinicalCase with at least one EvidenceItem shall exist.

POSTCONDITIONS
  POST-EAA-01: The agent shall return a structured narrative identifying:
               - primary diagnosis with ICD-10 code
               - requested procedure with CPT/HCPCS code
               - prior treatments documented in evidence
               - current clinical status based on submitted evidence
               - medical necessity justification if documentable
  POST-EAA-02: The narrative shall not add clinical information not present
               in the submitted EvidenceItems.
  POST-EAA-03: Missing information shall be identified explicitly
               (e.g., "Prior therapy history: not documented in submission").

INVARIANTS
  INV-EAA-01: INV-DSA-01, INV-DSA-02 apply.

PROHIBITIONS
  PROHIB-EAA-01: PROHIB-DSA-01 (hallucination prohibition) applies.

RESOURCE BOUNDS
  Same as DecisionSupportAgent.
```

---

## C5. ClinicalClassificationAgent Contract

**Role:** Scores case complexity, identifies medical specialty, and assesses urgency.

**Invocation context:** Called after EvidenceAggregationAgent, before guideline retrieval.

```
PRECONDITIONS
  PRE-CCA-01: A synthesized clinical narrative from EvidenceAggregationAgent
              shall exist.

POSTCONDITIONS
  POST-CCA-01: The agent shall return:
               - complexity_score: integer in [1, 5]
               - specialty: string (e.g., "oncology", "rheumatology")
               - urgency_level: LOW | STANDARD | EXPEDITED | URGENT
               - routing_recommendation: AUTO | SPECIALIST | HUMAN
  POST-CCA-02: complexity_score ≥ 4 → routing_recommendation shall be
               SPECIALIST or HUMAN.
  POST-CCA-03: urgency_level == URGENT → Orchestrator shall not queue the
               case in a standard review pool.

INVARIANTS
  INV-CCA-01: INV-DSA-01, INV-DSA-02 apply.

PROHIBITIONS
  PROHIB-CCA-01: PROHIB-DSA-01 (hallucination prohibition) applies.

RESOURCE BOUNDS
  Same as DecisionSupportAgent.
```

---

## C6. PolicyEvolutionAgent Contract

**Role:** Analyzes human override patterns and produces governed policy amendment proposals.

**Invocation context:** Triggered by `POST /admin/optimize_policies`. Not invoked in the authorization request pipeline.

```
PRECONDITIONS
  PRE-PEA-01: Access to case_precedents ChromaDB collection shall exist.
  PRE-PEA-02: At least one human override decision shall exist in
              case_precedents.

POSTCONDITIONS
  POST-PEA-01: The agent shall produce a PolicyProposal containing:
               - guideline_id: identifier of the guideline to be amended
               - current_text: the exact current guideline text
               - proposed_text: the specific proposed replacement text
               - pattern_evidence: list of case IDs demonstrating the pattern
               - override_count: number of overrides supporting the proposal
               - clinical_rationale: explanation of why the amendment is warranted
               - reviewer_checklist: structured checklist for Medical Director review
               - status: "pending" (proposals are NEVER deployed by this agent)
  POST-PEA-02: The proposal shall be stored in the proposal store with
               status="pending". No ChromaDB collection shall be modified
               by this agent.

INVARIANTS
  INV-PEA-01: The agent shall have no write access to the nccn_guidelines
              ChromaDB collection.
  INV-PEA-02: The agent shall have no write access to the case_precedents
              ChromaDB collection.
  INV-PEA-03: proposals_deployed_by_agent = 0 at all times.
              (The agent produces proposals; humans deploy them.)

PROHIBITIONS
  PROHIB-PEA-01: The agent shall not call vector_store.add_guideline() or
                 any ChromaDB write method.
  PROHIB-PEA-02: The agent shall not set proposal.status = "approved" or
                 proposal.status = "deployed".
  PROHIB-PEA-03: PROHIB-DSA-01 (hallucination prohibition) applies — the
                 agent shall not fabricate case IDs, override counts, or
                 guideline text not present in the case_precedents collection.

RESOURCE BOUNDS
  RES-PEA-01: max_tokens per response: 4096
  RES-PEA-02: max_retry_attempts: settings.llm_retry_max_attempts
  RES-PEA-03: temperature: 0.1 (slight variation permitted for proposal
              language diversity across multiple invocations on the
              same data)
```

---

## C7. Behavioral Drift Specification

Behavioral drift refers to the progressive divergence of agent behavior from its specified contracts over extended operation. This is a recognized failure mode in multi-session LLM systems (Rath 2026, "Agent Stability Index") and is particularly critical in clinical AI systems where drift may be clinically consequential.

### C7.1 Drift Definition for PACCA

For each PACCA agent, behavioral drift is defined as a measurable change in the distribution of the agent's outputs that is not attributable to changes in the input distribution.

```
DRIFT-DEF-01 (DecisionSupportAgent):
  Drift is detected when the rolling approval rate over 100 consecutive
  cases deviates by more than ±15% from the 30-day baseline approval rate,
  without a corresponding change in the distribution of procedure codes
  or diagnosis codes in submitted cases.

DRIFT-DEF-02 (DecisionSupportAgent):
  Drift is detected when the rolling hallucination rate (measured by
  LLM-as-judge on a weekly sample of 20 cases) exceeds 5% in any
  rolling 4-week window.

DRIFT-DEF-03 (MedicalDirectorAgent):
  Drift is detected when the Tier 2 reversal rate (cases where MD
  Agent changes Tier 1 decision) deviates by more than ±20% from
  the 30-day baseline reversal rate.

DRIFT-DEF-04 (PolicyEvolutionAgent):
  Drift is detected when the agent produces proposals citing case IDs
  not present in the case_precedents collection, or when proposals
  contain proposed_text not derivable from any existing guideline.
```

### C7.2 Drift Detection Mechanism

The primary drift detection mechanism is the LLM-as-judge CI gate (`tests/clinical/test_clinical_accuracy.py`):

```
DRIFT-DETECT-01:
  GIVEN the golden dataset of 20 annotated cases
  WHEN the CI evaluation suite is executed
  THEN the LLM-as-judge shall score each agent decision 1–5
       against its expected outcome and reasoning requirements

DRIFT-DETECT-02:
  GIVEN the LLM-as-judge scores
  WHEN overall accuracy < 0.80 (fewer than 16 of 20 cases score ≥3)
  THEN the CI pipeline shall fail and deployment shall be blocked

DRIFT-DETECT-03:
  GIVEN cases GC-018 and GC-019 (hallucination trap cases)
  WHEN the LLM-as-judge detects any hallucinated clinical value
       in either case
  THEN the CI pipeline shall fail immediately, independent of the
       overall accuracy gate
```

### C7.3 Drift Response Protocol

```
DRIFT-RESPONSE-01:
  GIVEN DRIFT-DETECT-02 triggers (accuracy < 80%)
  WHEN the CI gate fails on a deployment pipeline
  THEN:
    (a) Deployment is blocked automatically
    (b) The specific failing cases are reported in the CI output
    (c) Whether hallucinations were detected is reported separately
    (d) The last passing commit is tagged for rollback

DRIFT-RESPONSE-02:
  GIVEN DRIFT-RESPONSE-01 has blocked a deployment
  WHEN the cause is identified as model-side drift
       (Anthropic has released a new model version)
  THEN re-evaluation against the golden dataset shall occur
       before re-enabling deployment with the new model

DRIFT-RESPONSE-03:
  GIVEN production behavioral drift is suspected
       (approval rate or hallucination rate exceeds drift thresholds)
  WHEN a Medical Director or Platform Engineer issues
       `PATCH /admin/config {"enable_autonomous_decisions": false}`
  THEN all cases route to human review immediately,
       without server restart, within one API call
```

---

## C8. Resource Contracts

Resource contracts bound the computational resources each agent may consume per invocation. These are derived from the tenacity retry configuration in `config/settings.py`.

### C8.1 Per-Agent Resource Envelope

```
RESOURCE CONTRACT: All Agents (default)

TOKEN BUDGET:
  Input:  bounded by guidelines_context length + clinical_notes length
          (typically 2,000–8,000 input tokens per invocation)
  Output: max_tokens = 4096 (hard cap enforced by API)

RETRY BUDGET:
  max_attempts = settings.llm_retry_max_attempts (default: 3)
  min_wait     = settings.llm_retry_wait_min_seconds (default: 1.0s)
  max_wait     = settings.llm_retry_wait_max_seconds (default: 30.0s)

  TOTAL WORST-CASE WALL TIME PER AGENT:
    = (max_attempts × max_llm_latency) + (retry_waits)
    = (3 × 8s) + (1s + 2s)
    = 27 seconds worst-case per agent

  For a 2-agent pipeline (Tier 1 + Tier 2):
    Total worst-case = 54 seconds
    (This is the basis for the 60-second agent_timeout setting)

TERMINATION CONDITIONS (hard stops — cannot be overridden by retry):
  - 401 AuthenticationError → fail immediately, do not retry
  - 400 BadRequestError → fail immediately, do not retry
  - ValidationError from Pydantic → fail immediately, do not retry
  - After settings.llm_retry_max_attempts exhausted → reraise last error
```

### C8.2 Pipeline-Level Resource Contract

```
RESOURCE CONTRACT: Authorization Pipeline

GIVEN a single authorization request
THEN total resource consumption shall not exceed:
  - LLM API calls:  max 2 (Tier 1 + Tier 2, each with max 3 retries = 6 total calls)
  - LLM API calls:  0 for pre-flight-escalated cases
  - Wall time:      ≤ 60 seconds (settings.agent_timeout)
  - Audit records:  4–7 per request (submission + per-agent start/complete + final)
  - ChromaDB reads: 1 dual-collection query per request

TERMINATION: The request shall terminate — with an error status and audit record —
if agent_timeout is exceeded. The error shall be recorded with success=False.
```

---

# PART III — ORCHESTRATION PROTOCOL INVARIANTS

---

## P1. Message Schema Specification

All inter-component communication uses typed Pydantic models. Untyped messages are prohibited. The type system enforces contract boundaries between the Orchestrator, agents, and the data layer.

### P1.1 Inbound Message Schema (Authorization Request)

```
AuthorizationRequest {
  request_id:    str           (UUID, required)
  patient_id:    str           (required)
  provider_npi:  str           (10-digit NPI, required)
  clinical_case: ClinicalCase  (required, non-empty)
}

ClinicalCase {
  patient_id:               str                 (required)
  primary_diagnosis_code:   str                 (ICD-10, required)
  procedure_code:           str                 (CPT/HCPCS, required)
  evidence:                 List[EvidenceItem]  (required, min length 1)
}

EvidenceItem {
  id:            str                 (required)
  source_type:   EvidenceSourceType  (LAB_RESULT | CLINICAL_NOTE |
                                      MEDICATION | PATIENT_REPORTED)
  description:   str                 (required, non-empty)
  original_text: str                 (required, non-empty)
  confidence:    float               ([0.0, 1.0])
}
```

**Schema invariant:** Any request failing Pydantic validation shall return HTTP 422 before reaching any agent or the Orchestrator. Validation is a pre-agent gate, not an agent responsibility.

### P1.2 Outbound Message Schema (Authorization Decision)

```
AuthorizationDecision {
  decision_id:       str                  (UUID7, time-sortable)
  status:            AuthorizationStatus  (AUTO_APPROVED | IN_REVIEW |
                                           DENIED | PENDING)
  confidence_score:  float                ([0.0, 1.0])
  rationale:         str                  (non-empty)
  review_tier_used:  ReviewTier           (AUTOMATED | MEDICAL_DIRECTOR_AGENT | HUMAN)
  timestamp:         datetime             (UTC)
  audit_trail:       List[AuditLogEntry]
}
```

**Schema invariant:** A decision with `status = AUTO_APPROVED` shall never have `review_tier_used = HUMAN`. A decision produced by pre-flight escalation shall have `confidence_score = 0.0` and `review_tier_used = HUMAN`.

### P1.3 Audit Record Schema

```
AuditLogEntry {
  entry_id:       str       (UUID7)
  correlation_id: str       (shared across all records for one request)
  action:         str       (from controlled vocabulary — see P1.4)
  actor:          str       (provider NPI | agent name | "system")
  actor_type:     str       ("provider" | "agent" | "user" | "system")
  success:        bool
  error_message:  str | None
  duration_ms:    int
  token_usage:    dict | None   ({"input_tokens": int, "output_tokens": int})
  details:        dict | None   (action-specific structured data)
  timestamp:      datetime      (UTC, auto-set at creation)
}
```

### P1.4 Controlled Audit Action Vocabulary

The `action` field in audit records shall use only values from this controlled vocabulary. Free-form action strings are prohibited.

| Action | Actor | Trigger |
|---|---|---|
| `authorization_submitted` | provider NPI | Request received, before any processing |
| `agent_decision_started` | DecisionSupportAgent | Before Tier 1 LLM call |
| `agent_decision_completed` | DecisionSupportAgent | After Tier 1 returns |
| `agent_medical_director_started` | MedicalDirectorAgent | Before Tier 2 LLM call |
| `agent_medical_director_completed` | MedicalDirectorAgent | After Tier 2 returns |
| `escalation_auto_approved` | orchestrator | Branch 1 routing decision |
| `escalation_medical_director` | orchestrator | Branch 2 routing decision |
| `escalation_human_review_required` | orchestrator | Branch 3 routing decision |
| `escalation_pre_flight_triggered` | orchestrator | Branches 4–7 routing decision |
| `agent_call_failed` | agent name | Any agent exception |
| `authorization_finalized` | system | Final status persisted to database |
| `policy_proposal_created` | PolicyEvolutionAgent | New proposal stored |
| `policy_proposal_approved` | reviewer ID | Medical Director approves |
| `policy_proposal_rejected` | reviewer ID | Medical Director rejects |
| `policy_amendment_deployed` | system | ChromaDB updated after approval |
| `config_updated` | admin user | Runtime config changed via PATCH /admin/config |

---

## P2. Role-Capability Scope Assignments

Each agent is assigned a capability scope defining exactly what system resources it may access. Capability scope violations are detected by unit tests and auditable via the action vocabulary.

| Agent | ChromaDB Read | ChromaDB Write | Database Write | External API | Config Read |
|---|---|---|---|---|---|
| DecisionSupportAgent | None | None | Via audit only | Claude API | settings.* |
| MedicalDirectorAgent | None | None | Via audit only | Claude API | settings.* |
| EvidenceAggregationAgent | None | None | Via audit only | Claude API | settings.* |
| ClinicalClassificationAgent | None | None | Via audit only | Claude API | settings.* |
| PolicyEvolutionAgent | case_precedents (read) | None | Via audit only | Claude API | settings.* |
| Orchestrator | None | None | Via audit only | None | settings.* |
| GuidelineRetriever | nccn_guidelines + case_precedents (read) | None | None | None | None |
| Admin Route (approved proposal) | None | nccn_guidelines (write) | PolicyChangeLog | None | settings.* |

**Scope invariant:** An agent that writes to ChromaDB outside of the Admin Route approval flow shall be treated as a contract violation. This invariant is enforced by the PolicyEvolutionAgent prohibition PROHIB-PEA-01 and verified by tests in `test_prompt_engineering.py`.

---

## P3. The 7-Branch Escalation Protocol

The escalation protocol is a deterministic decision procedure executed by the Orchestrator. It is not probabilistic, not model-dependent, and does not change based on LLM output for Branches 4–7.

### P3.1 Protocol State Machine

```
State: SUBMITTED
  → Run ClinicalRiskDetector.evaluate()
  → If flags.should_pre_escalate == True:
      → Transition to HUMAN_REVIEW_PRE_FLIGHT
  → Else:
      → Transition to TIER_1_EVALUATION

State: TIER_1_EVALUATION
  → Run DecisionSupportAgent.run(context)
  → Evaluate confidence_score:
      → If confidence ≥ 0.95 AND status == AUTO_APPROVED:
          → Transition to AUTO_APPROVED (Branch 1)
      → If 0.90 ≤ confidence < 0.95:
          → Transition to TIER_2_EVALUATION (Branch 2)
      → If confidence < 0.90:
          → Transition to HUMAN_REVIEW_LOW_CONFIDENCE (Branch 3)

State: TIER_2_EVALUATION
  → Run MedicalDirectorAgent.run(context, tier1_decision)
  → Evaluate confidence_score:
      → If confidence ≥ 0.95:
          → Transition to AUTO_APPROVED (Branch 2a)
      → Else:
          → Transition to HUMAN_REVIEW_MD_INSUFFICIENT (Branch 2b)

Terminal States:
  AUTO_APPROVED              — decision returned immediately
  HUMAN_REVIEW_PRE_FLIGHT    — branches 4–7
  HUMAN_REVIEW_LOW_CONFIDENCE — branch 3
  HUMAN_REVIEW_MD_INSUFFICIENT — branch 2b
```

### P3.2 Protocol Invariants

```
PROTO-INV-01: The AUTO_APPROVED terminal state shall only be reached via
              Branch 1 or Branch 2a. No other path produces AUTO_APPROVED.

PROTO-INV-02: HUMAN_REVIEW_PRE_FLIGHT shall be reached without any LLM
              API call. The LLM API shall not be called for pre-flight
              escalated cases.

PROTO-INV-03: The TIER_2_EVALUATION state shall only be entered from
              TIER_1_EVALUATION. MedicalDirectorAgent shall not be invoked
              for pre-flight escalated cases.

PROTO-INV-04: Every state transition shall produce at least one audit record
              before the transition completes.

PROTO-INV-05: The protocol shall terminate in a finite number of steps.
              The maximum depth is:
              SUBMITTED → TIER_1_EVALUATION → TIER_2_EVALUATION → terminal
              = 3 state transitions, bounded by RES-DSA-02 retry budget.
```

---

## P4. Pre-Flight Invariants

Pre-flight checks are pure functions with no side effects. They run deterministically before any LLM call.

```
PRE-FLIGHT-INV-01 (Experimental Treatment):
  GIVEN a ClinicalCase with procedure_code P
  WHEN P is in EXPERIMENTAL_PROCEDURE_CODES
  THEN flags.should_pre_escalate == True
       AND flags.reasons contains EscalationReason.EXPERIMENTAL_TREATMENT
       AND no LLM API call is made

PRE-FLIGHT-INV-02 (Experimental Treatment — Keywords):
  GIVEN a ClinicalCase with evidence text T
  WHEN any term in EXPERIMENTAL_DIAGNOSIS_KEYWORDS appears in T
  THEN (same as PRE-FLIGHT-INV-01)

PRE-FLIGHT-INV-03 (Rare Condition):
  GIVEN a ClinicalCase with primary_diagnosis_code D
  WHEN any prefix in RARE_CONDITION_ICD10_PREFIXES is a prefix of D
  THEN flags.should_pre_escalate == True
       AND flags.reasons contains EscalationReason.RARE_CONDITION
       AND no LLM API call is made

PRE-FLIGHT-INV-04 (Conflicting Guidelines):
  GIVEN guidelines_context string G
  WHEN G contains both an approval marker and a rejection marker
  THEN flags.should_pre_escalate == True
       AND flags.reasons contains EscalationReason.CONFLICTING_GUIDELINES
       AND no LLM API call is made

PRE-FLIGHT-INV-05 (Prior Denial):
  GIVEN a ClinicalCase with procedure_code P
       AND prior_denial_codes list containing P
  THEN flags.should_pre_escalate == True
       AND flags.reasons contains EscalationReason.PRIOR_DENIAL_SAME_SERVICE
       AND no LLM API call is made

PRE-FLIGHT-INV-06 (Multiple Triggers):
  GIVEN a case that triggers multiple pre-flight checks simultaneously
  THEN ALL triggered reasons shall appear in flags.reasons
       AND the audit record shall enumerate all triggered reasons
```

---

## P5. Post-Agent Routing Invariants

```
ROUTE-INV-01:
  GIVEN DecisionSupportAgent returns decision D
  WHEN D.confidence_score >= 0.95 AND D.status == AUTO_APPROVED
  THEN Orchestrator returns D immediately
       AND audit action == "escalation_auto_approved"

ROUTE-INV-02:
  GIVEN DecisionSupportAgent returns decision D
  WHEN 0.90 <= D.confidence_score < 0.95
  THEN MedicalDirectorAgent is invoked with D as Tier 1 reference
       AND audit action == "escalation_medical_director"

ROUTE-INV-03:
  GIVEN DecisionSupportAgent returns decision D
  WHEN D.confidence_score < 0.90
  THEN D.status is overwritten with IN_REVIEW
       AND Orchestrator returns D without invoking MedicalDirectorAgent
       AND audit action == "escalation_human_review_required"

ROUTE-INV-04:
  GIVEN MedicalDirectorAgent returns decision M
  WHEN M.confidence_score >= 0.95
  THEN M.status is set to AUTO_APPROVED
       AND Orchestrator returns M

ROUTE-INV-05:
  GIVEN MedicalDirectorAgent returns decision M
  WHEN M.confidence_score < 0.95
  THEN M.status is set to IN_REVIEW
       AND Orchestrator returns M
```

---

## P6. Termination Conditions

The authorization pipeline shall terminate under all conditions — including API failures, timeouts, and unexpected exceptions.

```
TERM-01 (Normal termination):
  The pipeline terminates when the Orchestrator reaches any terminal state
  in the P3.1 state machine and returns an AuthorizationDecision.

TERM-02 (API exhaustion termination):
  GIVEN all retry attempts for an LLM call are exhausted
  THEN:
    (a) The exception is re-raised to the route handler
    (b) The route handler catches the exception
    (c) An audit record is written with success=False and the error message
    (d) HTTP 503 is returned to the caller

TERM-03 (Timeout termination):
  GIVEN the agent pipeline has been running for > settings.agent_timeout seconds
  THEN:
    (a) The pipeline is interrupted
    (b) An audit record is written with success=False
    (c) The case is marked IN_REVIEW with rationale "Pipeline timeout"
    (d) HTTP 504 is returned to the caller

TERM-04 (Schema violation termination):
  GIVEN the LLM returns a response that fails Pydantic validation
  THEN:
    (a) The exception is NOT retried (validation errors are not transient)
    (b) Steps (b)–(d) from TERM-02 apply

TERM-05 (Pre-flight termination — not an error):
  GIVEN ClinicalRiskDetector.evaluate() returns should_pre_escalate == True
  THEN:
    (a) Pipeline terminates immediately with IN_REVIEW status
    (b) Audit record written with action == "escalation_pre_flight_triggered"
    (c) HTTP 200 returned with IN_REVIEW decision (not an error)
```

---

## P7. Multi-Agent Compositionality

Compositionality specifies how the behavior of the multi-agent pipeline relates to the behavior of individual agents. This is necessary because multi-agent systems can exhibit emergent failures that do not appear in individual agent testing.

```
COMPOSE-01 (Output type preservation):
  GIVEN DecisionSupportAgent satisfies POST-DSA-01 (returns valid AuthorizationDecision)
  AND MedicalDirectorAgent satisfies POST-MDA-01 (returns valid AuthorizationDecision)
  THEN the Orchestrator pipeline shall always return a valid AuthorizationDecision.
  The type guarantee composes across agents.

COMPOSE-02 (Hallucination prohibition is pipeline-wide):
  GIVEN PROHIB-DSA-01 holds for DecisionSupportAgent
  AND PROHIB-MDA-02 holds for MedicalDirectorAgent
  THEN the final decision rationale shall never contain hallucinated values,
  regardless of which agent tier produced it.

COMPOSE-03 (Pre-flight takes precedence over all agent outputs):
  GIVEN PRE-FLIGHT-INV-01 through PRE-FLIGHT-INV-05 hold
  THEN no agent output can cause a pre-flight-escalated case to be AUTO_APPROVED.
  The pre-flight invariants are absorbing — once triggered, no downstream
  computation can reverse them.

COMPOSE-04 (Resource bound composition):
  GIVEN RES-DSA-01 through RES-DSA-04 hold for each individual agent
  THEN the pipeline-level resource bound in C8.2 is achievable.
  The pipeline's worst-case resource consumption equals the sum of
  individual agent worst-case consumptions.

COMPOSE-05 (Audit trail completeness):
  GIVEN every state transition in P3.1 produces at least one audit record
  AND every agent execution produces start + complete audit records
  THEN for any authorization request, the complete set of audit records
  sharing its correlation_id constitutes a full account of all decisions made.
  No decision-relevant action shall be unrecorded.
```

---

# PART IV — COMPLIANCE AND GOVERNANCE SPECIFICATION

---

## G1. HIPAA Audit Control Specification

**CFR Citation:** 45 CFR §164.312(b) — Audit Controls

```
HIPAA-AUDIT-01 (Pre-write requirement):
  GIVEN a provider submits an authorization request
  WHEN the request is received by the API route handler
  THEN an audit record with action="authorization_submitted" shall be
       committed to the database BEFORE any agent call, ChromaDB query,
       or processing of any kind.
  Rationale: A crash between receipt and processing must produce an
  audit record proving the request was received.

HIPAA-AUDIT-02 (Correlation-ID requirement):
  GIVEN an authorization request with request_id R
  THEN all audit records produced by the processing of R shall share
       a single correlation_id UUID.
  THEN a single database query on correlation_id shall retrieve the
       complete audit chain for R.

HIPAA-AUDIT-03 (Start/complete pairs):
  GIVEN an agent is invoked
  THEN an audit record with action="agent_<name>_started" shall be
       written before the LLM call.
  AND an audit record with action="agent_<name>_completed" shall be
       written after the LLM call returns.
  An orphaned "started" record with no matching "completed" record
  identifies the exact failure point.

HIPAA-AUDIT-04 (Failure is auditable):
  GIVEN any exception occurs during request processing
  THEN an audit record with success=False and error_message populated
       shall be written.
  The success=False field shall be distinguishable by query without
  log file parsing.

HIPAA-AUDIT-05 (Actor accountability):
  GIVEN any audit record
  THEN the actor field shall be populated with a non-null identifier:
       provider NPI, agent name, or "system".
  No audit record shall have an anonymous actor.

HIPAA-AUDIT-06 (Immutability):
  No route handler, agent, or service class shall implement an UPDATE
  or DELETE operation on the AuditLogModel table.
  Failures produce new records with success=False.
  They do not modify existing success=True records.
```

---

## G2. Authentication and Access Control Specification

**CFR Citations:** 45 CFR §164.312(d), §164.312(a)(1), §164.312(a)(2)(iii)

```
AUTH-01 (Fail-fast SECRET_KEY):
  GIVEN the application starts (FastAPI lifespan event)
  WHEN SECRET_KEY environment variable is absent
       OR len(SECRET_KEY) < 32
  THEN the application shall raise RuntimeError and refuse to serve requests.
  Rationale: A misconfigured deployment fails loudly at startup, not silently
  at the first authentication request.

AUTH-02 (JWT requirement):
  GIVEN any route under /api/v1/authorizations/ or /api/v1/admin/
  WHEN a request arrives without a valid JWT Bearer token
  THEN the request shall be rejected with HTTP 401 before reaching
       any route handler logic, agent, or database operation.

AUTH-03 (Token lifetime):
  GIVEN a JWT is issued at login
  THEN the token shall expire after TOKEN_EXPIRE_MINUTES (default: 30) minutes.
  THEN a request with an expired token shall receive HTTP 401.
  Rationale: 30-minute expiry limits PHI exposure window per
  §164.312(a)(2)(iii) automatic logoff requirement.

AUTH-04 (Password hashing):
  GIVEN a user password P
  THEN P shall be hashed with bcrypt using a unique per-password salt.
  THEN P shall never be stored in plaintext in any database, log, or
       environment variable.
  THEN password verification shall use bcrypt.checkpw() (timing-safe).

AUTH-05 (CORS restriction):
  GIVEN a production deployment
  THEN CORS_ORIGINS shall not contain "*".
  THEN all allowed origins shall be explicitly enumerated.
```

---

## G3. Policy Evolution Governance Specification

**Regulatory Reference:** FDA AI/ML SaMD Action Plan — change control for AI-driven clinical decision support

```
GOV-01 (Proposal-only output):
  GIVEN PolicyEvolutionAgent executes
  THEN the only modification to system state shall be the creation of
       a PolicyProposal record with status="pending".
  THEN no ChromaDB collection shall be modified.

GOV-02 (Approval gate):
  GIVEN a PolicyProposal with status="pending"
  WHEN POST /admin/proposals/{id}/approve is called with reviewer_id R
  THEN:
    (a) The amendment shall be deployed to nccn_guidelines ChromaDB
    (b) A PolicyChangeLogEntry shall be created recording:
        - change_id (UUID)
        - proposal_id
        - guideline_id
        - original_text (the text BEFORE amendment)
        - new_text (the text AFTER amendment)
        - approved_by: R
        - deployed_at (timestamp)
        - rationale_summary
    (c) proposal.status shall be updated to "deployed"

GOV-03 (Rejection):
  GIVEN a PolicyProposal with status="pending"
  WHEN POST /admin/proposals/{id}/reject is called
  THEN:
    (a) proposal.status shall be updated to "rejected"
    (b) No ChromaDB modification shall occur

GOV-04 (Change log immutability):
  GIVEN a PolicyChangeLogEntry exists
  THEN it shall never be deleted or modified.
  THEN GET /admin/change-log shall return all entries in creation order.

GOV-05 (No autonomous deployment):
  GIVEN no human has approved a proposal
  THEN no policy amendment shall appear in nccn_guidelines.
  THEN policy_amendments_deployed_without_reviewer_id = 0 at all times.
```

---

## G4. Clinical Evaluation and Drift Detection Specification

```
EVAL-01 (Golden dataset integrity):
  GIVEN the golden dataset in tests/clinical/golden_cases.py
  THEN:
    (a) Every case shall have an expected_outcome field
    (b) Every case shall have a reasoning_must_include list (non-empty)
    (c) Every case shall have a clinical_rationale field
    (d) All 8 escalation scenario groups (A through H) shall be represented
    (e) At least 2 hallucination trap cases (sparse documentation) shall exist

EVAL-02 (CI accuracy gate):
  GIVEN the CI evaluation suite executes
  WHEN overall_accuracy = (cases scoring ≥3) / total_cases
  THEN overall_accuracy >= 0.80 or CI pipeline fails

EVAL-03 (Hallucination zero-tolerance):
  GIVEN hallucination trap cases GC-018 and GC-019
  WHEN the LLM-as-judge scores either case
  THEN any hallucinated clinical value detected = score of 1
       = CI pipeline fails immediately, independent of overall_accuracy

EVAL-04 (Drift detection cadence):
  GIVEN a production deployment
  THEN the CI evaluation suite shall execute on every pull request to main
  THEN the CI evaluation suite should execute weekly in production on a
       rolling sample of 20 recent cases scored against their audit trail

EVAL-05 (Judge scoring contract):
  GIVEN LLM-as-judge evaluates a case
  THEN the judge shall apply the rubric:
    5: Correct decision, complete criterion citation, no hallucination
    4: Correct decision, mostly complete, minor gaps
    3: Correct decision, adequate but vague reasoning
    2: Wrong decision OR correct decision with seriously flawed reasoning
    1: Wrong decision on clearly documented case OR any hallucination
  THEN score 1 shall be automatic for any hallucination regardless of decision correctness
```

---

## G5. Runtime Governance Controls

```
RUNTIME-GOV-01 (Master autonomy switch):
  GIVEN the system is in autonomous operation
  WHEN `PATCH /admin/config {"enable_autonomous_decisions": false}` is executed
  THEN all subsequent authorization requests shall route to IN_REVIEW
       regardless of confidence score or documentation quality.
  THEN this change shall take effect without server restart.
  THEN an audit record with action="config_updated" shall be written.

RUNTIME-GOV-02 (Threshold adjustment):
  GIVEN a PATCH /admin/config request modifying confidence thresholds
  WHEN auto_approve_confidence_threshold <= escalation_confidence_threshold
  THEN the request shall be rejected with HTTP 422.
  Rationale: A configuration where the escalation threshold equals or
  exceeds the auto-approve threshold is logically invalid and could
  silently disable escalation.

RUNTIME-GOV-03 (Configuration audit):
  GIVEN any PATCH /admin/config request
  THEN an audit record shall be written recording:
       - which fields changed
       - old values
       - new values
       - which user made the change

RUNTIME-GOV-04 (Configuration rollback):
  GIVEN DELETE /admin/config/overrides is executed
  THEN all runtime configuration overrides shall be cleared.
  THEN the effective configuration shall revert to environment variable defaults.
  THEN an audit record shall be written.
```

---

# TRACEABILITY MATRIX

This matrix maps every specification requirement to its implementing source artifact and its verification test. Requirements without a verification test are flagged as gaps.

| REQ-ID | Requirement Summary | Source | Implementing Artifact | Verification Test |
|---|---|---|---|---|
| **Part I — Intent** | | | | |
| SC-01 | Routine cases auto-approved | S4 | agents/orchestrator.py | demo/run_demo.py --groups A |
| SC-02 | Experimental cases never auto-approved | S4, C-HARD-01 | agents/clinical_risk_detector.py | test_escalation_tree.py::test_branch_4_experimental |
| SC-03 | Zero hallucinations on sparse cases | S4 | agents/prompts/templates.py | test_clinical_accuracy.py::test_zero_hallucinations |
| SC-04 | 80% clinical accuracy gate | S4 | tests/clinical/evaluator.py | test_clinical_accuracy.py::test_ci_gate |
| SC-05 | Every PHI access produces audit record | S4, HIPAA-AUDIT-01 | api/routes/authorizations.py | test_audit_trail.py::test_audit_written_before_processing |
| SC-06 | Weak SECRET_KEY refused at startup | S4, AUTH-01 | api/auth.py | test_security_and_scalability.py::test_startup_rejects_weak_key |
| SC-07 | No amendment without human approval | S4, GOV-05 | agents/evolution.py | test_prompt_engineering.py::test_evolution_agent_cannot_deploy |
| SC-08 | Config change without restart | S4, RUNTIME-GOV-01 | api/routes/admin.py | test_config_api.py::test_config_change_takes_effect |
| **Part II — Agent Contracts** | | | | |
| PRE-DSA-01 | ClinicalCase required pre-call | C2 | api/routes/authorizations.py | test_models.py::test_clinical_case_validation |
| PRE-DSA-02 | Non-empty guidelines_context required | C2 | agents/orchestrator.py | test_escalation_tree.py::test_empty_guidelines_context |
| POST-DSA-01 | Returns typed AuthorizationDecision | C2 | agents/decision.py | test_models.py::test_authorization_decision_structure |
| POST-DSA-02 | Rationale cites MET/NOT MET/UNCLEAR | C2 | agents/prompts/templates.py | test_clinical_accuracy.py (all cases) |
| POST-DSA-03 | Missing info named explicitly | C2 | agents/prompts/templates.py | test_clinical_accuracy.py::GC-018, GC-019 |
| POST-DSA-04 | UNCLEAR criteria → confidence < 0.90 | C2 | agents/prompts/templates.py | test_clinical_accuracy.py::Group B cases |
| INV-DSA-01 | No external system access | C2 | agents/base.py | test_retry_and_tracing.py (mocked client) |
| INV-DSA-03 | prompt_version == "v2.2" | C2 | agents/prompts/templates.py | test_prompt_engineering.py::test_registry_versions |
| PROHIB-DSA-01 | No hallucination of missing values | C2 | agents/prompts/templates.py | test_clinical_accuracy.py::test_zero_hallucinations |
| PROHIB-DSA-02 | Cannot override pre-flight | C2 | agents/orchestrator.py | test_escalation_tree.py::test_pre_flight_not_overridable |
| PROHIB-DSA-03 | confidence ≥ 0.95 requires no UNCLEAR | C2 | agents/prompts/templates.py | test_clinical_accuracy.py (Group A validation) |
| RES-DSA-01 | max_tokens = 4096 | C2, C8 | agents/base.py::AgentConfig | test_retry_and_tracing.py::test_agent_config_defaults |
| RES-DSA-02 | max_retry_attempts from settings | C2, C8 | agents/base.py::_call_with_retry | test_retry_and_tracing.py::test_retry_attempts |
| RES-DSA-04 | temperature = 0.0 | C2, C8 | agents/base.py::AgentConfig | test_retry_and_tracing.py::test_agent_config_defaults |
| POST-MDA-01 | Returns typed decision | C3 | agents/decision.py | test_models.py |
| POST-MDA-02 | Rationale addresses Tier 1 uncertainty | C3 | agents/prompts/templates.py | test_prompt_engineering.py::test_md_prompt_addresses_uncertainty |
| PROHIB-MDA-01 | Cannot override pre-flight | C3 | agents/orchestrator.py | test_escalation_tree.py::test_pre_flight_not_overridable |
| INV-PEA-01 | No write to nccn_guidelines | C6 | agents/evolution.py | test_prompt_engineering.py::test_evolution_no_direct_write |
| INV-PEA-03 | proposals_deployed_by_agent = 0 | C6 | agents/evolution.py | test_prompt_engineering.py::test_evolution_agent_cannot_deploy |
| PROHIB-PEA-01 | No add_guideline() call | C6 | agents/evolution.py | test_prompt_engineering.py::test_evolution_no_direct_write |
| **Part II — Behavioral Drift** | | | | |
| DRIFT-DETECT-02 | Accuracy gate ≥ 80% | C7 | tests/clinical/evaluator.py | test_clinical_accuracy.py::test_ci_gate_passes_at_threshold |
| DRIFT-DETECT-03 | Hallucination = immediate fail | C7 | tests/clinical/evaluator.py | test_clinical_accuracy.py::test_zero_hallucinations |
| DRIFT-RESPONSE-03 | enable_autonomous_decisions=false routes all to human | C7 | api/routes/admin.py | test_config_api.py (master switch test) |
| **Part II — Resource Contracts** | | | | |
| RES-PEA-03 | PolicyEvolutionAgent temperature = 0.1 | C8 | agents/evolution.py | test_prompt_engineering.py::test_evolution_agent_config |
| PIPE-RES-01 | Max 2 LLM calls per request | C8 | agents/orchestrator.py | test_escalation_tree.py (call count assertions) |
| PIPE-RES-02 | 0 LLM calls for pre-flight cases | C8 | agents/orchestrator.py | test_escalation_tree.py::test_pre_flight_no_llm_call |
| **Part III — Protocol Invariants** | | | | |
| SCHEMA-INV-01 | Invalid requests → HTTP 422 pre-agent | P1 | api/routes/authorizations.py | test_security_and_scalability.py::test_invalid_request_422 |
| SCHEMA-INV-02 | AUTO_APPROVED → review_tier != HUMAN | P1 | models/authorization.py | test_models.py::test_decision_tier_consistency |
| SCOPE-INV-01 | No agent writes ChromaDB directly | P2 | agents/* | test_prompt_engineering.py (all agent write tests) |
| PROTO-INV-01 | AUTO_APPROVED only via Branch 1 or 2a | P3 | agents/orchestrator.py | test_escalation_tree.py::test_auto_approve_branches_only |
| PROTO-INV-02 | Pre-flight = no LLM call | P3 | agents/orchestrator.py | test_escalation_tree.py::test_pre_flight_no_llm_call |
| PROTO-INV-04 | Every transition → audit record | P3 | api/routes/authorizations.py | test_audit_trail.py (all transition tests) |
| PRE-FLIGHT-INV-01 | Experimental code → pre-escalate | P4 | agents/clinical_risk_detector.py | test_escalation_tree.py::test_branch_4_* |
| PRE-FLIGHT-INV-03 | Rare ICD-10 prefix → pre-escalate | P4 | agents/clinical_risk_detector.py | test_escalation_tree.py::test_branch_5_* |
| PRE-FLIGHT-INV-04 | Conflicting guidelines → pre-escalate | P4 | agents/clinical_risk_detector.py | test_escalation_tree.py::test_branch_6_* |
| PRE-FLIGHT-INV-05 | Prior denial → pre-escalate | P4 | agents/clinical_risk_detector.py | test_escalation_tree.py::test_branch_7_* |
| ROUTE-INV-01 | conf ≥ 0.95 → AUTO_APPROVED | P5 | agents/orchestrator.py | test_escalation_tree.py::test_branch_1_* |
| ROUTE-INV-02 | 0.90–0.95 → Tier 2 | P5 | agents/orchestrator.py | test_escalation_tree.py::test_branch_2_* |
| ROUTE-INV-03 | conf < 0.90 → IN_REVIEW | P5 | agents/orchestrator.py | test_escalation_tree.py::test_branch_3_* |
| TERM-02 | API exhaustion → 503 with audit | P6 | api/routes/authorizations.py | test_retry_and_tracing.py::test_exhausted_retries |
| TERM-04 | Schema violation → no retry | P6 | agents/base.py | test_retry_and_tracing.py::test_validation_error_no_retry |
| COMPOSE-03 | Pre-flight absorbing | P7 | agents/orchestrator.py | test_escalation_tree.py::test_pre_flight_not_overridable |
| COMPOSE-05 | Audit trail complete for all requests | P7 | api/routes/authorizations.py | test_audit_trail.py::test_complete_chain |
| **Part IV — Compliance** | | | | |
| HIPAA-AUDIT-01 | Audit record before processing | G1 | api/routes/authorizations.py | test_audit_trail.py::test_audit_before_processing |
| HIPAA-AUDIT-02 | Correlation-ID across all records | G1 | api/routes/authorizations.py | test_audit_trail.py::test_correlation_id_propagation |
| HIPAA-AUDIT-03 | Start/complete pairs per agent | G1 | agents/orchestrator.py | test_audit_trail.py::test_start_complete_pairs |
| HIPAA-AUDIT-04 | Failures are audited | G1 | agents/base.py | test_audit_trail.py::test_failure_audit_record |
| HIPAA-AUDIT-05 | Actor populated on all records | G1 | db/repository.py | test_audit_trail.py::test_actor_populated |
| HIPAA-AUDIT-06 | No audit record updates or deletes | G1 | db/repository.py | test_security_and_scalability.py (no DELETE audit tests) |
| AUTH-01 | Fail-fast weak SECRET_KEY | G2 | api/auth.py | test_security_and_scalability.py::test_startup_rejects_weak_key |
| AUTH-02 | JWT required on all clinical routes | G2 | api/main.py | test_security_and_scalability.py::test_unauthenticated_rejected |
| AUTH-03 | Token expiry from TOKEN_EXPIRE_MINUTES | G2 | api/auth.py | test_security_and_scalability.py::test_token_expiry |
| AUTH-04 | bcrypt with unique salt | G2 | api/auth.py | test_security_and_scalability.py::test_bcrypt_hashing |
| GOV-01 | PolicyEvolutionAgent produces proposals only | G3 | agents/evolution.py | test_prompt_engineering.py::test_evolution_proposal_only |
| GOV-02 | Approval creates PolicyChangeLogEntry | G3 | api/routes/admin.py | test_prompt_engineering.py::test_approval_creates_log_entry |
| GOV-04 | Change log immutable | G3 | api/routes/admin.py | test_prompt_engineering.py::test_change_log_append_only |
| GOV-05 | No deployment without reviewer_id | G3 | api/routes/admin.py | test_prompt_engineering.py::test_no_autonomous_deployment |
| EVAL-01 | Golden dataset integrity | G4 | tests/clinical/golden_cases.py | test_clinical_accuracy.py::TestGoldenDatasetIntegrity (all) |
| EVAL-02 | CI accuracy gate 80% | G4 | tests/clinical/evaluator.py | test_clinical_accuracy.py::test_ci_gate_passes_at_threshold |
| EVAL-03 | Hallucination zero-tolerance | G4 | tests/clinical/evaluator.py | test_clinical_accuracy.py::test_zero_hallucinations |
| EVAL-05 | Judge scoring contract 1–5 | G4 | tests/clinical/evaluator.py | test_clinical_accuracy.py::test_score_below_threshold_marks_failed |
| RUNTIME-GOV-01 | Autonomy switch works without restart | G5 | api/routes/admin.py | test_config_api.py::test_autonomy_switch |
| RUNTIME-GOV-02 | Invalid threshold relationship → 422 | G5 | api/routes/admin.py | test_config_api.py::test_threshold_validation |
| RUNTIME-GOV-03 | Config changes audited | G5 | api/routes/admin.py | test_config_api.py::test_config_change_audited |

---

## References

1. GitHub. "Spec-driven development with AI: Get started with a new open source toolkit." GitHub Blog, September 2024.
2. ruvnet. "SPARC Framework." github.com/ruvnet/sparc, 2024.
3. arXiv 2602.22302. "Agent Behavioral Contracts: Formal Specification and Runtime Enforcement for Reliable Autonomous AI Agents." February 2026.
4. arXiv 2512.09458. "Architectures for Building Agentic AI." Chapter 3. December 2025.
5. Moslemi et al. "POLARIS: Governed Orchestration for Enterprise Workflows." AAAI 2026 Workshop.
6. Khan et al. "AGENTSAFE: A Unified Governance Framework for Agentic AI." 2025.
7. Rath. "Agent Stability Index: Behavioral Drift in Multi-Agent LLM Systems." 2026.
8. Thoughtworks. "Spec-driven development." Medium, December 2025.
9. InfoQ. "Spec Driven Development: When Architecture Becomes Executable." January 2026.
10. Anthropic. "Levels of Agentic AI: A Practical Maturity Model for Enterprise Deployment." 2025.
11. U.S. Department of Health and Human Services. "HIPAA Security Rule." 45 CFR Part 164.
12. FDA. "Artificial Intelligence and Machine Learning (AI/ML)-Based Software as a Medical Device (SaMD) Action Plan." 2021.

---

*PACCA SDD v2.2.0 — April 2026*
*github.com/drdgreed/pacca*
*Author: David Reed, PhD | david.reed@interviewkickstart.com*
