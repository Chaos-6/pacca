# Decision Support Agent — Long-Term Memory

This file encodes case-pattern shortcuts learned from prior PACCA evaluations.
Entries describe COMPLETE criteria sets — never just headline indications.
When a pattern matches, the agent must STILL verify each listed criterion
against the case before applying the shortcut. Memory is reasoning **support**,
not reasoning **replacement**.

Entries follow a fixed format so the criterion-preservation discipline is
auditable:

1. **Headline indication** — one sentence naming the case class.
2. **Required criteria** — the full set that ALL must be explicitly documented.
3. **Anti-patterns** — the disqualifying details that flip the outcome.
4. **When the shortcut applies** — the outcome and the rationale-content requirement.
5. **When the shortcut DOES NOT apply** — explicit fallback to standard evaluation.

---

## Pattern: First-line pembrolizumab for metastatic NSCLC with high PD-L1

**Headline indication:** Stage IV (metastatic) non-small cell lung cancer,
first-line pembrolizumab monotherapy, per NCCN Category 1 recommendation
for PD-L1 TPS ≥ 50%.

**Required criteria — ALL must be explicitly documented:**

1. Disease stage is metastatic (stage IV) — NOT stage IIIA or earlier.
   Stage IIIA NSCLC is locally advanced and receives curative-intent
   combined-modality therapy (chemotherapy + radiation ± surgery),
   not first-line systemic monotherapy.
2. PD-L1 tumor proportion score (TPS) is **≥ 50%**, confirmed by a
   validated assay with a documented date.
3. **No** sensitizing EGFR mutations detected on molecular testing.
4. **No** ALK rearrangements detected on molecular testing.
5. **No** prior systemic therapy for metastatic disease (first-line status).
6. ECOG performance status documented (0 or 1 supports treatment tolerability).

**Anti-patterns — disqualify the shortcut, require human review:**

When ANY of the following is present, the auto-approve shortcut does NOT apply
and the case **routes to IN_REVIEW for human clinical judgment** — **NEVER to
DENIED**. These are borderline / off-pattern scenarios where a human clinician
must weigh the specific case context, not cases the system should reject
outright. The status field of your output must be `IN_REVIEW` in all such cases.

- PD-L1 TPS **< 50%** → guidelines recommend combination chemo-immunotherapy,
  not monotherapy. The patient may still be a candidate for combination
  therapy or for treatment with a different agent — that determination is
  a human decision. **Status: IN_REVIEW.** (Not DENIED.)
- EGFR sensitizing mutation present → first-line is targeted therapy
  (e.g. osimertinib), not pembrolizumab. The patient still needs treatment;
  the question is which one. **Status: IN_REVIEW.** (Not DENIED.)
- ALK rearrangement → first-line is ALK inhibitor (e.g. alectinib). Same
  pattern: alternative therapy may be appropriate. **Status: IN_REVIEW.**
  (Not DENIED.)
- Stage IIIA or earlier (locally advanced or earlier) → different treatment
  paradigm. The case may still warrant systemic therapy in specific contexts
  (e.g. unresectable disease, post-definitive-therapy progression). A human
  clinician determines that. **Status: IN_REVIEW.** (Not DENIED.)
- Prior systemic therapy for metastatic disease → not first-line, but may
  still be appropriate later-line therapy. **Status: IN_REVIEW.** (Not DENIED.)

**Why this distinction matters.** PACCA's design routes off-pattern cases
to human review, not to automatic denial. The agent's role is to recognize
that a case doesn't fit the auto-approve shortcut — NOT to make the
adjudication call itself. Multiple anti-patterns in one case do not justify
denial; they reinforce the need for human judgment.

**When the shortcut applies:** AUTO_APPROVE at high confidence (≥ 0.95).
The rationale MUST explicitly cite every required criterion above by its
specific value (e.g. "PD-L1 62%", "no EGFR", "no ALK", "stage IV", "first-line",
"ECOG 1"). Memory is the prompt for thoroughness, not the substitute for it.

**When the shortcut DOES NOT apply:** treat the case as a standard
evaluation under the framework in the main system prompt. The memory
pattern's role is to remind the agent which criteria to check explicitly —
not to skip them.

---

## Pattern: First-line biologic DMARD for seropositive RA after conventional DMARD failure

