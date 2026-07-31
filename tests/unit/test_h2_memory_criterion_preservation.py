"""
iter-3 chg-2 — H2 institutional memory: criterion-preservation tests.

These are STRUCTURAL tests on the rendered system prompt (the output of
_prompt_loader.load_agent_prompt), not live agent calls. Their purpose is
the H2 analog of iter-1's byte-identity check:

  iter-1's check: every prompt token sent to Claude must match the
  pre-extraction f-string output exactly.

  iter-3's check: when a memory entry compresses a case class, the
  prompt MUST still expose every required criterion the un-compressed
  evaluation would have cited — explicitly — so the agent has no way
  to apply the shortcut without verifying each one.

If a future memory edit removes (or weakens) a required-criterion line,
these tests fail loudly before any live evaluation runs and before any
H2 regression can ship.

No API calls. Pure prompt-rendering assertions.
"""

from __future__ import annotations

from pacca.agents._prompt_loader import load_agent_prompt


class TestMemoryInjection:
    """Verify the memory section is rendered into the Decision agent's prompt."""

    def test_decision_support_prompt_contains_institutional_memory_section(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "## Institutional Memory" in rendered

    def test_decision_support_memory_loads_the_nsclc_pembrolizumab_entry(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "First-line pembrolizumab for metastatic NSCLC" in rendered

    def test_decision_support_prompt_version_is_at_least_v23(self) -> None:
        """
        Any v2.3+ version is a valid H2-active audit signal. The current
        canonical version is asserted by the iter-4 chg-1 test class
        (test_decision_support_prompt_version_bumped_to_v28); this test
        only guards against accidental downgrade below v2.3 (the H2 floor).
        """
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        # Match v2.3, v2.4, v2.5, ... but not v2.0 / v2.1 / v2.2.
        import re

        match = re.search(r"Prompt version: v2\.(\d+)", rendered)
        assert match is not None, "DecisionSupportAgent prompt version missing"
        minor = int(match.group(1))
        assert minor >= 3, f"prompt version v2.{minor} predates H2 (floor: v2.3)"


class TestCriterionPreservation:
    """
    Each entry's required criteria must appear in the rendered prompt — verbatim.

    This is the H2 contract. If an edit to long_term_memory.md silently
    drops a criterion line, the agent loses the prompt-level reminder to
    verify it, and the shortcut becomes unsafe. These tests are the
    safety net against that class of edit.
    """

    def test_memory_lists_stage_metastatic_criterion(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "Disease stage is metastatic (stage IV)" in rendered

    def test_memory_lists_pdl1_50_percent_criterion(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "PD-L1 tumor proportion score (TPS) is **≥ 50%**" in rendered

    def test_memory_lists_no_egfr_criterion(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "No** sensitizing EGFR mutations" in rendered

    def test_memory_lists_no_alk_criterion(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "No** ALK rearrangements" in rendered

    def test_memory_lists_first_line_criterion(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "No** prior systemic therapy" in rendered
        assert "first-line status" in rendered

    def test_memory_lists_ecog_criterion(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "ECOG performance status documented" in rendered


class TestAntiPatternsPreserved:
    """
    The anti-patterns are the iter-2-finding-derived discriminations that
    catch GC-021 (PD-L1 45%) and GC-022 (EGFR+). These MUST stay in the
    memory verbatim; otherwise H2 would compress them away and the
    near-miss memory traps would auto-approve.
    """

    def test_anti_pattern_pdl1_below_threshold(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "PD-L1 TPS **< 50%**" in rendered
        # Anti-patterns must explicitly route to IN_REVIEW (not DENIED).
        # The exact wording uses "Status: IN_REVIEW" with "(Not DENIED.)"
        # clarification — the GC-021 regression showed this distinction matters.
        assert "**Status: IN_REVIEW.**" in rendered
        assert "(Not DENIED.)" in rendered

    def test_anti_pattern_egfr_mutation(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "EGFR sensitizing mutation present" in rendered

    def test_anti_pattern_alk_rearrangement(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "ALK rearrangement" in rendered

    def test_anti_pattern_stage_iiia_or_earlier(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "Stage IIIA or earlier" in rendered

    def test_anti_pattern_prior_systemic_therapy(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "Prior systemic therapy for metastatic disease" in rendered


class TestMemoryIsSupportNotReplacement:
    """
    The H2 design constraint from iter-2/findings/GC-001.md: memory must
    encode FULL criteria sets and must instruct the agent to verify each
    one. These tests enforce that the wording in the rendered prompt is
    "support, not replacement."
    """

    def test_prompt_says_memory_is_support_not_replacement(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        # The "support, not replacement" phrase may be split across a line
        # break in the rendered output; assert each half independently and
        # confirm they appear in the right order, close together.
        assert "Memory is support," in rendered
        assert "not replacement" in rendered
        support_pos = rendered.index("Memory is support,")
        replace_pos = rendered.index("not replacement")
        assert replace_pos > support_pos
        assert replace_pos - support_pos < 100, (
            "Memory-as-support phrasing should appear together; "
            "found split too far apart in rendered prompt"
        )

    def test_prompt_requires_rationale_to_cite_each_criterion(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        # The "must STILL verify" instruction is in the system_prompt wrapper.
        assert "must STILL verify each required criterion" in rendered
        # The memory entry itself reinforces it.
        assert "Memory is the prompt for thoroughness" in rendered


class TestByteIdentityContractForAgentsWithoutMemory:
    """
    iter-1's byte-identity contract: agents whose prompts haven't been
    modified must render byte-identical output. MedicalDirectorAgent has
    no long_term_memory.md file; its rendered prompt must be unchanged
    from before iter-3 chg-2.
    """

    def test_medical_director_prompt_has_no_institutional_memory_section(self) -> None:
        rendered = load_agent_prompt("medical_director", "MedicalDirectorAgent")
        assert "## Institutional Memory" not in rendered

    def test_medical_director_prompt_has_no_nsclc_pembrolizumab_content(self) -> None:
        rendered = load_agent_prompt("medical_director", "MedicalDirectorAgent")
        assert "First-line pembrolizumab" not in rendered
        assert "PD-L1 TPS" not in rendered

    def test_medical_director_version_unchanged(self) -> None:
        """MD agent version must remain at its pre-iter-3 value."""
        rendered = load_agent_prompt("medical_director", "MedicalDirectorAgent")
        # v2.2 is the pre-iter-3 version per PROMPT_REGISTRY.
        assert "Prompt version: v2.2" in rendered


# =============================================================================
# iter-4 chg-1 — Second H2 memory entry: RA biologic after DMARD failure.
#
# Mirrors the iter-3 chg-2 NSCLC pembrolizumab test-class structure exactly,
# scoped to the new entry's criteria and anti-patterns. iter-1's byte-identity-
# for-MD-agent tests above are unchanged — they verify the MD prompt remains
# unmodified regardless of how many entries the DecisionSupportAgent has.
# =============================================================================


class TestRABiologicMemoryInjection:
    """Verify the RA biologic entry is rendered into the Decision agent's prompt."""

    def test_decision_support_prompt_contains_ra_biologic_entry(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert (
            "First-line biologic DMARD for seropositive RA after conventional DMARD failure"
            in rendered
        )

    def test_decision_support_prompt_version_is_at_least_v24(self) -> None:
        """
        v2.4+ signals the RA entry was active. The iter-4 chg-1 floor;
        the canonical current version is asserted by the iter-5 chg-4 test
        (test_decision_support_prompt_version_bumped_to_v26). This guards
        against accidental downgrade below v2.4.
        """
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        import re

        match = re.search(r"Prompt version: v2\.(\d+)", rendered)
        assert match is not None
        minor = int(match.group(1))
        assert minor >= 4, f"prompt version v2.{minor} predates RA entry (floor: v2.4)"

    def test_both_h2_entries_present_in_one_prompt(self) -> None:
        """The DecisionSupportAgent now ships with BOTH H2 entries — neither should hide the other."""
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "First-line pembrolizumab for metastatic NSCLC" in rendered  # iter-3 chg-2
        assert "First-line biologic DMARD for seropositive RA" in rendered  # iter-4 chg-1


class TestRABiologicCriterionPreservation:
    """
    Each required criterion must appear in the rendered prompt verbatim.
    Mirrors the NSCLC-entry test class — see its docstring for the H2
    contract rationale.
    """

    def test_memory_lists_seropositive_marker_criterion(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "seropositive markers" in rendered
        assert "RF" in rendered
        assert "anti-CCP" in rendered

    def test_memory_lists_disease_activity_score_criterion(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "moderate-to-severe" in rendered
        # Specific scoring instruments must be named so the agent doesn't
        # accept vague "active disease" language as sufficient.
        assert "DAS28 ≥ 3.2" in rendered
        assert "CDAI ≥ 10" in rendered
        assert "SDAI ≥ 11" in rendered

    def test_memory_lists_step_therapy_criterion(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "failure of **2 or more conventional DMARDs**" in rendered
        assert "methotrexate" in rendered
        # Adequate trial duration must be explicit.
        assert "≥ 3 months" in rendered

    def test_memory_lists_acr_recommended_biologic_criterion(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "ACR-recommended biologic list for RA" in rendered

    def test_memory_lists_infection_contraindication_criterion(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "No active uncontrolled infection" in rendered


class TestRABiologicAntiPatternsPreserved:
    """
    Each anti-pattern must appear verbatim with explicit IN_REVIEW routing.
    This is the criterion that the iter-3 H2 memory iteration learning
    (memory wording matters semantically, not just syntactically) made
    non-negotiable.
    """

    def test_anti_pattern_single_dmard_trial(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "Only **ONE** conventional DMARD tried" in rendered
        assert "**Status: IN_REVIEW.**" in rendered

    def test_anti_pattern_seronegative_ra(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "Seronegative RA" in rendered

    def test_anti_pattern_mild_disease_activity(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "Mild disease activity" in rendered
        # The thresholds must be named so the boundary is unambiguous.
        assert "DAS28 < 3.2" in rendered

    def test_anti_pattern_inadequate_trial_duration(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "Inadequate trial duration" in rendered

    def test_anti_pattern_active_infection(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "Active infection / pregnancy / live vaccine" in rendered

    def test_every_anti_pattern_routes_to_in_review_not_denied(self) -> None:
        """
        The (Not DENIED.) clarification must appear on every anti-pattern.
        This is what the iter-3 GC-021 regression taught — without explicit
        routing, the agent can over-apply multiple anti-patterns into DENIED.
        """
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        # Both H2 entries together should produce at least 11 "(Not DENIED.)"
        # occurrences: 5 from the NSCLC entry's anti-patterns, 6 from the RA
        # entry's anti-patterns. Use >= to allow future entries to add more.
        not_denied_count = rendered.count("(Not DENIED.)")
        assert not_denied_count >= 11, (
            f"expected at least 11 (Not DENIED.) clarifications across both "
            f"H2 entries; found {not_denied_count}"
        )


class TestRABiologicCostInteraction:
    """
    Unique to the RA entry: the memory's interaction with iter-3 chg-1's
    high_cost_check. GC-010 must continue to route to IN_REVIEW via cost
    even when the memory's clinical criteria are met. The memory must
    explicitly tell the agent NOT to override the cost escalation.
    """

    def test_memory_documents_high_cost_check_interaction(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        # The interaction with iter-3 chg-1's high_cost_check must be named.
        assert "high_cost_check" in rendered
        assert "iter-3 chg-1" in rendered
        # The memory must explicitly NOT claim to override the cost escalation.
        assert "memory does **not** override" in rendered

    def test_memory_prescribes_correct_rationale_when_cost_fires(self) -> None:
        """The memory should teach the agent the right phrasing on cost-fired cases."""
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "criteria met **but cost escalates per policy**" in rendered


# =============================================================================
# iter-5 chg-4 — Third H2 memory entry: dupilumab for severe eosinophilic asthma.
#
# Mirrors the iter-3 NSCLC and iter-4 RA test-class structure. Adds explicit
# coverage of the entry's documented non-override of the iter-5 chg-3
# pediatric_complex check (which fires on the canonical GC-012 case).
# =============================================================================


class TestAsthmaDupilumabMemoryInjection:
    def test_decision_support_prompt_contains_asthma_entry(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "Dupilumab for severe eosinophilic asthma" in rendered

    def test_decision_support_prompt_version_is_at_least_v25(self) -> None:
        """
        v2.5+ signals the asthma (3rd) entry was active. The iter-5 chg-4 floor;
        the canonical current version is asserted by the iter-6 chg-4 test
        (test_decision_support_prompt_version_bumped_to_v26). Guards against
        accidental downgrade below v2.5.
        """
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        import re

        match = re.search(r"Prompt version: v2\.(\d+)", rendered)
        assert match is not None
        minor = int(match.group(1))
        assert minor >= 5, f"prompt version v2.{minor} predates asthma entry (floor: v2.5)"

    def test_all_three_h2_entries_present(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "First-line pembrolizumab for metastatic NSCLC" in rendered
        assert "First-line biologic DMARD for seropositive RA" in rendered
        assert "Dupilumab for severe eosinophilic asthma" in rendered


class TestAsthmaCriterionPreservation:
    def test_memory_lists_severe_persistent_criterion(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "severe persistent asthma" in rendered.lower()
        assert "GINA Step" in rendered or "Step 4 or 5" in rendered

    def test_memory_lists_ics_laba_failure_criterion(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "Inadequate control on high-dose ICS / LABA" in rendered
        assert "≥ 3 months" in rendered

    def test_memory_lists_eosinophilic_phenotype_criterion(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "Eosinophilic phenotype" in rendered
        assert "eosinophil count ≥ 300/µL" in rendered
        assert "FeNO ≥ 25 ppb" in rendered

    def test_memory_lists_age_12_criterion(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "age ≥ 12 years" in rendered


class TestAsthmaAntiPatternsPreserved:
    def test_anti_pattern_mild_moderate_asthma(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "Mild or moderate asthma" in rendered

    def test_anti_pattern_insufficient_trial_duration(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "Insufficient ICS/LABA trial duration" in rendered

    def test_anti_pattern_non_eosinophilic_phenotype(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "Non-eosinophilic phenotype" in rendered

    def test_anti_pattern_age_below_12(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "Patient age < 12" in rendered

    def test_every_anti_pattern_routes_to_in_review_not_denied(self) -> None:
        """All H2 entries' anti-patterns say '(Not DENIED.)'. Four entries now:
        NSCLC (5) + RA (6) + asthma (5) + benefit-cap-deny over-denial guards (5)
        = 21. The deny entry INVERTS direction (deny -> IN_REVIEW) but uses the
        same marker, so the count only grows."""
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        not_denied_count = rendered.count("(Not DENIED.)")
        assert not_denied_count >= 21, (
            f"expected at least 21 (Not DENIED.) clarifications across four "
            f"H2 entries, found {not_denied_count}."
        )


class TestAsthmaPediatricComplexInteraction:
    """
    The asthma entry's unique-to-iter-5 documentation: explicit non-override
    of BOTH the iter-3 chg-1 high_cost_check AND the iter-5 chg-3
    pediatric_complex check. GC-012 is the canonical interaction case —
    severe pediatric asthma satisfies the clinical criteria AND the
    pediatric_complex check correctly escalates.
    """

    def test_memory_documents_pediatric_complex_check_interaction(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "pediatric_complex_check" in rendered
        assert "iter-5 chg-3" in rendered

    def test_memory_names_gc012_as_canonical_interaction(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "GC-012" in rendered

    def test_memory_explicitly_says_does_not_override(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "memory does **not** override" in rendered


# =============================================================================
# iter-6 chg-4 — Fourth H2 memory entry: FIRST deny-pattern (benefit-cap
# exhaustion). Re-anchored from GC-034 to GC-035 after the iter-6 baseline
# capture showed GC-034 (off-label oncology) is unconditionally intercepted by
# the pre-flight experimental_treatment check ("off-label" is itself an
# experimental keyword), so its disposition can never reach the
# DecisionSupportAgent where this memory lives. GC-035 (PT benefit-cap) reaches
# the agent, so the deny entry can actually act. The off-label/experimental
# pre-flight contradiction is recorded as an iter-7 finding.
#
# This entry INVERTS the approve-class format of the three entries above: its
# shortcut outcome is DENIED, and its anti-patterns flip DENIED -> IN_REVIEW.
# The over-denial guards are the safety property of the whole iteration.
# =============================================================================


class TestBenefitCapDenyMemoryInjection:
    def test_decision_support_prompt_contains_deny_entry(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "Outpatient benefit-cap exhaustion without a documented exception" in rendered

    def test_decision_support_prompt_version_bumped_to_v28(self) -> None:
        """The version must move whenever the injected memory does.

        It reaches audit logs and OTel spans, so a decision made under a changed
        memory that still reports the old version is unauditable after the fact.
        v2.7 (iter-10 chg-10): evidence-id citation. v2.8 (iter-20 chg-29): the
        second deny-class entry, coverage-criteria determination.
        """
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "Prompt version: v2.8" in rendered

    def test_all_five_h2_entries_present(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "First-line pembrolizumab for metastatic NSCLC" in rendered
        assert "First-line biologic DMARD for seropositive RA" in rendered
        assert "Dupilumab for severe eosinophilic asthma" in rendered
        assert "Outpatient benefit-cap exhaustion without a documented exception" in rendered
        # chg-29 — the second deny-class entry.
        assert "Coverage-criteria determination" in rendered


class TestBenefitCapDenyCriterionPreservation:
    """The 5 denial criteria must appear verbatim in the rendered prompt."""

    def test_lists_benefit_cap_exhausted(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "benefit cap is exhausted" in rendered.lower()

    def test_lists_contractual_not_medical_necessity(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "contractual benefit-design limit" in rendered.lower()
        assert "separate from medical necessity" in rendered.lower()

    def test_lists_no_exception_criterion(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "acute new injury" in rendered.lower()
        assert "post-surgical" in rendered.lower()
        assert "functional progress" in rendered.lower()

    def test_lists_maintenance_not_acute(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "maintenance/chronic, not an acute exacerbation" in rendered.lower()

    def test_lists_plan_policy_alignment(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "benefit document specifies the cap" in rendered.lower()


class TestBenefitCapDenyOverDenialGuards:
    """The over-denial guard: each anti-pattern FLIPS deny -> IN_REVIEW, and the
    governing 'never auto-deny on doubt' rule is present. This is the safety
    property of the entire iteration."""

    def test_any_exception_criterion_flips_to_in_review(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "Any documented exception criterion" in rendered
        assert "(Not DENIED.)" in rendered

    def test_acute_exacerbation_flips_to_in_review(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "acute exacerbation or new episode" in rendered.lower()

    def test_pending_appeal_flips_to_in_review(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "pending appeal" in rendered.lower()

    def test_incomplete_documentation_flips_to_in_review(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "ambiguous or incomplete visit-count" in rendered.lower()

    def test_governing_rule_never_auto_deny_on_doubt(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert (
            "Any uncertainty -> IN_REVIEW" in rendered or "Any uncertainty → IN_REVIEW" in rendered
        )
        assert "never auto-deny on doubt" in rendered.lower()
        assert "absence of evidence is not evidence of ineligibility" in rendered.lower()

    def test_valid_denial_requires_cited_basis_and_appeal_redirect(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "cite the specific benefit-cap basis" in rendered.lower()
        assert "appeal" in rendered.lower()
        assert "exception pathway" in rendered.lower()


class TestBenefitCapDenyInteraction:
    def test_names_gc035_anchor(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "GC-035" in rendered

    def test_does_not_sweep_medical_necessity_into_deny(self) -> None:
        # A not-medically-necessary denial is a clinical-criteria call, NOT this
        # contractual benefit-cap pattern.
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "not medically necessary" in rendered.lower()
        assert "is NOT this deny pattern" in rendered

    def test_does_not_override_pre_flight_checks(self) -> None:
        rendered = load_agent_prompt("decision_support", "DecisionSupportAgent")
        assert "does not override" in rendered.lower()
        assert "adult_complex" in rendered  # names the iter-6 chg-2 pre-flight
