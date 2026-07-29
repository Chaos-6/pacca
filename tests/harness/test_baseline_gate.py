"""D6 deliverable validation suite: the `baseline` field gate.

Mechanises AGENT_LESSONS.md P-013 ("characterize before you change") as a schema-enforced
field rather than a habit. Per David's forward-only decision (D6 spec section 4): required
for `improvement`/`rollback` changes from iteration 18 onward; iterations 0-17 are
grandfathered and never need it.

Per P-010 ("a regression test that has only ever passed is an assumption, not a test"),
this suite's core test (`test_improvement_at_cutover_without_baseline_fails_with_useful_message`)
is the permanent, mutation-style proof that the gate actually rejects the case it exists to
reject -- not just a demonstration run once by hand.
"""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest

from pacca.harness.validate_manifest import main, validate_manifest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST_DIR = _REPO_ROOT / "harness" / "manifests"
_SCHEMA = _MANIFEST_DIR / "change_manifest.schema.json"

_VALID_BASELINE = {
    "claim": "The evaluator performs no Python keyword containment before this change.",
    "verified_by": "git show <sha>:tests/clinical/evaluator.py | grep -n must_include",
    "result": "Only two lines, both inside the judge-prompt template. No containment check.",
}

_CHANGE_TEMPLATE = {
    "id": "chg-99",
    "type": "improvement",
    "description": "Synthetic change used only to exercise the baseline schema gate.",
    "files": ["docs/HARNESS.md"],
    "failure_pattern": "Synthetic failure pattern for gate testing purposes only.",
    "root_cause": "Synthetic root cause for gate testing purposes only.",
    "predicted_fixes": [],
    "risk_cases": [],
    "constraint_level": "evaluation_harness",
    "why_this_component": "Synthetic justification for gate testing purposes only.",
}


def _manifest(iteration: int, change_overrides: dict) -> dict:
    change = copy.deepcopy(_CHANGE_TEMPLATE)
    change.update(change_overrides)
    return {
        "iteration": iteration,
        "iteration_tag": f"harness-iter-{iteration}",
        "iso_date": "2026-07-29",
        "author": "Test Fixture",
        "base_model": "claude-sonnet-4-5-20250929",
        "previous_iteration_tag": f"harness-iter-{iteration - 1}" if iteration > 0 else None,
        "changes": [change],
    }


@pytest.fixture
def schema_in_tmp(tmp_path: Path) -> Path:
    """A copy of the real schema in a tmp dir, with a manifest written alongside it."""
    shutil.copy(_SCHEMA, tmp_path / _SCHEMA.name)
    return tmp_path


def _write(tmp: Path, name: str, data: dict) -> Path:
    p = tmp / name
    p.write_text(json.dumps(data))
    return p


# ---------------------------------------------------------------------------
# The fail-then-pass proof (P-010): watch the gate reject the bad case, then
# accept the fixed one -- both against the same synthetic base.
# ---------------------------------------------------------------------------


def test_improvement_at_cutover_without_baseline_fails_with_useful_message(
    schema_in_tmp: Path,
) -> None:
    """RED: an 'improvement' change at iteration 18 with no baseline must fail, and the
    error must name the field, the iteration cutover, and P-013 -- not just recite
    "'baseline' is a required property"."""
    manifest = _manifest(iteration=18, change_overrides={"type": "improvement"})
    path = _write(schema_in_tmp, "iter-18.json", manifest)

    errors = validate_manifest(path)

    assert errors, "expected the gate to reject a cutover-iteration improvement with no baseline"
    joined = "\n".join(errors)
    assert "baseline" in joined
    assert "P-013" in joined
    assert "18" in joined  # names the cutover iteration
    assert "claim" in joined and "verified_by" in joined  # worked example present