**Headline indication:** Seropositive rheumatoid arthritis with documented
failure of 2+ conventional DMARDs (typically methotrexate plus at least
one other), moderate-to-severe disease activity, requesting first-line
biologic DMARD therapy (e.g. abatacept, adalimumab, etanercept,
infliximab, tocilizumab) per ACR 2021 guidelines.

**Required criteria — ALL must be explicitly documented:**

1. Diagnosis is rheumatoid arthritis with **seropositive markers** —
   RF (rheumatoid factor) positive AND/OR anti-CCP (anti-cyclic
   citrullinated peptide) positive, with the specific test results
   in the chart.
2. Disease activity is **moderate-to-severe**, with a specific score
   documented: DAS28 ≥ 3.2, CDAI ≥ 10, or SDAI ≥ 11. Vague language
   like "active disease" without a score is not sufficient.
3. Step therapy: failure of **2 or more conventional DMARDs**, each at
   an adequate dose for an adequate duration. The typical pattern is
   methotrexate (≥ 3 months at therapeutic dose) plus at least one
   other conventional DMARD (hydroxychloroquine, sulfasalazine, or
   leflunomide). Document the specific agents, durations, and
   reason for failure (inefficacy or intolerance).
4. Requested agent is on the **ACR-recommended biologic list for RA**
   (TNF inhibitors, abatacept, tocilizumab, rituximab, JAK inhibitors).
5. **No active uncontrolled infection** and no contraindication to
   immunosuppression (e.g. latent TB without treatment, active
   hepatitis B).

**Anti-patterns — disqualify the shortcut, require human review:**

When ANY of the following is present, the auto-approve shortcut does NOT
apply and the case **routes to IN_REVIEW for human clinical judgment** —
**NEVER to DENIED**. These are cases where a clinician must weigh the
specific context, not cases the system should reject outright.

- Only **ONE** conventional DMARD tried (or none) → insufficient step
  therapy. The patient may need a second conventional DMARD trial first,
  OR there may be a documented intolerance pattern that justifies
  earlier biologic — a human determines that. **Status: IN_REVIEW.**
  (Not DENIED.)
- **Seronegative RA** (RF and anti-CCP both negative) → different
  treatment paradigm. Combination conventional DMARDs are typically
  preferred first; biologic may still be appropriate in specific
  contexts. **Status: IN_REVIEW.** (Not DENIED.)
- **Mild disease activity** (DAS28 < 3.2, CDAI < 10, or SDAI < 11) →
  biologic generally not indicated; conventional DMARDs may be sufficient.
  **Status: IN_REVIEW.** (Not DENIED.)
- **Inadequate trial duration** on prior DMARDs (< 3 months on
  methotrexate, etc.) → cannot establish true failure. The patient may
  need to complete the trial OR there may be a tolerability issue. A
  human determines that. **Status: IN_REVIEW.** (Not DENIED.)
- **Active infection / pregnancy / live vaccine within 30 days** →
  contraindication review. **Status: IN_REVIEW.** (Not DENIED.)
- Requested agent is **not** an ACR-recommended biologic for RA →
  unusual choice requires clinical justification. **Status: IN_REVIEW.**
  (Not DENIED.)

**Why this distinction matters.** Same as the NSCLC pembrolizumab entry:
PACCA's design routes off-pattern cases to human review, not to automatic
denial. Multiple anti-patterns in one case do not justify denial; they
reinforce the need for human judgment.

**When the shortcut applies:** AUTO_APPROVE at high confidence (≥ 0.95)
**conditional on** the policy-level cost check. The rationale MUST
explicitly cite (a) seropositive status with specific markers (RF and/or
anti-CCP), (b) each conventional DMARD tried with duration and outcome,
(c) the disease-activity score, and (d) that the requested agent is
ACR-recommended for RA.

**Important interaction with policy escalation (iter-3 chg-1):** if
`ClinicalRiskDetector`'s pre-flight `high_cost_check` fires (estimated
annual cost > HIGH_COST_THRESHOLD), the case routes to IN_REVIEW
regardless of clinical eligibility. The memory does **not** override
that — it gives the agent the clinical-reasoning support to articulate
"criteria met **but cost escalates per policy**" rather than the
incorrect "criteria met → approve" (which would be the wrong outcome on
GC-010 and any future high-cost biologic case). When the pre-flight has
fired, the agent's rationale should still cite the clinical criteria as
met, then acknowledge the cost trigger.

**When the shortcut DOES NOT apply:** treat the case as a standard
evaluation under the framework in the main system prompt.

---

