"""Dataset integrity at the level of CASE OBJECTS, not case ids.

Why this file exists
---------------------
Every existing measurement of the clinical dataset counts unique **ids**:
`holdout.all_dataset_case_ids()` returns a `frozenset[str]`, and
`docs/DATASET_SUFFICIENCY.md` states the size as "105 cases (GC-001 through
GC-105 — verified by unique-ID count across `tests/clinical/`)". So does
`test_eval_holdout_guard.py::test_holdout_and_in_sample_partition_the_full_dataset`,
whose docstring promises "No case may be silently dropped" — and which passed
while three cases were.

Counting ids cannot see two different clinical cases sharing one id. On
2026-07-29 there were three: `adult_complexity_cases.py` claimed GC-101/102/103
on 2026-05-31, and `oncology_breadth_cases.py` re-used all three a week later
for unrelated oncology cases, because RUNBOOK_iter6 and the later PR both
independently recorded "the dataset is at GC-100" (see docs/AGENT_LESSONS.md
P-017). 108 case objects presented as 105.

That collision also concealed a second, larger fact. Once you count objects,
the evaluation census stops balancing: 39 cases run in the in-sample accuracy
gate, 32 in the held-out report, and the remainder are executed by nothing at
all — authored, reviewed, counted in the headline figure, never scored.

The two tests below are deliberately different in kind:

  * `test_no_two_cases_share_a_case_id` is a **gate**. Two different cases
    sharing an id is wrong under every reading: counters keyed by case_id
    become ambiguous, `held_out_cases()` would resolve one id to two objects,
    and the holdout partition guard is weaker than it appears.

  * `test_evaluation_reachability_census` is a **report**, not a gate — the
    same "warn before enforce" posture as the held-out accuracy report and the
    judge-stability harness. Wiring the unreached cases into a run is a scoped
    dataset decision with real cost (roughly doubling clinical-gate runtime and
    spend), not something a test should force by going red. Its only assertion
    is that the census balances, so the number cannot quietly drift while
    looking accounted-for.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

# `_ALL_CASE_LISTS` is private but is the single place that enumerates every
# case file. Re-listing the imports here would create exactly the drift this
# file exists to detect.
from tests.clinical.adult_complexity_cases import ADULT_COMPLEXITY_CASES
from tests.clinical.expansion_cases import EXPANSION_CASES
from tests.clinical.golden_cases import GOLDEN_CASES
from tests.clinical.holdout import _ALL_CASE_LISTS, held_out_cases
from tests.clinical.near_miss_cases import NEAR_MISS_CASES
from tests.clinical.pediatric_cases import PEDIATRIC_CASES

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ACCURACY_TEST = _REPO_ROOT / "tests" / "clinical" / "test_clinical_accuracy.py"

# Mirrors the concatenation passed to `_run_cases_through_pipeline` in
# test_clinical_accuracy.py::test_full_pipeline_meets_accuracy_threshold.
# `test_in_sample_composition_has_not_drifted` below keeps this honest.
_IN_SAMPLE_LIST_NAMES = (
    "GOLDEN_CASES",
    "NEAR_MISS_CASES",
    "PEDIATRIC_CASES",
    "EXPANSION_CASES",
    "ADULT_COMPLEXITY_CASES",
)
_IN_SAMPLE_RUN = (
    GOLDEN_CASES + NEAR_MISS_CASES + PEDIATRIC_CASES + EXPANSION_CASES + ADULT_COMPLEXITY_CASES
)


def _distinct_cases() -> list[Any]:
    """Every case OBJECT exactly once.

    De-duplicated by object identity, not by id string: `holdout.py` imports
    the same list objects it re-exports, so a naive sweep double-counts each
    case while genuinely distinct cases sharing an id collapse into one — the
    two errors cancel and the total looks plausible.
    """
    seen: set[int] = set()
    distinct: list[Any] = []
    for case_list in _ALL_CASE_LISTS:
        for case in case_list:
            if id(case) not in seen:
                seen.add(id(case))
                distinct.append(case)
    return distinct


def test_no_two_cases_share_a_case_id() -> None:
    """GATE: a case_id must identify exactly one clinical case.

    Two distinct cases behind one id make every case_id-keyed counter
    ambiguous — `report.failed_cases`, the constraint-violation lists, the
    fabrication census — and would make `held_out_cases()` resolve a single
    declared id to two objects, silently breaking its own wiring assertion.
    """
    by_id: dict[str, list[Any]] = defaultdict(list)
    for case in _distinct_cases():
        by_id[case.case_id].append(case)

    collisions = {cid: cases for cid, cases in by_id.items() if len(cases) > 1}
    detail = "case_id collisions — one id, several clinical cases:\n" + "\n".join(
        f"  {cid}:\n" + "\n".join(f"    - {c.title}" for c in cases)
        for cid, cases in sorted(collisions.items())
    )

    # Assert on the id list, not the dict: asserting on the mapping makes pytest
    # dump every colliding GoldenCase in full, burying the message under a
    # screenful of clinical_notes.
    assert sorted(collisions) == [], detail


def test_evaluation_reachability_census(capsys: Any) -> None:
    """REPORT, not a gate: which case objects does any evaluation actually run?

    Asserts only that the census balances — every case object is either in the
    in-sample gate, in the held-out report, or explicitly counted as unreached.
    The size of that last bucket is printed rather than bounded, so it cannot
    drift unnoticed while still looking accounted-for.
    """
    distinct = _distinct_cases()
    in_sample_ids = {id(c) for c in _IN_SAMPLE_RUN}
    held_out_ids = {id(c) for c in held_out_cases()}

    unreached = [c for c in distinct if id(c) not in in_sample_ids and id(c) not in held_out_ids]
    reached = len(distinct) - len(unreached)

    with capsys.disabled():
        print(
            f"\nDataset evaluation census:"
            f"\n  case objects on disk : {len(distinct)}"
            f"\n  unique case ids      : {len({c.case_id for c in distinct})}"
            f"\n  in-sample gate       : {len(_IN_SAMPLE_RUN)}"
            f"\n  held-out report      : {len(held_out_ids)}"
            f"\n  reached by some run  : {reached}"
            f"\n  NOT reached by any   : {len(unreached)}"
        )
        if unreached:
            print("  unreached case ids   : " + ", ".join(sorted(c.case_id for c in unreached)))

    assert reached + len(unreached) == len(distinct), (
        "census does not balance — a case object was counted in neither bucket, "
        "which means this report is no longer measuring what it claims"
    )


def test_in_sample_composition_has_not_drifted() -> None:
    """The census mirrors the accuracy gate's case lists; prove the mirror holds.

    `_IN_SAMPLE_RUN` restates the concatenation inside
    test_clinical_accuracy.py. If a list is added there and not here, the census
    silently over-reports the unreached bucket — a wrong number is worse than no
    number, since it invites a fix for a problem that has already been solved.
    """
    source = _ACCURACY_TEST.read_text()
    call_start = source.index("verdicts = await _run_cases_through_pipeline(")
    call_body = source[call_start : source.index("detector,", call_start)]

    referenced = {name for name in _IN_SAMPLE_LIST_NAMES if name in call_body}
    assert referenced == set(_IN_SAMPLE_LIST_NAMES), (
        "the accuracy gate no longer runs exactly the lists this census mirrors; "
        f"missing from the gate: {sorted(set(_IN_SAMPLE_LIST_NAMES) - referenced)}"
    )

    extra = [
        line.strip().lstrip("+ ").rstrip(",")
        for line in call_body.splitlines()
        if line.strip().lstrip("+ ").rstrip(",").endswith("_CASES")
    ]
    unexpected = {name for name in extra if name and name not in _IN_SAMPLE_LIST_NAMES}
    assert not unexpected, (
        f"the accuracy gate gained case lists this census does not mirror: {sorted(unexpected)} — "
        "add them to _IN_SAMPLE_LIST_NAMES and _IN_SAMPLE_RUN"
    )
