"""
Held-out evaluation split for PACCA's clinical golden dataset.

Why this file exists
---------------------
PACCA's live clinical gate (`tests/clinical/test_clinical_accuracy.py ::
TestFullClinicalEvaluation`) reports "20/20 = 100%" against `GOLDEN_CASES`.
That headline number is partially in-sample: the DecisionAgent's long-term
memory (`src/pacca/agents/decision_support/long_term_memory.md`) explicitly
names golden cases it was authored against —

    long_term_memory.md:159   "GC-010 and any future high-cost biologic case"
    long_term_memory.md:231   "this is exactly the GC-012 case"
    long_term_memory.md:251   GC-035 ...

— and several other prompts/detectors/docs under `src/pacca/` name specific
case IDs too (see the contamination guard in
`tests/unit/test_eval_holdout_guard.py` for the live, executed list). A
system whose prompt was tuned by looking at a case's expected answer is not
being *evaluated* on that case when it is re-run — it is being asked to
recite. That is train-on-test contamination, and it makes the "100%"
headline optimistic.

The dataset is large enough (105 cases, `GC-001`..`GC-105`, no gaps) to
afford a genuine holdout: a subset that has never been named in a prompt,
memory entry, or detector rule, and is not part of the case lists the live
accuracy gate currently exercises (`GOLDEN_CASES`, `NEAR_MISS_CASES`,
`PEDIATRIC_CASES`, `EXPANSION_CASES`, `ADULT_COMPLEXITY_CASES` — see
`tests/clinical/test_clinical_accuracy.py::TestFullClinicalEvaluation`).
`HELD_OUT_CASE_IDS` is that subset.

Why declared, not derived
--------------------------
`HELD_OUT_CASE_IDS` is an explicit, hand-picked frozenset — NOT computed as
"every case_id currently unreferenced under src/pacca/". A derived holdout
would silently shrink the moment someone references a new case in a prompt:
add one `GC-NNN` mention to a docstring or memory entry, and that case
quietly falls out of the holdout with no signal to anyone. Declaring the
membership explicitly means contamination becomes a loud, named test
failure (see guard #1 in `test_eval_holdout_guard.py`) instead of an
invisible shrink in what "held out" means.

How membership was chosen (2026-07-27)
----------------------------------------
1. Computed the full 105-case dataset by importing every `tests/clinical/
   *_cases.py` list (see `all_dataset_case_ids()` below) — not hardcoded.
2. Excluded every id referenced anywhere under `src/pacca/**/*.py` or
   `src/pacca/**/*.md` (regex `GC-\\d{3}`) — this is the literal
   train-on-test contamination the guard test enforces.
3. Also excluded every id in the five case lists the live accuracy gate
   (`TestFullClinicalEvaluation.test_full_pipeline_meets_accuracy_threshold`)
   already runs today: `GOLDEN_CASES` (GC-001..020), `NEAR_MISS_CASES`
   (GC-021..022), `PEDIATRIC_CASES` (GC-023..025), `EXPANSION_CASES`
   (GC-026..033, GC-073..075), `ADULT_COMPLEXITY_CASES` (GC-101..103). These
   cases are not literally named in a prompt, but they are the cases the
   system's aggregate accuracy is already measured and iterated against
   every run — calling one of them "held out" while it's also part of the
   routinely-graded set would overstate what the holdout buys. This is a
   judgment call beyond the letter of the contamination guard, made to keep
   the holdout honest in spirit, not just in the regex.
4. From the remaining 63-case pool, selected 32 ids (comfortably above the
   n=20 power-floor target — see `docs/STATISTICAL_POWER.md`'s "20 pp drop"
   row), stratified across every `expected_branch` / `ExpectedOutcome`
   combination present in that pool (branch labels below are the
   `EscalationBranch` member names; the enum's actual *value* for the first
   one is `"branch_1_high_confidence"`, not "auto_approve" — the member
   name is what's stratified on here, so it's spelled out to avoid
   confusion with the string value used elsewhere):
     - EscalationBranch.BRANCH_1_AUTO_APPROVE ("branch_1_high_confidence")
       / ExpectedOutcome.AUTO_APPROVED: 19 cases
     - EscalationBranch.BRANCH_2_MEDICAL_DIRECTOR ("branch_2_medical_director")
       / ExpectedOutcome.IN_REVIEW: 7 cases
     - EscalationBranch.BRANCH_3_LOW_CONFIDENCE ("branch_3_low_confidence")
       / ExpectedOutcome.IN_REVIEW: 4 cases
     - EscalationBranch.NONE ("no_escalation_expected")
       / ExpectedOutcome.DENIED: 2 cases (both available DENIED cases in the pool)
   The remaining pool had no BRANCH_4/5/6/7 or PRE_FLIGHT_ESCALATE cases
   outside the excluded set (those only exist inside the already-excluded
   golden 20), so they are not represented in the holdout.
   Selections were also spread across 13 distinct specialty case files
   (cardiology, mental health, geriatric, pulmonology, transplant,
   neurology, OB, endocrinology, hematology, oncology depth/breadth,
   depth-extension, ambiguous-completeness) rather than clustering in one.

Structural gap — branches 4-7 are unmeasurable out-of-sample
---------------------------------------------------------------
`BRANCH_4_EXPERIMENTAL`, `BRANCH_5_RARE`, `BRANCH_6_CONFLICTING`,
`BRANCH_7_PRIOR_DENIAL`, and `ExpectedOutcome.PRE_FLIGHT_ESCALATE` exist
ONLY inside the contaminated golden-20: GC-006 and GC-009 (BRANCH_4),
GC-007 (BRANCH_5), GC-011 (BRANCH_6), GC-008 (BRANCH_7) — five cases total,
all pre-flight-escalated. Every case that exercises PACCA's deterministic
pre-flight safety layer is inside the already-excluded gate, so no
re-slicing of the existing 105-case dataset can produce an out-of-sample
measurement for it — only authoring NEW pre-flight cases in those four
categories can. This holdout can measure out-of-sample performance for
branch-1 auto-approvals and branch-2/3 escalations plus deny-class
outcomes; it proves nothing out-of-sample about pre-flight safety. See
`docs/EVALUATION.md` for the full accounting; authoring the missing
cases is named there as follow-up work, not done in this change.

No held-out case may ever be referenced in a prompt, memory entry, detector
rule, or added to a case list the live accuracy gate runs — doing so is
exactly the contamination this file exists to prevent, and the guard test
will fail loudly (not silently) the moment it happens.
"""