## Pattern: Dupilumab for severe eosinophilic asthma after high-dose ICS / LABA failure

**Headline indication:** Severe persistent asthma uncontrolled on high-dose
ICS/LABA with eosinophilic phenotype (eosinophils ≥ 300/µL OR FeNO ≥ 25 ppb),
patient age ≥ 12, requesting dupilumab as add-on therapy per NIST/GINA
guidelines.

**Required criteria — ALL must be explicitly documented:**

1. Diagnosis is **severe persistent asthma** with the severity tier
   stated in the chart. Vague language like "active asthma" is not
   sufficient; specific GINA Step 4 or 5 classification preferred.
2. **Inadequate control on high-dose ICS / LABA**, with a specific agent
   (e.g. fluticasone/salmeterol, budesonide/formoterol) at maximum dose
   for ≥ 3 months. Objective markers of inadequate control documented
   (multiple ED visits, exacerbations requiring oral steroids, decreased
   PEF, or persistent symptoms despite adherence).
3. **Eosinophilic phenotype** confirmed: eosinophil count ≥ 300/µL OR
   FeNO ≥ 25 ppb. Both values with specific numbers in the chart.
4. **Patient age ≥ 12 years** (per dupilumab label and NIST/GINA).
5. **No contraindication**: no active uncontrolled infection, no live
   vaccine within 30 days, no known dupilumab hypersensitivity.

**Anti-patterns — disqualify the shortcut, require human review:**

When ANY of the following is present, the auto-approve shortcut does NOT
apply and the case **routes to IN_REVIEW for human clinical judgment** —
**NEVER to DENIED**.

- **Mild or moderate asthma** (not severe) → step-up to medium-/high-dose
  ICS/LABA may be appropriate before biologic. **Status: IN_REVIEW.**
  (Not DENIED.)
- **Insufficient ICS/LABA trial duration** (< 3 months at maximum dose) →
  cannot establish inadequate-control criterion. **Status: IN_REVIEW.**
  (Not DENIED.)
- **Non-eosinophilic phenotype** (eos < 300/µL AND FeNO < 25 ppb) →
  alternative biologics (e.g. omalizumab for IgE-mediated, tezepelumab
  for thymic stromal lymphopoietin) may be more appropriate.
  **Status: IN_REVIEW.** (Not DENIED.)
- **Patient age < 12** → dupilumab not labeled for this age group in
  asthma; off-label use requires specialist review. **Status: IN_REVIEW.**
  (Not DENIED.)
- **Active infection / live vaccine / known hypersensitivity** →
  contraindication review. **Status: IN_REVIEW.** (Not DENIED.)

**Why this distinction matters.** Same as the NSCLC and RA entries:
PACCA's design routes off-pattern cases to human review, not to
automatic denial.

**When the shortcut applies:** AUTO_APPROVE at high confidence (≥ 0.95)
**conditional on** the policy-level pre-flight checks (iter-3 chg-1
high_cost_check and pediatric_complex check from iter-5 chg-3). The
rationale MUST explicitly cite (a) severe asthma classification, (b) the
specific high-dose ICS/LABA agent and trial duration, (c) the specific
eosinophil count and/or FeNO value, (d) age, (e) NIST/GINA citation.

**Important interactions with policy escalations (iter-3 chg-1, iter-5 chg-3):**

- If `ClinicalRiskDetector.high_cost_check` fires (annual cost > $100K),
  the case routes to IN_REVIEW regardless of clinical eligibility.
- If `ClinicalRiskDetector.pediatric_complex_check` fires (patient < 18
  with complexity score ≥ 3), the case routes to IN_REVIEW regardless of
  clinical eligibility — even for the canonical severe eosinophilic
  pediatric asthma pattern (this is exactly the GC-012 case, which the
  pediatric_complex check correctly escalates).

The memory does **not** override either policy check. Its role is to
give the agent the clinical-reasoning support to articulate "clinical
criteria met **but policy escalation applies**" rather than the
incorrect "clinical criteria met → approve" on cases like GC-012 (where
the pediatric_complex check fires) or any hypothetical high-cost asthma
biologic case (where high_cost_check fires).

**When the shortcut DOES NOT apply:** treat the case as a standard
evaluation under the framework in the main system prompt.

---

## Pattern: Outpatient benefit-cap exhaustion without a documented exception

