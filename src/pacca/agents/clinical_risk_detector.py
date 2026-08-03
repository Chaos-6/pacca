"""
Clinical Risk Detector — pre-flight escalation check for the Orchestrator.

This module implements the four new escalation branches specified in PRD SS5.4:
  - Experimental treatment detection
  - Rare condition (ICD-10 prevalence) detection
  - Conflicting guidelines detection
  - Prior denial on same service detection

Architecture teaching note — why a separate class instead of putting
this logic directly in the Orchestrator?

  The Single Responsibility Principle: each class should have one reason
  to change. The Orchestrator's reason to change is "the workflow changed."
  The ClinicalRiskDetector's reason to change is "our clinical risk criteria
  changed." By separating them:

  1. You can test every detection rule in complete isolation — no mocks
     of agents or databases needed.
  2. You can update clinical criteria (e.g. add new experimental drugs to
     the list) without touching the orchestration workflow.
  3. A compliance auditor can read this file alone to understand all the
     conditions that trigger human review.

Teaching note — pure functions vs. database queries:
  Several of these checks (experimental treatment, rare conditions) use
  curated lists defined right here in the module. You might wonder: why
  not store these in the database and query them dynamically?

  Answer: configuration that affects clinical safety decisions should be
  version-controlled alongside the code that uses it. A database row can
  be changed by anyone with DB access; a Python file requires a code
  review and a deployment. For clinical escalation criteria, that friction
  is a feature, not a bug. The lists below change slowly (quarterly at
  most) and every change should be reviewed.
"""

import re
from dataclasses import dataclass, field

from ..models.clinical import ClinicalCase
from ..models.enums import EscalationReason

# =============================================================================
# Curated clinical reference data
#
# Teaching note: These sets are the "policy" side of the system. The AI
# handles the "reasoning" side. Some decisions should not be left to
# probabilistic reasoning — they require deterministic policy enforcement.
# These sets encode that policy.
# =============================================================================

# Procedure codes for treatments that are investigational, in active clinical
# trials, or not yet standard-of-care. Sources: CMS coverage determinations,
# FDA breakthrough therapy designations, NCCN Category 2B/3 listings.
#
# This is a representative sample for the portfolio prototype. A production
# system would maintain this list in a versioned configuration store and
# update it quarterly in sync with FDA and CMS coverage decisions.
EXPERIMENTAL_PROCEDURE_CODES: frozenset[str] = frozenset(
    {
        # CAR-T cell therapies (FDA-approved for limited indications; experimental for others)
        "Q2041",  # Axicabtagene ciloleucel (Yescarta) — experimental outside approved indications
        "Q2042",  # Tisagenlecleucel (Kymriah) — experimental outside approved indications
        "Q2055",  # Lisocabtagene maraleucel (Breyanzi)
        # Gene therapies
        "J3399",  # Gene therapy — unspecified investigational
        "C9399",  # Investigational device or treatment — unspecified
        # Investigational oncology biologics
        "J9999",  # Unclassified antineoplastic drug (often used for investigational agents)
        "J3490",  # Unclassified drugs (catch-all for unlisted agents)
        # Experimental neural stimulation
        "0278T",  # Transcranial magnetic stimulation — experimental for certain indications
        "0364T",  # Vagus nerve stimulation — experimental for new indications
        # Investigational imaging
        "A9699",  # Radiopharmaceutical — investigational
        # Fecal microbiota transplantation beyond approved indications
        "G0455",  # Fecal microbiota transplantation — experimental outside C. diff indication
    }
)

# Diagnosis keyword fragments that suggest experimental treatment context.
# Used as a secondary check when procedure code is not on the list above.
EXPERIMENTAL_DIAGNOSIS_KEYWORDS: frozenset[str] = frozenset(
    {
        "investigational",
        "experimental",
        "clinical trial",
        "compassionate use",
        "expanded access",
        "off-label",
        "off label",
        "unproven",
        "not fda approved",
        "phase i",
        "phase ii",
        "phase iii",
        "phase 1",
        "phase 2",
        "phase 3",
    }
)

