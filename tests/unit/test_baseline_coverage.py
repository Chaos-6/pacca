"""
What capture_baseline records must cover what the gate measures.

THE BUG THIS PREVENTS, WHICH SHIPPED AND WAS INVISIBLE
------------------------------------------------------
capture_baseline iterated GOLDEN_CASES (20). The clinical accuracy gate
evaluates IN_SAMPLE_CASES (42). So every baseline ever captured described less
than half the cases it would be compared against, and the 22 unbaselined cases
surfaced in a regression report as `new_cases` — a bucket that reads like a
routine note about recently-added cases, not like two thirds of the gate having
no regression protection at all.

Nothing failed. A baseline was produced, its schema was valid, its numbers were
real, and the comparison ran. Coverage gaps do not raise; they just quietly
narrow what a gate can see. That is why this is asserted rather than left to
review, and why the assertion is on the RELATIONSHIP between the two sets
rather than on either count — hard-coding 42 would need editing every time a
case is added, and an assertion people routinely edit is one they stop reading.
"""

from __future__ import annotations

from tests.clinical.golden_cases import GOLDEN_CASES
from tests.clinical.holdout import HELD_OUT_CASE_IDS, IN_SAMPLE_CASES, all_dataset_case_ids


def _in_sample_ids() -> set[str]:
    return {case.case_id for case in IN_SAMPLE_CASES}


class TestBaselineCoversTheGate:
    def test_capture_iterates_the_gate_s_own_case_list(self) -> None:
        """
        capture_baseline reads holdout.IN_SAMPLE_CASES directly, so this asserts
        the import is still the shared one rather than re-testing list algebra.
        """
        import ast
        import inspect
        import textwrap

        from tests.clinical import capture_baseline

        # Parse rather than grep the raw source: the docstring names both sets
        # while explaining the fix, so a substring search matches the prose and
        # reports the opposite of the truth.
        tree = ast.parse(textwrap.dedent(inspect.getsource(capture_baseline.run_golden_dataset)))
        iterated = {
            node.iter.id
            for node in ast.walk(tree)
            if isinstance(node, ast.For) and isinstance(node.iter, ast.Name)
        }
        assert "IN_SAMPLE_CASES" in iterated, (
            f"run_golden_dataset iterates {iterated or 'nothing by name'}, not the "
            "shared in-sample definition. A private case list here means captured "
            "baselines stop describing the cases the gate measures."
        )
        assert "GOLDEN_CASES" not in iterated, (
            "run_golden_dataset is back on GOLDEN_CASES (20) while the gate runs "
            "IN_SAMPLE_CASES (42) — the original coverage gap."
        )

    def test_in_sample_is_strictly_larger_than_golden(self) -> None:
        """The distinction is the whole point; if they converge, something moved."""
        assert len(IN_SAMPLE_CASES) > len(GOLDEN_CASES)
        assert {c.case_id for c in GOLDEN_CASES} < _in_sample_ids()

    def test_no_in_sample_case_is_also_held_out(self) -> None:
        """A case cannot be both baselined in-sample and reserved out-of-sample."""
        overlap = _in_sample_ids() & set(HELD_OUT_CASE_IDS)
        assert not overlap, f"cases both in-sample and held out: {sorted(overlap)}"

    def test_in_sample_ids_are_real_dataset_cases(self) -> None:
        unknown = _in_sample_ids() - all_dataset_case_ids()
        assert not unknown, f"in-sample ids absent from the dataset: {sorted(unknown)}"

    def test_the_uncovered_remainder_is_visible(self) -> None:
        """
        Not every authored case is on the gate. That is fine, and it should be
        a number someone can read rather than a surprise: this records that the
        gap exists so a future coverage claim is made against the real total.
        """
        total = len(all_dataset_case_ids())
        in_sample = len(_in_sample_ids())
        held_out = len(HELD_OUT_CASE_IDS)
        assert in_sample + held_out <= total
        assert in_sample < total, (
            "in-sample now equals the whole dataset; the holdout is gone, which "
            "would make every out-of-sample claim in docs/EVALUATION.md false."
        )