from __future__ import annotations

from .adult_complexity_cases import ADULT_COMPLEXITY_CASES
from .ambiguous_completeness_cases import AMBIGUOUS_COMPLETENESS_CASES
from .cardiology_cases import CARDIOLOGY_CASES
from .denial_cases import DENIAL_CASES
from .depth_extension_cases import DEPTH_EXTENSION_CASES
from .endocrinology_cases import ENDOCRINOLOGY_CASES
from .expansion_cases import EXPANSION_CASES
from .geriatric_cases import GERIATRIC_CASES
from .golden_cases import GOLDEN_CASES, GoldenCase
from .hematology_cases import HEMATOLOGY_CASES
from .mental_health_cases import MENTAL_HEALTH_CASES
from .near_miss_cases import NEAR_MISS_CASES
from .neurology_cases import NEUROLOGY_CASES
from .ob_cases import OB_CASES
from .oncology_breadth_cases import ONCOLOGY_BREADTH_CASES
from .oncology_depth_cases import ONCOLOGY_DEPTH_CASES
from .pediatric_cases import PEDIATRIC_CASES
from .pulmonology_adult_cases import PULMONOLOGY_ADULT_CASES
from .transplant_cases import TRANSPLANT_CASES

# Every case list across tests/clinical/*_cases.py. Add a new entry here
# whenever a new case file lands — `all_dataset_case_ids()` and the
# partition-completeness guard test both depend on this being exhaustive.
# ─────────────────────────────────────────────────────────────────────────
# The in-sample evaluation set — ONE definition, three consumers.
#
# This is the concatenation the clinical accuracy gate runs
# (test_clinical_accuracy.py::test_full_pipeline_meets_accuracy_threshold).
# It lives here because three places need it and a hand-copy in each is three
# things that drift: the gate itself, the coverage census in
# test_dataset_integrity.py, and capture_baseline.py, whose captured scores are
# meaningless unless they describe exactly the cases the gate measures.
#
# It is NOT every authored case. 108 cases exist across _ALL_CASE_LISTS; 32 are
# held out; this is the 42 the gate evaluates. The gap is deliberate for the
# holdout and accidental for the rest — the remaining authored cases are simply
# not on the gate's list, which is worth knowing when reading a coverage number.
#
# test_dataset_integrity.py::test_in_sample_composition_has_not_drifted parses
# the real concatenation out of the accuracy test and compares it to this, so
# adding a list there without adding it here fails rather than silently
# under-reporting.
IN_SAMPLE_CASES: list[GoldenCase] = (
    GOLDEN_CASES
    + NEAR_MISS_CASES
    + PEDIATRIC_CASES
    + EXPANSION_CASES
    + ADULT_COMPLEXITY_CASES
    + DENIAL_CASES
)


