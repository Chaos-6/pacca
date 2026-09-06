"""
The variance measurement must stay manual-only, and stay out of ci.yml.

WHY THIS IS WORTH A TEST
------------------------
The measurement is the most expensive thing in the repo: it runs the whole
golden dataset N times through the pipeline AND re-scores each decision N times
through the judge. At the defaults that is several hundred live calls.

It first shipped as a job inside ci.yml, gated on `workflow_dispatch`. That
looked correct and was not, because ci.yml's dispatch ALSO starts the clinical
gate and the held-out report -- both deliberately manual-runnable. So a dispatch
aimed at the measurement paid for three live jobs, and the surprise arrived as
an invoice rather than a failure. Splitting it into its own file made the
dispatch surgical.

Both halves of that fix are load-bearing and neither is self-evident from
reading either file alone, so both are pinned here:

  1. variance.yml triggers on workflow_dispatch and NOTHING else. A `push` or
     `pull_request` trigger added here would bill this on every commit.
  2. ci.yml does not carry the variance job, or the split is undone.

A cost regression has no failing symptom -- CI stays green while the bill grows
-- so it cannot be caught the way a broken test is. It has to be asserted.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_VARIANCE_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "variance.yml"
_CI_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "ci.yml"


def _triggers(path: Path) -> dict:
    """The `on:` block. PyYAML parses a bare `on` key as the boolean True."""
    doc = yaml.safe_load(path.read_text())
    return doc[True] if True in doc else doc["on"]


class TestVarianceWorkflowIsManualOnly:
    def test_file_exists(self) -> None:
        assert _VARIANCE_WORKFLOW.is_file(), (
            "variance.yml is gone — if the job moved back into ci.yml, a dispatch "
            "for it also starts the clinical gate and the held-out report."
        )

    def test_workflow_dispatch_is_the_only_trigger(self) -> None:
        triggers = _triggers(_VARIANCE_WORKFLOW)
        assert set(triggers) == {"workflow_dispatch"}, (
            f"variance.yml triggers on {sorted(triggers)}. It must be manual-only: "
            "this workflow runs the golden dataset N times through the pipeline and "
            "re-scores every decision N times through the judge, so any automatic "
            "trigger bills hundreds of live calls per firing."
        )

    @pytest.mark.parametrize("knob", ["judge_runs", "rollouts"])
    def test_both_sample_counts_are_settable(self, knob: str) -> None:
        """Cost scales with these, so they must be adjustable without a commit."""
        inputs = _triggers(_VARIANCE_WORKFLOW)["workflow_dispatch"]["inputs"]
        assert knob in inputs

    def test_reports_rather_than_gates(self) -> None:
        """
        Both measurement steps carry continue-on-error. A variance measurement
        that fails the build would read as a code regression, which it never is.
        """
        jobs = yaml.safe_load(_VARIANCE_WORKFLOW.read_text())["jobs"]
        steps = jobs["variance-report"]["steps"]
        measuring = [s for s in steps if "measure_judge_noise" in str(s.get("run", ""))]
        measuring += [s for s in steps if "capture_baseline" in str(s.get("run", ""))]
        assert len(measuring) == 2, "expected exactly the two measurement steps"
        for step in measuring:
            assert step.get("continue-on-error") is True, (
                f"step {step.get('name')!r} would fail the build; this workflow reports"
            )


class TestCiDoesNotCarryTheVarianceJob:
    def test_ci_has_no_variance_job(self) -> None:
        jobs = yaml.safe_load(_CI_WORKFLOW.read_text())["jobs"]
        assert "variance-report" not in jobs, (
            "the variance job is back in ci.yml — a manual dispatch there also "
            "starts the clinical gate and the held-out report, which is the "
            "three-jobs-for-one-request problem the split fixed."
        )

    def test_ci_manual_run_still_reaches_its_own_live_jobs(self) -> None:
        """
        The split must not have cost ci.yml anything. Running the clinical gate
        or the held-out report on demand is deliberate existing behaviour.
        """
        doc = yaml.safe_load(_CI_WORKFLOW.read_text())
        assert "workflow_dispatch" in _triggers(_CI_WORKFLOW)
        holdout_condition = doc["jobs"]["holdout-report"]["if"]
        assert "workflow_dispatch" in holdout_condition
