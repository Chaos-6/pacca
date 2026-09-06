"""
Tests for the live judge-noise runner — no API key, no network.

The runner's *value* is a number only a live judge can produce, so what is
testable here is everything around that call: that the extracted pipeline
helper still takes the pre-flight short-circuit without touching an agent,
that the summary arithmetic is right, and that the CLI refuses a run that
cannot observe disagreement.

The last one matters more than it looks. `--runs 1` would complete happily,
report a 0% disagreement rate, and be entirely meaningless -- one sample
cannot disagree with itself. A measurement that reports "no noise" because it
structurally could not detect noise is worse than no measurement, because
someone will cite it.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any

import pytest

from tests.clinical.capture_baseline import run_pipeline_for_case
from tests.clinical.golden_cases import GOLDEN_CASES
from tests.clinical.measure_judge_noise import render


class _EscalatingDetector:
    """Stands in for ClinicalRiskDetector when pre-flight fires."""

    class _Flags:
        should_pre_escalate = True
        reasons: list[Any] = []
        details: dict[str, Any] = {}

    def evaluate(self, **_: Any) -> _Flags:
        return self._Flags()


class _ExplodingAgent:
    """Any call is a failure — proves the agent is never reached."""

    async def run(self, _ctx: Any) -> Any:  # pragma: no cover - must not run
        raise AssertionError("the agent must not be called after a pre-flight escalation")


class TestPipelineHelperPreservesPreFlight:
    """
    The helper was extracted from run_golden_dataset so two callers share one
    copy. If the extraction dropped the short-circuit, captured baselines would
    silently start including agent output for cases that must never reach an
    agent — and every downstream number would be measuring the wrong pipeline.
    """

    @pytest.mark.asyncio
    async def test_preflight_escalation_never_calls_the_agent(self) -> None:
        status, rationale, confidence = await run_pipeline_for_case(
            GOLDEN_CASES[0], _EscalatingDetector(), _ExplodingAgent()
        )
        assert status == "IN_REVIEW"
        assert confidence == 0.0
        assert "Pre-flight escalation triggered" in rationale

    @pytest.mark.asyncio
    async def test_agent_failure_is_reported_not_raised(self) -> None:
        """A single failing case must not abort a 20-case measurement run."""

        class _Detector:
            class _Flags:
                should_pre_escalate = False

            def evaluate(self, **_: Any) -> _Flags:
                return self._Flags()

        class _FailingAgent:
            async def run(self, _ctx: Any) -> Any:
                raise RuntimeError("upstream boom")

        status, rationale, confidence = await run_pipeline_for_case(
            GOLDEN_CASES[0], _Detector(), _FailingAgent()
        )
        assert status == "ERROR"
        assert "upstream boom" in rationale
        assert confidence == 0.0


class TestRender:
    def test_reports_the_numbers_it_was_given(self) -> None:
        out = render(
            {
                "cases_measured": 20,
                "runs_per_case": 5,
                "disagreement_rate": 0.35,
                "band_crossing_rate": 0.10,
                "fabrication_disagreement_rate": 0.05,
                "max_judge_only_spread": 3,
            }
        )
        assert "20 cases x 5 runs" in out
        assert "35.0%" in out
        assert "10.0%" in out
        assert "3 points" in out

    def test_names_the_threshold_the_measurement_bears_on(self) -> None:
        """The output has to say what it is evidence *for*, or it is a number
        with no argument attached."""
        out = render(
            {
                "cases_measured": 1,
                "runs_per_case": 2,
                "disagreement_rate": 0.0,
                "band_crossing_rate": 0.0,
                "fabrication_disagreement_rate": 0.0,
                "max_judge_only_spread": 0,
            }
        )
        assert "REGRESSION_DROP_THRESHOLD" in out
        assert "capture_baseline.py --rollouts" in out


class TestCliRefusesAMeaninglessRun:
    @pytest.mark.parametrize("runs", ["1", "0", "-3"])
    def test_runs_below_two_is_rejected(self, runs: str) -> None:
        proc = subprocess.run(
            [sys.executable, "-m", "tests.clinical.measure_judge_noise", "--runs", runs],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert proc.returncode != 0
        assert "must be >= 2" in (proc.stderr + proc.stdout)