# ICD-10 code prefixes associated with rare diseases (prevalence < 1:10,000).
# Source: NORD (National Organization for Rare Disorders), OMIM, Orphanet.
#
# ICD-10 prefixes are used rather than full codes because rare disease codes
# often have multiple specificity levels (e.g. E70.0, E70.1, E70.2...) that
# all represent the same rare condition family.
RARE_CONDITION_ICD10_PREFIXES: frozenset[str] = frozenset(
    {
        # Phenylketonuria and amino acid metabolism disorders
        "E70",
        # Gaucher disease, Fabry disease, other lysosomal storage disorders
        "E75",
        # Glycogen storage diseases (Pompe, McArdle, etc.)
        "E74",
        # Wilson disease, hemochromatosis
        "E83",
        # Huntington disease
        "G10",
        # Friedreich ataxia and spinocerebellar ataxias
        "G11",
        # Spinal muscular atrophy (SMA)
        "G12",
        # Amyotrophic lateral sclerosis (ALS) — rare but high-cost treatment
        "G12.21",
        # Myasthenia gravis — rare, complex treatment
        "G70",
        # Tuberous sclerosis
        "Q85",
        # Marfan syndrome
        "Q87.4",
        # Ehlers-Danlos syndrome
        "Q79.6",
        # Cystic fibrosis
        "J84.9",
        # Pulmonary arterial hypertension (rare forms)
        "I27.0",
        # Hereditary angioedema
        "D84.1",
        # Systemic mastocytosis
        "D47.0",
        # Aplastic anemia (severe forms)
        "D61",
        # Paroxysmal nocturnal hemoglobinuria
        "D59.5",
        # Primary immunodeficiencies
        "D80",
        "D81",
        "D82",
        "D83",
        "D84",
    }
)

# Guideline source identifiers that, when multiple are present, indicate
# a potential conflict. The detection logic checks whether the RAG context
# contains guidance from multiple sources that use different recommendation
# language (e.g. "recommended" vs "not recommended" for the same treatment).
GUIDELINE_CONFLICT_MARKERS: tuple[str, ...] = (
    "not recommended",
    "contraindicated",
    "avoid",
    "insufficient evidence",
    "conflicting evidence",
    "guideline discordant",
    "not supported",
    "evidence is limited",
)

GUIDELINE_APPROVAL_MARKERS: tuple[str, ...] = (
    "recommended",
    "indicated",
    "supported",
    "evidence-based",
    "standard of care",
    "category 1",
    "strong recommendation",
)


# =============================================================================
# iter-3 chg-1: parsers for branch_2_medical_director triggers
#
# These fall-back parsers extract cost / age / severity from clinical_notes
# text when the structured ClinicalCase fields are None. They are deliberately
# simple — a production system would replace them with structured-data
# requirements upstream — but they let the detector handle the existing
# golden cases (whose data lives in prose) without forcing a dataset rewrite.
# =============================================================================

# Matches "$288,000", "$288K", "$1,234.56". Captures the numeric value and
# any trailing K/k multiplier. The _parse_cost_from_notes function returns
# the MAX across all matches — see its docstring for why.
_COST_DOLLAR_RE = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)\s*(K|k)?", re.IGNORECASE)

# Matches "55-year-old", "14 year old", "age 55", "55yo".
_AGE_RE = re.compile(
    r"(\d{1,3})[\s-]*(?:year[s]?[\s-]*old|yo\b)|age\s*[:\s]+(\d{1,3})",
    re.IGNORECASE,
)

# Severity keywords scanned in priority order (longer phrases first).
_SEVERITY_KEYWORDS: tuple[str, ...] = (
    "moderate-to-severe",
    "moderate to severe",
    "severe",
    "critical",
    "complex",
)