def test_improvement_at_cutover_with_baseline_passes(schema_in_tmp: Path) -> None:
    """GREEN: the same shape, fixed by adding a conforming baseline object."""
    manifest = _manifest(
        iteration=18,
        change_overrides={"type": "improvement", "baseline": _VALID_BASELINE},
    )
    path = _write(schema_in_tmp, "iter-18.json", manifest)

    errors = validate_manifest(path)

    assert errors == [], "\n".join(errors)


def test_rollback_at_cutover_without_baseline_fails(schema_in_tmp: Path) -> None:
    manifest = _manifest(iteration=20, change_overrides={"type": "rollback"})
    path = _write(schema_in_tmp, "iter-20.json", manifest)

    errors = validate_manifest(path)

    assert any("baseline" in e for e in errors), errors


# ---------------------------------------------------------------------------
# Boundary and exemption cases.
# ---------------------------------------------------------------------------


def test_new_type_change_without_baseline_passes_even_past_cutover(schema_in_tmp: Path) -> None:
    """'new' asserts nothing about prior behaviour -- there is nothing to baseline."""
    manifest = _manifest(iteration=25, change_overrides={"type": "new"})
    path = _write(schema_in_tmp, "iter-25.json", manifest)

    errors = validate_manifest(path)

    assert errors == [], "\n".join(errors)


def test_instrumentation_type_change_without_baseline_passes_past_cutover(
    schema_in_tmp: Path,
) -> None:
    manifest = _manifest(iteration=30, change_overrides={"type": "instrumentation"})
    path = _write(schema_in_tmp, "iter-30.json", manifest)

    errors = validate_manifest(path)

    assert errors == [], "\n".join(errors)


def test_improvement_below_cutover_without_baseline_still_passes(schema_in_tmp: Path) -> None:
    """Forward-only (D6 section 4): iteration 17 and earlier are grandfathered, not
    backfilled -- an 'improvement' with no baseline at iteration 17 must still validate."""
    manifest = _manifest(iteration=17, change_overrides={"type": "improvement"})
    path = _write(schema_in_tmp, "iter-17.json", manifest)

    errors = validate_manifest(path)

    assert errors == [], "\n".join(errors)


def test_improvement_at_iteration_18_is_the_cutover_boundary(schema_in_tmp: Path) -> None:
    """Iteration 18 itself is in scope (>=), not just 19+."""
    manifest = _manifest(iteration=18, change_overrides={"type": "improvement"})
    path = _write(schema_in_tmp, "iter-18.json", manifest)

    errors = validate_manifest(path)

    assert errors != []


def test_baseline_object_rejects_extra_and_missing_subfields(schema_in_tmp: Path) -> None:
    bad_baseline = {"claim": "x" * 25, "verified_by": "grep foo", "extra_field": "not allowed"}
    manifest = _manifest(
        iteration=19, change_overrides={"type": "improvement", "baseline": bad_baseline}
    )
    path = _write(schema_in_tmp, "iter-19.json", manifest)

    errors = validate_manifest(path)

    assert errors, "extra property and missing 'result' should both be rejected"


# ---------------------------------------------------------------------------
# All shipped manifests, including the migrated iter-18, must still validate.
# ---------------------------------------------------------------------------


def test_all_shipped_manifests_still_valid() -> None:
    assert main(["--all", "--dir", str(_MANIFEST_DIR)]) == 0


def test_iter18_was_migrated_not_backfilled() -> None:
    """iter-18 (chg-25) must carry a real `baseline` object restored from the evidence
    entry the schema originally rejected it into -- not a freshly invented one, and not
    a duplicated evidence entry left behind."""
    data = json.loads((_MANIFEST_DIR / "iter-18.json").read_text())
    change = data["changes"][0]
    assert change["id"] == "chg-25"
    assert "baseline" in change
    baseline = change["baseline"]
    assert set(baseline) == {"claim", "verified_by", "result"}
    assert "fa71251" in baseline["verified_by"]
    # The duplicated evidence entry (prefixed "BASELINE CLAIM:") must be gone.
    for ev in change.get("evidence", []):
        assert "BASELINE CLAIM" not in ev.get("note", "")
