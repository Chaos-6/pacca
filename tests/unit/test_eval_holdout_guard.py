"""
Guard tests for the clinical evaluation holdout split (`tests/clinical/holdout.py`).

Why this file exists
---------------------
PACCA's live clinical gate reports "20/20 = 100%" against `GOLDEN_CASES`,
but the DecisionAgent's prompt and long-term memory were authored while
looking at specific golden cases (GC-010, GC-012, GC-035 are named
verbatim in `src/pacca/agents/decision_support/long_term_memory.md`).
Evaluating a system on a case its prompt was tuned against is not a fair
test — it's train-on-test. `tests/clinical/holdout.py` declares a 32-case
holdout that has never been referenced anywhere under `src/pacca/` and is
not part of the case lists the live accuracy gate runs today.

These tests are the enforcement mechanism. Without them, the holdout is
just a comment — nothing stops a future prompt edit from silently naming a
held-out case, which would quietly re-contaminate the split with no signal
to anyone. Each test below turns one specific way that could go wrong into
a loud, named CI failure.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.clinical.holdout import (
    HELD_OUT_CASE_IDS,
    IN_SAMPLE_CASE_IDS,
    all_dataset_case_ids,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_PACCA = _REPO_ROOT / "src" / "pacca"

# Matches PACCA's case-id convention exactly (see
# src/pacca/harness/validate_manifest.py's own "GC-\d{3}" comment).
_CASE_ID_RE = re.compile(r"GC-\d{3}")


def _scan_src_pacca_for_case_ids() -> dict[str, set[str]]:
    """Map each case_id found under src/pacca/ to the file:line(s) it appears in.

    Scans every .py and .md file under src/pacca/ — the same surfaces a
    prompt, memory entry, or detector rule could reference a case id from
    (system prompts, long_term_memory.md, code comments, docstrings).
    """
    hits: dict[str, set[str]] = {}
    for pattern in ("*.py", "*.md"):
        for path in _SRC_PACCA.rglob(pattern):
            text = path.read_text(encoding="utf-8", errors="replace")
            rel = path.relative_to(_REPO_ROOT)
            for lineno, line in enumerate(text.splitlines(), start=1):
                for match in _CASE_ID_RE.finditer(line):
                    hits.setdefault(match.group(), set()).add(f"{rel}:{lineno}")
    return hits


def test_no_held_out_case_referenced_under_src_pacca() -> None:
    """
    Contamination guard: no held-out case id may appear anywhere under
    src/pacca/ (prompts, memory, comments, docstrings).

    This is the mechanism that would have caught the original finding
    (GC-010, GC-012, GC-035 named in long_term_memory.md) if it had run
    before those entries were written — and will catch any future prompt
    edit that names a held-out case.
    """
    referenced = _scan_src_pacca_for_case_ids()
    contaminated = {
        case_id: sorted(locations)
        for case_id, locations in referenced.items()
        if case_id in HELD_OUT_CASE_IDS
    }
    assert not contaminated, (
        "Held-out case id(s) referenced under src/pacca/ — this is exactly "
        "the train-on-test contamination the holdout exists to prevent. "
        "Remove the reference, or if the case must be named, move it out of "
        f"HELD_OUT_CASE_IDS in tests/clinical/holdout.py. Offenders: {contaminated}"
    )


def test_holdout_and_in_sample_partition_the_full_dataset() -> None:
    """HELD_OUT ∪ IN_SAMPLE must equal the full dataset, with no overlap.

    No case may be silently dropped (missing from both sets) or
    double-counted (present in both) as new case files are added.
    """
    full_dataset = all_dataset_case_ids()

    overlap = HELD_OUT_CASE_IDS & IN_SAMPLE_CASE_IDS
    assert not overlap, f"Case ids in both HELD_OUT and IN_SAMPLE: {sorted(overlap)}"

    union = HELD_OUT_CASE_IDS | IN_SAMPLE_CASE_IDS
    missing_from_union = full_dataset - union
    extra_in_union = union - full_dataset
    assert not missing_from_union, (
        f"Dataset case ids missing from both HELD_OUT and IN_SAMPLE: {sorted(missing_from_union)}"
    )
    assert not extra_in_union, (
        f"HELD_OUT/IN_SAMPLE contain ids not in the dataset: {sorted(extra_in_union)}"
    )


def test_holdout_meets_statistical_power_floor() -> None:
    """
    len(HELD_OUT_CASE_IDS) must be >= 20.

    Per docs/STATISTICAL_POWER.md's sample-size table, n ~= 13 is the
    binomial-CI requirement to detect a 20-percentage-point aggregate
    accuracy drop at alpha=0.05 / 80% power (baseline 95% -> regressed
    75%). That table's own "operational target" for this row is 20 (a
    safety margin over the raw n=13), which is the floor this test
    enforces on the held-out split specifically.
    """
    assert len(HELD_OUT_CASE_IDS) >= 20, (
        f"Held-out set has only {len(HELD_OUT_CASE_IDS)} cases; need >= 20 "
        "to meet the power floor for detecting a 20pp aggregate accuracy "
        "drop (see docs/STATISTICAL_POWER.md, 'operational target' column)."
    )


def test_every_held_out_id_exists_in_the_dataset() -> None:
    """Every declared held-out id must resolve to a real case (catches typos)."""
    full_dataset = all_dataset_case_ids()
    unknown = HELD_OUT_CASE_IDS - full_dataset
    assert not unknown, (
        f"HELD_OUT_CASE_IDS references id(s) not present in any case list: "
        f"{sorted(unknown)}. Check for a typo in tests/clinical/holdout.py."
    )


def test_known_contaminated_cases_are_in_sample_not_held_out() -> None:
    """
    Pins the actual finding that motivated this work: GC-010 and GC-012 are
    named verbatim in src/pacca/agents/decision_support/long_term_memory.md
    (lines 159 and 231/237) and therefore can never be part of the holdout.
    They must remain in IN_SAMPLE_CASE_IDS.
    """
    assert "GC-010" in IN_SAMPLE_CASE_IDS, (
        "GC-010 is referenced in long_term_memory.md and must be in-sample"
    )
    assert "GC-012" in IN_SAMPLE_CASE_IDS, (
        "GC-012 is referenced in long_term_memory.md and must be in-sample"
    )
    assert "GC-010" not in HELD_OUT_CASE_IDS
    assert "GC-012" not in HELD_OUT_CASE_IDS