def _parse_cost_from_notes(notes: str) -> float | None:
    """
    Best-effort extraction of an estimated annual cost from clinical notes.

    Strategy: return the MAX of all dollar amounts found in the text.

    Rationale: clinical-cost prose typically mentions multiple amounts (e.g.
    per-infusion cost AND totalled annual cost — "$24,000/infusion x 12 =
    $288,000"). The annualized/totalled value is almost always the largest.
    Using max() is more robust than positional preference (first match,
    last match) and matches the actual semantics of "what does this case
    cost annually?" — the biggest number in the room is the answer.

    Returns None when no dollar amount appears.
    """
    values: list[float] = []
    for match in _COST_DOLLAR_RE.finditer(notes):
        raw, k_suffix = match.group(1), match.group(2)
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            continue
        if k_suffix:
            value *= 1000
        values.append(value)
    return max(values) if values else None


def _parse_age_from_notes(notes: str) -> int | None:
    """Best-effort patient-age extraction from clinical notes."""
    m = _AGE_RE.search(notes)
    if not m:
        return None
    raw = m.group(1) or m.group(2)
    try:
        age = int(raw)
    except (TypeError, ValueError):
        return None
    return age if 0 <= age <= 130 else None


def _parse_severity_from_notes(notes: str) -> str | None:
    """Best-effort severity-keyword detection. Returns the first matching keyword."""
    lower = notes.lower()
    for kw in _SEVERITY_KEYWORDS:
        if kw in lower:
            return kw
    return None


def _evidence_blob(case: ClinicalCase) -> str:
    """Concatenate evidence text for parser fallback. Defined once for reuse."""
    return " ".join(item.description + " " + item.original_text for item in case.evidence)


# =============================================================================
# iter-5 chg-3: complexity-score model for the pediatric_complex check.
#
# Honest framing: this is a HEURISTIC IN SCORE-MODEL CLOTHING, not a data-fit
# discriminator. PACCA's dataset has 4 pediatric cases (GC-012 from iter-1 +
# GC-023/GC-024/GC-025 added in iter-5 chg-2). Four data points is enough to
# defend a per-feature weighting based on clinical rationale, not enough to
# "train" a model. The defensibility comes from each feature having a
# clinical reason, the integer 1-5 range matching the existing Settings
# schema, and the four data points validating the chosen weights against
# expected outcomes.
#
# Weights:
#   - Age extremes (< 18 or > 75):       +2  (developmental / geriatric concerns)
#   - Severity tier:
#       "critical":                       +3
#       "severe" / "moderate-to-severe":  +2
#       "moderate":                       +1
#       "mild" (or absent):               +0
#   - >= 2 prior therapy failures:       +1  (refractory disease pattern)
#   - Multiple comorbidities:            +1  (interaction risk)
#
# Total clamped to [1, 5] to match the Settings schema constraint.
# Pediatric escalation threshold = 3 (one below complexity_specialist_review_min=4).
# =============================================================================

_PRIOR_FAILURE_RE = re.compile(
    r"prior\s+failure|failed\s+(?:trial|to|of)|inadequate\s+(?:response|control)"
    r"|intoleran(?:t|ce)|refractory",
    re.IGNORECASE,
)

_COMORBIDITY_HINTS: tuple[str, ...] = (
    "comorbid",
    "history of",
    "concurrent",
    "growth delay",
    "atopic march",
    "multiple atopic",
)