_ALL_CASE_LISTS: tuple[list[GoldenCase], ...] = (
    GOLDEN_CASES,
    NEAR_MISS_CASES,
    PEDIATRIC_CASES,
    EXPANSION_CASES,
    DENIAL_CASES,
    CARDIOLOGY_CASES,
    MENTAL_HEALTH_CASES,
    GERIATRIC_CASES,
    PULMONOLOGY_ADULT_CASES,
    AMBIGUOUS_COMPLETENESS_CASES,
    TRANSPLANT_CASES,
    NEUROLOGY_CASES,
    OB_CASES,
    HEMATOLOGY_CASES,
    ENDOCRINOLOGY_CASES,
    ONCOLOGY_DEPTH_CASES,
    ONCOLOGY_BREADTH_CASES,
    DEPTH_EXTENSION_CASES,
    ADULT_COMPLEXITY_CASES,
)


def all_dataset_case_ids() -> frozenset[str]:
    """Every case_id across the full clinical dataset (108 as of 2026-07-29).

    Computed by importing the case modules directly rather than hardcoding
    a count or an id range, so it tracks the dataset as it grows.

    It tracks IDS, not cases, and that distinction is load-bearing. Being a
    frozenset, this collapses two different cases sharing one id into a single
    entry — which is exactly how three collisions (GC-101/102/103) stayed
    invisible from 2026-06-06 to 2026-07-29 while every count in the repo,
    including this one, reported the dataset as "105". The figure above is now
    108 because chg-27 renumbered the duplicates, not because cases were added.

    For anything that must reason about case OBJECTS — how many exist, which
    ones an evaluation actually runs — use
    tests/unit/test_dataset_integrity.py, which de-duplicates by object
    identity. A count taken from this function cannot answer those questions.
    """
    return frozenset(case.case_id for cases in _ALL_CASE_LISTS for case in cases)


# ─────────────────────────────────────────────────────────────────────────
# The declared holdout. See the module docstring for the selection method.
# ─────────────────────────────────────────────────────────────────────────
HELD_OUT_CASE_IDS: frozenset[str] = frozenset(
    {
        # EscalationBranch.BRANCH_1_AUTO_APPROVE (value: "branch_1_high_confidence")
        # / ExpectedOutcome.AUTO_APPROVED (19)
        "GC-037",
        "GC-038",
        "GC-041",
        "GC-044",
        "GC-046",
        "GC-048",
        "GC-050",
        "GC-052",
        "GC-060",
        "GC-063",
        "GC-064",
        "GC-067",
        "GC-068",
        "GC-071",
        "GC-076",
        "GC-078",
        "GC-079",
        "GC-083",
        "GC-089",
        # EscalationBranch.BRANCH_2_MEDICAL_DIRECTOR / ExpectedOutcome.IN_REVIEW (7)
        "GC-042",
        "GC-047",
        "GC-061",
        "GC-065",
        "GC-070",
        "GC-090",
        "GC-096",
        # EscalationBranch.BRANCH_3_LOW_CONFIDENCE / ExpectedOutcome.IN_REVIEW (4)
        "GC-055",
        "GC-057",
        "GC-059",
        "GC-077",
        # EscalationBranch.NONE / ExpectedOutcome.DENIED (2)
        "GC-039",
        "GC-105",
    }
)

IN_SAMPLE_CASE_IDS: frozenset[str] = all_dataset_case_ids() - HELD_OUT_CASE_IDS


def held_out_cases() -> list[GoldenCase]:
    """
    Resolve HELD_OUT_CASE_IDS to their actual GoldenCase objects, sorted by
    case number for stable, deterministic ordering (e.g. in evaluation logs).

    Used by the live held-out accuracy test
    (`tests/clinical/test_clinical_accuracy.py::TestFullClinicalEvaluation::
    test_held_out_accuracy_report`) to run the real pipeline over the
    declared holdout.
    """
    lookup = {case.case_id: case for cases in _ALL_CASE_LISTS for case in cases}
    return [
        lookup[case_id] for case_id in sorted(HELD_OUT_CASE_IDS, key=lambda c: int(c.split("-")[1]))
    ]