**Headline deny-class:** An outpatient service (e.g. physical therapy) requested
after the plan's contractual visit/benefit cap for the period is already
exhausted, where no medical-necessity exception is documented. Anchored on
GC-035 (physical therapy — 50 visits used of a 30-visit annual cap, maintenance
phase). This is a **benefit-design** denial — a contractual coverage limit — and
is the first deny-class entry in this memory; the three entries above are
approve-class.

> **This is the only entry whose shortcut outcome is DENIED.** Its failure mode —
> over-denial — is the one that harms patients. The anti-patterns below are not
> edge notes; they are the core of the entry. When in doubt, you do NOT deny.

**Required criteria for denial — ALL must be explicitly documented:**

1. The **benefit cap is exhausted** — the visits or units already used meet or
   exceed the plan's documented limit for the period (state the count and the
   cap explicitly, e.g. "50 of 30 used").
2. The limit is a **contractual benefit-design limit**, documented in the plan
   benefit, and is **separate from medical necessity** — do not frame this as a
   medical-necessity denial.
3. **No documented exception criterion** is met — specifically no documented
   **acute new injury**, no **post-surgical episode** within the plan window,
   and no **demonstrable functional progress toward measurable goals**.
4. The episode is **maintenance/chronic, not an acute exacerbation** (a new
   acute episode is itself an exception trigger; see anti-patterns).
5. **Plan/policy alignment**: the benefit document specifies the cap and the
   exhaustion is established on the record. State the alignment explicitly.

**Anti-patterns — these FLIP the outcome from DENIED to IN_REVIEW (the
over-denial guard):**

When ANY of the following is present, the deny shortcut does NOT apply and the
case **routes to IN_REVIEW for human clinical judgment** — the agent must NOT
auto-deny. The status field of your output must be `IN_REVIEW` in all such cases.

- **Any documented exception criterion** — an acute new injury, a post-surgical
  episode within the plan window, or demonstrable functional progress toward
  measurable goals → a medical-necessity exception may apply; a human weighs it.
  **Status: IN_REVIEW.** (Not DENIED.)
- **An acute exacerbation or new episode** (not maintenance) → the cap-exception
  pathway may apply and the clinical picture has changed. **Status: IN_REVIEW.**
  (Not DENIED.)
- **Ambiguous or incomplete visit-count or benefit documentation** — the record
  does not clearly establish that the cap is exhausted → you cannot deny on an
  incomplete record. **Status: IN_REVIEW.** (Not DENIED.)
- **A pending appeal or peer-to-peer review**, or a submitted medical-necessity
  exception request → the exception channel is already engaged. **Status:
  IN_REVIEW.** (Not DENIED.)
- **A plausible medical-necessity argument** for continued care despite the cap
  (e.g. recent functional decline with a concrete, measurable goal) → a human
  evaluates it. **Status: IN_REVIEW.** (Not DENIED.)

**Governing rule (read this before denying):** Any uncertainty -> IN_REVIEW.
Never auto-deny on doubt. The **absence of evidence is not evidence of ineligibility**
— a gap in the record is a reason to review, not to deny. A valid
denial must (a) **cite the specific benefit-cap basis** (name the cap and the
used-vs-limit count, e.g. "50 of 30 used") AND (b) **note the appeal /
medical-necessity-exception pathway** as the redirect. A denial that does neither
is not a valid denial — route IN_REVIEW instead.

**When the shortcut applies:** DENIED — but only when **all five required
criteria are explicitly documented** AND **none of the anti-patterns is present**.
The rationale MUST cite the specific benefit-cap basis by name and note the
appeal / exception pathway. If you cannot write that cited rationale, you do not
have a denial — route IN_REVIEW.

**When the shortcut DOES NOT apply:** treat the case as a standard evaluation.
This entry **does not override pre-flight checks** — if any pre-flight escalation
fires (e.g. `experimental_treatment`, `high_cost`, `pediatric_complex`,
`adult_complex`, `prior_denial_same_service`), that routing wins and this entry is
moot. And note the boundary with **medical-necessity** denials: a denial because a
service is **not medically necessary** (the clinical criteria are unmet)
**is NOT this deny pattern** — that is a clinical-criteria evaluation, not a
contractual benefit-cap exhaustion. Do not conflate benefit-design limits with
medical-necessity determinations.

---

*This file is loaded at agent-initialization time by `_prompt_loader.py`
(see iter-2 chg-1's Jinja2 mount-point pattern). Updates here propagate
to the live agent on the next run.*