def _compute_complexity_score(case: ClinicalCase) -> int:
    """
    Compute an integer 1-5 complexity score from case features.

    See module-level comment for the weighting rationale.
    Score is clamped to [1, 5] to match the Settings schema constraint.
    """
    notes_blob = _evidence_blob(case)
    score = 0

    # Age extremes
    age = case.patient_age
    if age is None:
        age = _parse_age_from_notes(notes_blob)
    if age is not None and (age < 18 or age > 75):
        score += 2

    # Severity tier
    severity = (case.disease_severity or "").lower()
    if not severity:
        severity = (_parse_severity_from_notes(notes_blob) or "").lower()
    if "critical" in severity:
        score += 3
    elif (
        "moderate-to-severe" in severity or "moderate to severe" in severity or "severe" in severity
    ):
        score += 2
    elif "moderate" in severity:
        score += 1
    # mild contributes 0; absent contributes 0

    # Prior therapy failures: count 2+ in evidence text
    failure_matches = _PRIOR_FAILURE_RE.findall(notes_blob)
    if len(failure_matches) >= 2:
        score += 1

    # Comorbidities
    lower_blob = notes_blob.lower()
    if any(hint in lower_blob for hint in _COMORBIDITY_HINTS):
        score += 1

    return max(1, min(score, 5))


# =============================================================================
# Escalation flag dataclass
# =============================================================================


@dataclass
class EscalationFlags:
    """
    The result of running all pre-flight checks on a clinical case.

    Teaching note — why a dataclass instead of a dict?
      A dataclass gives you type safety and auto-completion. You can write
      flags.is_experimental and your editor knows that's a bool. With a
      dict you write flags["is_experimental"] and hope you spelled it right.

    Attributes:
        reasons:             List of EscalationReason values that fired
        should_pre_escalate: True if any check fired — shortcuts to human review
        details:             Human-readable explanation of each triggered check
    """

    reasons: list[EscalationReason] = field(default_factory=list)
    details: dict[str, str] = field(default_factory=dict)

    @property
    def should_pre_escalate(self) -> bool:
        """True if any pre-flight check triggered escalation."""
        return len(self.reasons) > 0

    def add(self, reason: EscalationReason, detail: str) -> None:
        """Record a triggered escalation reason with its explanation."""
        self.reasons.append(reason)
        self.details[reason.value] = detail


# =============================================================================
# The detector
# =============================================================================


