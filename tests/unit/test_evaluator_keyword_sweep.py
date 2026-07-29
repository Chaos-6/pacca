"""
Dataset-wide sweep: does whole-word matching change behavior versus the
(buggy) substring rule it replaced, for any of the ~95 forbidden keywords
actually authored across the clinical case dataset? (chg-25, post-review)

Why this file exists
---------------------
The review that caught the substring bug asked one further question: GC-028,
GC-046, GC-048 are three cases where "age" as a forbidden keyword collides
with ordinary vocabulary ("coverage", "average") -- are there OTHER forbidden
keywords across the full ~105-case dataset with the same exposure that
weren't caught by manual inspection? This sweep answers empirically, against
the dataset's OWN clinical/guideline/rationale text (the realistic prior-auth
vocabulary the system actually reasons over), rather than a synthetic
wordlist.

Method: for every unique keyword ever used in any case's
`reasoning_must_not_include`, count substring hits vs whole-word hits across
the concatenated text of every case's `clinical_notes` +
`guidelines_context` + `clinical_rationale` + `judge_scoring_criteria`
(every field a real rationale might echo vocabulary from). Whole-word hits
can never exceed substring hits (every whole-word match is also a substring
match) -- that is asserted as a structural invariant. The keywords where the
two counts actually DIFFER are the ones with real false-positive exposure
under the old (buggy) rule; that exact set is pinned below so dataset drift
that reintroduces or newly creates this exposure is caught, not silently
absorbed.
"""

from __future__ import annotations

import re

from tests.clinical.adult_complexity_cases import ADULT_COMPLEXITY_CASES
from tests.clinical.ambiguous_completeness_cases import AMBIGUOUS_COMPLETENESS_CASES
from tests.clinical.cardiology_cases import CARDIOLOGY_CASES
from tests.clinical.denial_cases import DENIAL_CASES
from tests.clinical.depth_extension_cases import DEPTH_EXTENSION_CASES
from tests.clinical.endocrinology_cases import ENDOCRINOLOGY_CASES
from tests.clinical.expansion_cases import EXPANSION_CASES
from tests.clinical.geriatric_cases import GERIATRIC_CASES
from tests.clinical.golden_cases import GOLDEN_CASES, GoldenCase
from tests.clinical.hematology_cases import HEMATOLOGY_CASES
from tests.clinical.mental_health_cases import MENTAL_HEALTH_CASES
from tests.clinical.near_miss_cases import NEAR_MISS_CASES
from tests.clinical.neurology_cases import NEUROLOGY_CASES
from tests.clinical.ob_cases import OB_CASES
from tests.clinical.oncology_breadth_cases import ONCOLOGY_BREADTH_CASES
from tests.clinical.oncology_depth_cases import ONCOLOGY_DEPTH_CASES
from tests.clinical.pediatric_cases import PEDIATRIC_CASES
from tests.clinical.pulmonology_adult_cases import PULMONOLOGY_ADULT_CASES
from tests.clinical.transplant_cases import TRANSPLANT_CASES

# Every case list authored in the dataset as of chg-25 review (mirrors
# test_clinical_accuracy.py::ALL_SUPPLEMENTARY_LISTS plus GOLDEN_CASES).
ALL_CASES: list[GoldenCase] = (
    GOLDEN_CASES
    + NEAR_MISS_CASES
    + PEDIATRIC_CASES
    + EXPANSION_CASES
    + DENIAL_CASES
    + CARDIOLOGY_CASES
    + MENTAL_HEALTH_CASES
    + GERIATRIC_CASES
    + PULMONOLOGY_ADULT_CASES
    + AMBIGUOUS_COMPLETENESS_CASES
    + TRANSPLANT_CASES
    + NEUROLOGY_CASES
    + OB_CASES
    + HEMATOLOGY_CASES
    + ENDOCRINOLOGY_CASES
    + ONCOLOGY_DEPTH_CASES
    + ONCOLOGY_BREADTH_CASES
    + DEPTH_EXTENSION_CASES
    + ADULT_COMPLEXITY_CASES
)


def _corpus() -> str:
    parts: list[str] = []
    for c in ALL_CASES:
        parts.extend(
            [c.clinical_notes, c.guidelines_context, c.clinical_rationale, c.judge_scoring_criteria]
        )
    return " ".join(parts).lower()


def _substring_hits(keyword: str, text: str) -> int:
    return text.count(keyword.lower())


def _whole_word_hits(keyword: str, text: str) -> int:
    pattern = r"(?<!\w)" + re.escape(keyword.lower()) + r"(?!\w)"
    return len(re.findall(pattern, text))


def _all_forbidden_keywords() -> set[str]:
    keywords: set[str] = set()
    for c in ALL_CASES:
        keywords.update(c.reasoning_must_not_include)
    return keywords


def test_dataset_has_95_unique_forbidden_keywords() -> None:
    """
    Pins the sweep's denominator so a future reader knows exactly what
    universe "the other 95 forbidden keywords" (coordinator's phrasing)
    refers to. If this drifts, it means cases were added/removed/edited --
    expected over time, not itself a failure signal.
    """
    assert len(_all_forbidden_keywords()) == 95


def test_whole_word_hits_never_exceed_substring_hits() -> None:
    """
    Structural invariant: every whole-word match is also a substring match,
    so whole-word hit count can never be greater than substring hit count,
    for any keyword, over any text. This is what makes the fix strictly
    NARROWING (removing false positives), never adding new ones.
    """
    corpus = _corpus()
    for keyword in _all_forbidden_keywords():
        assert _whole_word_hits(keyword, corpus) <= _substring_hits(keyword, corpus), (
            f"{keyword!r}: whole-word hits exceeded substring hits -- "
            f"this should be mathematically impossible."
        )


def test_exact_set_of_keywords_with_false_positive_exposure_in_current_dataset() -> None:
    """
    The full sweep result at chg-25 review time: these are the forbidden
    keywords whose substring-vs-whole-word hit count actually differs across
    the dataset's own clinical/guideline/rationale text -- i.e. the keywords
    that had real false-positive exposure under the old (buggy) substring
    rule. "age" (GC-028/046/048) is the one found by manual review; this
    sweep additionally surfaces 7 more the manual review did not catch:
    "appropriate", "approved", "biologic", "complete documentation", "deny",
    "elective", "escalate".

    If this test fails because the SET changed: a new/edited case introduced
    (or removed) false-positive exposure for some keyword. Inspect which
    keyword changed and whether it represents a genuine new exposure (fix
    the case's keyword choice) or is incidental dataset growth (update this
    pinned set with the new sweep result).
    """
    corpus = _corpus()
    changed = {
        keyword
        for keyword in _all_forbidden_keywords()
        if _substring_hits(keyword, corpus) != _whole_word_hits(keyword, corpus)
    }
    assert changed == {
        "age",
        "appropriate",
        "approved",
        "biologic",
        "complete documentation",
        "deny",
        "elective",
        "escalate",
    }


def test_age_keyword_hit_counts_before_and_after() -> None:
    """Pins the headline number from the review: "age" drops from 152
    substring hits to 42 whole-word hits across the full case corpus --
    the other 110 were false positives from words like "coverage",
    "average", "stage", "agent"."""
    corpus = _corpus()
    assert _substring_hits("age", corpus) == 152
    assert _whole_word_hits("age", corpus) == 42