class ClinicalRiskDetector:
    """
    Evaluates a clinical case for pre-flight escalation triggers.

    This class implements the 4 new PRD SS5.4 escalation branches:
      check_experimental_treatment()  → Branch 4
      check_rare_condition()          → Branch 5
      check_conflicting_guidelines()  → Branch 6
      check_prior_denial()            → Branch 7

    Each method is a pure function: same input always produces same output,
    no side effects, no database calls. This makes them trivially testable.

    Usage:
        detector = ClinicalRiskDetector()
        flags = detector.evaluate(case, guidelines_context, prior_denials)
        if flags.should_pre_escalate:
            # Route directly to human review with flags.reasons recorded
    """

    def evaluate(
        self,
        case: ClinicalCase,
        guidelines_context: str = "",
        prior_denial_codes: list[str] | None = None,
    ) -> EscalationFlags:
        """
        Run all pre-flight checks on a case and return the combined result.

        This is the single entry point for the Orchestrator. It runs all
        four checks and aggregates their results into one EscalationFlags
        object. Multiple checks can fire simultaneously — for example, a
        rare condition treated with an experimental drug triggers both
        RARE_CONDITION and EXPERIMENTAL_TREATMENT.

        Args:
            case:                The clinical case being evaluated
            guidelines_context:  The raw text returned by the RAG pipeline —
                                 used to detect guideline conflicts
            prior_denial_codes:  List of procedure codes that have been
                                 previously denied for this patient

        Returns:
            EscalationFlags with all triggered reasons and their explanations
        """
        flags = EscalationFlags()
        prior_denial_codes = prior_denial_codes or []

        # Run each check and collect any triggered flags
        self._check_experimental_treatment(case, flags)
        self._check_rare_condition(case, flags)
        self._check_conflicting_guidelines(case, guidelines_context, flags)
        self._check_prior_denial(case, prior_denial_codes, flags)
        # iter-3 chg-1: branch_2_medical_director triggers.
        self._check_high_cost(case, flags)
        self._check_pediatric_complex(case, flags)
        self._check_adult_complex(case, flags)  # iter-6 chg-2

        return flags

    # ── Branch 4: Experimental treatment ─────────────────────────────────────

    def _check_experimental_treatment(
        self,
        case: ClinicalCase,
        flags: EscalationFlags,
    ) -> None:
        """
        Detect whether the requested procedure is experimental or investigational.

        Two detection strategies, applied in order:
          1. Exact match against EXPERIMENTAL_PROCEDURE_CODES — the most
             reliable signal; procedure codes are authoritative identifiers
          2. Keyword scan of evidence text — catches cases where the provider
             notes explicitly mention investigational or trial status

        Teaching note — defense in depth:
          Using two strategies means neither a missing code nor missing keywords
          alone defeats the check. A real UM system might add a third strategy:
          querying the FDA clinical trials registry API. That would be a
          production enhancement, and is the right pattern for Week 5.
        """
        procedure = case.procedure_code.upper().strip()

        # Strategy 1: exact procedure code match
        if procedure in EXPERIMENTAL_PROCEDURE_CODES:
            flags.add(
                EscalationReason.EXPERIMENTAL_TREATMENT,
                f"Procedure code {procedure} is on the experimental treatment list. "
                f"No autonomous AI decision appropriate — requires human review.",
            )
            return  # No need to scan keywords if code already matched

        # Strategy 2: keyword scan across all evidence text
        all_evidence_text = " ".join(
            item.description.lower() + " " + item.original_text.lower() for item in case.evidence
        )
        matched_keywords = [kw for kw in EXPERIMENTAL_DIAGNOSIS_KEYWORDS if kw in all_evidence_text]
        if matched_keywords:
            flags.add(
                EscalationReason.EXPERIMENTAL_TREATMENT,
                f"Evidence text contains experimental treatment indicators: "
                f"{', '.join(matched_keywords[:3])}. Requires human review.",
            )

    # ── Branch 5: Rare condition ──────────────────────────────────────────────

    def _check_rare_condition(
        self,
        case: ClinicalCase,
        flags: EscalationFlags,
    ) -> None:
        """
        Detect whether the primary diagnosis is a rare condition.

        Matching strategy: prefix matching on ICD-10 code.

        Teaching note — why prefix matching?
          ICD-10 codes are hierarchical. "E70" means "Disorders of aromatic
          amino-acid metabolism." "E70.0" is phenylketonuria specifically.
          "E70.1" is tyrosinemia. All E70.x codes represent rare metabolic
          disorders. Matching on the prefix catches all specificity levels
          in a single check, which is more maintainable than listing every
          possible E70.x code.

          The trade-off: a prefix check could catch a non-rare condition if
          a rare prefix is more specific than expected. In practice, the
          prefixes in RARE_CONDITION_ICD10_PREFIXES were chosen to correspond
          to disease families that are uniformly rare. Any false positive
          (a non-rare condition escalated to human review) is acceptable —
          it's a conservative error. A false negative (a rare condition not
          escalated) would be a patient safety issue.
        """
        diagnosis_code = case.primary_diagnosis_code.upper().strip()

        for prefix in RARE_CONDITION_ICD10_PREFIXES:
            if diagnosis_code.startswith(prefix):
                flags.add(
                    EscalationReason.RARE_CONDITION,
                    f"Diagnosis code {diagnosis_code} matches rare condition prefix "
                    f"'{prefix}'. Population prevalence < 1:10,000. Clinical guidelines "
                    f"may be sparse or contradictory — requires specialist review.",
                )
                return  # One match is sufficient

    # ── Branch 6: Conflicting guidelines ─────────────────────────────────────

    def _check_conflicting_guidelines(
        self,
        case: ClinicalCase,
        guidelines_context: str,
        flags: EscalationFlags,
    ) -> None:
        """
        Detect whether the retrieved guidelines contain conflicting recommendations.

        Strategy: check whether the guidelines context contains both approval
        language AND rejection/caution language for the same case. If both
        are present, the guidelines conflict and a human must weigh them.

        Teaching note — this is a heuristic, not a certainty:
          The keyword check is a signal, not a guarantee. "Not recommended
          for patients under 18" in a guideline about an adult patient is
          a false positive — the restriction doesn't apply. A production
          system would use a more sophisticated NLP approach (e.g. sentence-
          level sentiment analysis, or structured extraction of guideline
          recommendations). For this portfolio project, the heuristic
          approach demonstrates the pattern and the intent. The important
          engineering principle is that the Orchestrator is structured to
          receive this signal; upgrading the detection algorithm is a
          localized change to this one function.

          False positives (unnecessary human review) are acceptable.
          False negatives (missed conflicts reaching autonomous decisions) are not.

        chg-34 — the trade-off, re-examined and DELIBERATELY KEPT:
          That last line is still the right posture, but the false positives are
          not randomly distributed, and that was not anticipated. Well-written
          clinical guidance is structurally "do X, don't do Y", so approval and
          rejection language co-occur in almost every denial-shaped context. The
          check therefore fires hardest on the cases where the guidance is
          CLEAREST, and the practical consequence is systemic rather than noisy:
          a guideline-based DENIED is effectively unreachable, because the
          pre-flight escalates before the DecisionAgent is ever called.

          Narrowing the marker list was investigated and REJECTED. The obvious
          split -- keep "conflicting evidence" / "guideline discordant" /
          "insufficient evidence", drop "not recommended" / "not supported" /
          "avoid" -- fails, because a genuine two-source conflict is worded
          identically to an aligned one:

              "NCCN: pembrolizumab is recommended as Category 1 ...
               CMS:  treatment is not recommended for patients with ..."

          Dropping those markers would blind the check to that, trading the
          acceptable failure direction for the unacceptable one. The
          discriminator is what the polarities REFER TO -- semantic, not lexical
          -- so no keyword edit can express it.

          The real fix is structural: have retrieval return guidance with source
          attribution and per-source recommendation polarity, so a conflict is
          "two sources, opposite polarity, same subject" rather than "both kinds
          of word appeared". That is a retrieval-layer change, not a detector
          one, and is the recorded path forward.

          Until then GC-027 pins the current contract as PRE_FLIGHT_ESCALATE,
          documenting the accepted false positive rather than pretending the
          system denies a case it does not. This tension sits against the
          decision that PACCA makes coverage determinations (CLAUDE.md safety
          invariants) and is the reason to revisit it.

          Note on "contraindicated": it is in the marker list but detects the
          WORD, not the clinical fact. In GC-085 the guideline reads "cetuximab
          + radiation is appropriate ... when cisplatin is contraindicated" --
          a contraindication to the ALTERNATIVE, i.e. the reason the requested
          therapy qualifies. A real contraindication check would have to read
          the patient record, not the guideline text. Kept because it currently
          contributes only when paired with a separate approval marker, which no
          case in the dataset does, so it costs nothing today.
        """
        if not guidelines_context:
            return

        context_lower = guidelines_context.lower()

        has_rejection = any(m in context_lower for m in GUIDELINE_CONFLICT_MARKERS)

        # chg-30: mask the conflict markers before scanning for approval language.
        #
        # Several approval markers are substrings of their own negations —
        # "recommended" inside "not recommended", "supported" inside
        # "not supported". Scanning the raw text let a single, unambiguous
        # negative statement satisfy BOTH tests and fabricate a conflict with
        # itself. GC-026's guidelines contain "recommended" exactly once,
        # entirely inside "not recommended", and pre-escalated on that alone.
        #
        # The consequence was systematic, not occasional: a guideline-based
        # denial necessarily says "not recommended", so every such case
        # pre-escalated, the DecisionAgent was never called, and DENIED became
        # structurally unreachable on guideline grounds.
        #
        # This fixes only the substring defect. The heuristic's *documented*
        # false positive — approval and rejection language about DIFFERENT
        # services in an aligned source, as in GC-027 — is deliberately left
        # in place; narrowing that is a change to an accepted safety trade-off,
        # not a bug fix, and belongs in its own change.
        masked = context_lower
        for marker in GUIDELINE_CONFLICT_MARKERS:
            masked = masked.replace(marker, " ")
        has_approval = any(m in masked for m in GUIDELINE_APPROVAL_MARKERS)

        if has_approval and has_rejection:
            matched_rejections = [m for m in GUIDELINE_CONFLICT_MARKERS if m in context_lower]
            flags.add(
                EscalationReason.CONFLICTING_GUIDELINES,
                f"Retrieved guidelines contain both approval and rejection language. "
                f"Conflict indicators: {', '.join(matched_rejections[:2])}. "
                f"Human review required to weigh guidelines against patient context.",
            )

    # ── Branch 7: Prior denial on same service ────────────────────────────────

    def _check_prior_denial(
        self,
        case: ClinicalCase,
        prior_denial_codes: list[str],
        flags: EscalationFlags,
    ) -> None:
        """
        Detect whether this patient has a prior denial for the same procedure.

        Strategy: exact match of current procedure code against the list of
        procedure codes previously denied for this patient.

        Teaching note — where does prior_denial_codes come from?
          In this prototype, the Orchestrator passes an empty list by default.
          In a production system, the route handler would query the
          AuthorizationRepository for prior decisions matching this patient ID
          and procedure code with status=DENIED, then pass those codes here.

          That query is the right place to add this: in the route, before
          calling process_decision(), something like:
            prior_denials = await auth_repo.get_denied_procedure_codes(
                patient_id=request.patient_id
            )
          This is a straightforward extension to AuthorizationRepository
          and is designed as a Week 3 enhancement.

          The important point is that the Orchestrator and ClinicalRiskDetector
          are already structured to receive and act on this information — the
          interface is ready even if the data source is not yet wired.
        """
        procedure = case.procedure_code.upper().strip()
        normalized_denials = [code.upper().strip() for code in prior_denial_codes]

        if procedure in normalized_denials:
            flags.add(
                EscalationReason.PRIOR_DENIAL_SAME_SERVICE,
                f"Patient has a prior denial record for procedure {procedure}. "
                f"Human review required to determine if circumstances have changed "
                f"or whether this is a repeat claim.",
            )

    # ── Branch 2: High-cost biologic / drug (iter-3 chg-1) ────────────────────

    def _check_high_cost(
        self,
        case: ClinicalCase,
        flags: EscalationFlags,
    ) -> None:
        """
        Escalate to Medical Director when estimated annual cost exceeds
        settings.high_cost_threshold.

        Data source order:
          1. ClinicalCase.estimated_annual_cost (structured field — preferred)
          2. Parser fallback against the evidence text (regex)

        Why this lives in pre-flight: cost-based escalation is a POLICY rule,
        not a CLINICAL one. It must fire regardless of how convincing the
        clinical case is. Per the iter-2 GC-010 finding, leaving cost rules
        in the agent prompt produces silent failures — the agent skips the
        cost check exactly when it's most confident on clinical merits, which
        is exactly when the cost trigger is most likely to apply.
        """
        from ..config.settings import effective_settings

        threshold = float(effective_settings().high_cost_threshold)

        cost = case.estimated_annual_cost
        if cost is None:
            cost = _parse_cost_from_notes(_evidence_blob(case))
        if cost is None or cost <= threshold:
            return

        flags.add(
            EscalationReason.HIGH_COST,
            f"Estimated annual cost ${cost:,.0f} exceeds the configured "
            f"HIGH_COST_THRESHOLD of ${threshold:,.0f}. Cost-based escalation "
            f"applies regardless of clinical eligibility per policy.",
        )

    # ── Branch 2: Pediatric complexity (iter-3 chg-1; iter-5 chg-3 score model) ─

    PEDIATRIC_AGE_CUTOFF = 18

    # iter-5 chg-3: pediatric escalation threshold = one below the standard
    # complexity_specialist_review_min (4). The pediatric age weight (+2) plus
    # any moderate severity (+1) lands at 3 — the boundary. Severe pediatric
    # cases land at 4+. Mild pediatric cases stay at 2.
    PEDIATRIC_COMPLEXITY_THRESHOLD = 3

    def _check_pediatric_complex(
        self,
        case: ClinicalCase,
        flags: EscalationFlags,
    ) -> None:
        """
        Escalate to Medical Director when the patient is under 18 AND the
        complexity score reaches the pediatric escalation threshold.

        iter-5 chg-3 replaces the iter-3 keyword heuristic with a numeric
        complexity score (integer 1-5). Data source order for the score:
          1. ClinicalCase.complexity_score (structured — preferred)
          2. Computed from case features via _compute_complexity_score

        Age check still gates the whole path — non-pediatric cases never
        reach the score evaluation here. A non-pediatric complexity check
        using the standard complexity_specialist_review_min=4 threshold is
        a separate iteration when a non-pediatric case justifies it.
        """
        notes_blob = _evidence_blob(case)

        age = case.patient_age
        if age is None:
            age = _parse_age_from_notes(notes_blob)
        if age is None or age >= self.PEDIATRIC_AGE_CUTOFF:
            return

        score = (
            case.complexity_score
            if case.complexity_score is not None
            else _compute_complexity_score(case)
        )
        if score < self.PEDIATRIC_COMPLEXITY_THRESHOLD:
            return

        flags.add(
            EscalationReason.PEDIATRIC_COMPLEX,
            f"Pediatric patient (age {age}) with complexity score {score} >= "
            f"threshold {self.PEDIATRIC_COMPLEXITY_THRESHOLD} — specialist "
            f"review required per policy regardless of clinical eligibility "
            f"verification.",
        )

    # ── Branch 2: Adult complexity (iter-6 chg-2 — generalizes the score model) ─
    # Shares PEDIATRIC_AGE_CUTOFF (=18) with the pediatric check above — one
    # boundary constant, so the two age-gated checks can never drift apart.

    def _check_adult_complex(
        self,
        case: ClinicalCase,
        flags: EscalationFlags,
    ) -> None:
        """
        Escalate to specialist review when the patient is an adult (age >= 18)
        AND the complexity score reaches settings.complexity_specialist_review_min.

        Mirrors _check_pediatric_complex but for the adult population and the
        standard (higher) specialist-review threshold. Reuses the iter-5
        _compute_complexity_score unchanged — no model change — and reuses the same
        PEDIATRIC_AGE_CUTOFF boundary, so the two checks are mutually exclusive by
        construction (pediatric < 18, adult >= 18 — no double-fire, no drift).

        Data source order for the score:
          1. ClinicalCase.complexity_score (structured — preferred)
          2. Computed from case features via _compute_complexity_score
        """
        from ..config.settings import effective_settings

        notes_blob = _evidence_blob(case)

        age = case.patient_age
        if age is None:
            age = _parse_age_from_notes(notes_blob)
        if age is None or age < self.PEDIATRIC_AGE_CUTOFF:
            return

        score = (
            case.complexity_score
            if case.complexity_score is not None
            else _compute_complexity_score(case)
        )
        threshold = int(effective_settings().complexity_specialist_review_min)
        if score < threshold:
            return

        flags.add(
            EscalationReason.ADULT_COMPLEX,
            f"Adult patient (age {age}) with complexity score {score} >= "
            f"specialist-review threshold {threshold} — specialist review "
            f"required per policy regardless of clinical eligibility verification.",
        )
