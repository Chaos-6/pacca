"""
Per-case regression gate for the PACCA clinical golden dataset — iter-2.

WHY THIS EXISTS (read this first)
---------------------------------
The existing CI gate in `evaluator.py` works like this:
  - A case "passes" if the judge scores it >= 3 (MINIMUM_PASSING_SCORE).
  - The whole run passes if >= 80% of cases pass (MINIMUM_ACCEPTABLE_ACCURACY).

That gate is AGGREGATE and ABSOLUTE. It has one blind spot that matters a great
deal for the v2.3 harness cycle: a case can slide from a 5 ("excellent, complete
reasoning") down to a 3 ("acceptable, vague reasoning") and STILL count as a pass,
because 3 >= 3. The aggregate stays green and nobody is alerted.

That 5 -> 3 slide — correct decision, hollowed-out reasoning — is exactly the
failure mode an over-aggressive institutional-memory entry (Phase H2) is most
likely to produce. The current net cannot see it.

This module closes that blind spot. It records each case's score at a known-good
baseline (e.g. harness-iter-1) and then, on every later run, flags any case whose
score DROPS relative to its own baseline — even when the aggregate is still above
80%. It is a *relative* gate that complements the existing *absolute* gate; it
does not replace it.

DESIGN NOTE — why stdlib only
-----------------------------
The regression gate reasons about integer scores keyed by case_id. It does not
need the Anthropic SDK, the agents, or anything else. Keeping it dependency-free
means the gate can run in any CI job (even one without an API key) and is trivial
to unit-test with synthetic scores. It accepts plain dicts {case_id: score} and
also offers an adapter for the `JudgeVerdict` objects produced by `evaluator.py`,
using duck typing so it never has to import that module.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterable

# A drop of this many points (or more) versus baseline counts as a regression.
# 1 is intentionally strict: any downward movement on a previously-good case is
# worth a human look, because that is the signal the absolute gate throws away.
REGRESSION_DROP_THRESHOLD = 1


# ---------------------------------------------------------------------------
# Score extraction (adapter) — works with evaluator.py's JudgeVerdict without
# importing it. Anything with `.case_id` (str) and `.score` (int) qualifies.
# ---------------------------------------------------------------------------
class _ScoredVerdict(Protocol):
    case_id: str
    score: int


def scores_from_verdicts(verdicts: Iterable[_ScoredVerdict]) -> dict[str, int]:
    """
    Turn a list of JudgeVerdict-like objects into a {case_id: score} mapping.

    Duck-typed on purpose: any object exposing `.case_id` and `.score` works,
    so this module never has to import evaluator.py (which would pull in the
    Anthropic SDK).
    """
    return {v.case_id: int(v.score) for v in verdicts}


# ---------------------------------------------------------------------------
# Baseline scoreboard — persist and load per-case scores
# ---------------------------------------------------------------------------
def save_baseline(
    scores: dict[str, int],
    path: str | Path,
    *,
    iteration_tag: str,
    distributions: dict[str, list[int]] | None = None,
) -> Path:
    """
    Write a baseline scoreboard to disk as JSON.

    Args:
        scores:        {case_id: score} captured from a known-good run.
        path:          Where to write the scoreboard (e.g.
                       tests/clinical/baselines/iter-1-baseline.json).
        iteration_tag: The harness tag this baseline represents
                       (e.g. "harness-iter-1"). Stored for provenance so a
                       future reader knows which commit the numbers describe.
        distributions: iter-3 chg-3 — optional per-case score distributions
                       from k=N rollouts (when capture_baseline was run with
                       --rollouts > 1). When present, `scores` should contain
                       the median (or chosen summary statistic) of each
                       distribution. Stored alongside `scores` so future
                       readers can see the underlying variance.

    Returns:
        The Path written.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "iteration_tag": iteration_tag,
        "scores": {cid: int(s) for cid, s in sorted(scores.items())},
    }
    if distributions is not None:
        payload["distributions"] = {
            cid: [int(s) for s in scores_list] for cid, scores_list in sorted(distributions.items())
        }
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


def load_baseline(path: str | Path) -> dict[str, int]:
    """
    Load a baseline scoreboard's {case_id: score} mapping from disk.

    Raises FileNotFoundError if the baseline does not exist — that is a
    deliberate, loud failure: you cannot run a regression check without a
    baseline to compare against, and silently passing would defeat the point.
    """
    path = Path(path)
    data = json.loads(path.read_text())
    return {cid: int(s) for cid, s in data["scores"].items()}


# ---------------------------------------------------------------------------
# Regression report
# ---------------------------------------------------------------------------
@dataclass
class CaseRegression:
    """One case that scored lower than its baseline."""

    case_id: str
    baseline_score: int
    current_score: int

    @property
    def drop(self) -> int:
        return self.baseline_score - self.current_score


@dataclass
class RegressionReport:
    """
    Result of comparing a current run against a baseline scoreboard.

    Attributes:
        regressions:   Cases that dropped strictly more than noise_threshold
                       (real regressions — gate fails).
        jitter:        Cases that dropped within the noise tolerance band
                       (informational; recorded but does not block).
                       Added iter-3 chg-3 after the iter-2 GC-017 2->4 and
                       iter-3 chg-1 GC-005 5->2 same-state-run swings made
                       run-to-run variance impossible to ignore. Recorded
                       then as LLM-as-judge variance; measurement since
                       attributes it to the agent (see check_regression).
        improvements:  Cases that rose (informational; never blocks).
        missing:       Baseline case_ids absent from the current run
                       (e.g. a case was dropped/renamed) — treated as a
                       gate failure, because silently losing coverage is
                       itself a regression.
        new_cases:     Case_ids in the current run but not in the baseline
                       (informational; e.g. the near-miss cases added later).
        passed:        True only if there are no regressions and nothing missing.
                       Jitter does NOT block.
    """

    regressions: list[CaseRegression] = field(default_factory=list)
    jitter: list[CaseRegression] = field(default_factory=list)
    improvements: list[CaseRegression] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    new_cases: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.regressions and not self.missing

    def summary(self) -> str:
        status = "PASSED" if self.passed else "FAILED"
        lines = [f"Per-case regression gate: {status}"]
        if self.regressions:
            lines.append(f"  Regressions ({len(self.regressions)}):")
            for r in self.regressions:
                lines.append(
                    f"    {r.case_id}: {r.baseline_score} -> {r.current_score} (drop {r.drop})"
                )
        if self.missing:
            lines.append(f"  Missing from current run: {', '.join(self.missing)}")
        if self.jitter:
            jitter_summary = ", ".join(
                f"{r.case_id} {r.baseline_score}->{r.current_score}" for r in self.jitter
            )
            lines.append(f"  Jitter within noise tolerance ({len(self.jitter)}): {jitter_summary}")
        if self.improvements:
            improved = ", ".join(
                f"{r.case_id} {r.baseline_score}->{r.current_score}" for r in self.improvements
            )
            lines.append(f"  Improvements (informational): {improved}")
        if self.new_cases:
            lines.append(f"  New cases (no baseline yet): {', '.join(self.new_cases)}")
        if self.passed and not self.jitter and not self.improvements and not self.new_cases:
            lines.append("  No per-case movement versus baseline.")
        return "\n".join(lines)


def check_regression(
    current: dict[str, int],
    baseline: dict[str, int],
    *,
    drop_threshold: int = REGRESSION_DROP_THRESHOLD,
    noise_threshold: int = 0,
) -> RegressionReport:
    """
    Compare current per-case scores against a baseline scoreboard.

    This is the heart of the gate. For every case in the baseline:
      - if it is missing from the current run                       -> `missing`
      - if current dropped by > noise_threshold AND >= drop_threshold -> `regressions`
      - if current dropped within noise_threshold (jitter)           -> `jitter`
      - if current rose                                              -> `improvements`
    Cases present only in the current run land in `new_cases` (informational).

    Crucially, this does NOT look at the 80% aggregate at all. A run can be
    comfortably above 80% and still fail this gate because one previously-strong
    case quietly degraded. That is the whole point.

    iter-3 chg-3 — noise_threshold (default 0, strict):
      Production usage should set noise_threshold=1 to suppress the +-1
      run-to-run variance observed on stable runs (e.g. GC-017's
      2 -> 4 -> 2 swing across three runs at iter-2-final, and GC-005's
      5 -> 2 swing on iter-3 chg-1's baseline-capture run with identical
      agent behavior on re-investigation). Tests use strict default to
      catch any real per-case drop; live runs use the tolerant setting
      to avoid chasing noise.

      That recommendation was made from those two anecdotes. It now has a
      measurement behind it (2026-09-06, run 34050063869): re-running the
      full pipeline three times over 20 golden cases moved 3 of them, each
      by exactly one point -- GC-005 [4,5,5], GC-010 [5,4,5], GC-012
      [3,4,4]. Max observed spread 1, which is what noise_threshold=1
      tolerates. The value was right; the justification was anecdotal.

      The same run re-scored one frozen (decision, rationale) tuple three
      times per case and found ZERO judge disagreement, so the variance
      this setting absorbs is the AGENT's, not the judge's. That matters
      for what to do about it: tolerating agent instability is a decision
      to live with it, not a measurement correction.

      Note the cost of the tolerance. At noise_threshold=1 a genuine 5->4
      quality slide is jitter, and single-draw comparison cannot tell it
      from the noise above. Sensitivity is bought with rollouts, not with a
      tighter threshold: compare medians of N runs and the median is far
      steadier than one draw, at N times the API cost.

      Setting noise_threshold higher than drop_threshold is the caller's
      choice; the most common configuration is drop_threshold=1 and
      noise_threshold=1 (a +-1 swing is jitter, a +-2 swing is a regression).
    """
    report = RegressionReport()

    for case_id, base_score in baseline.items():
        if case_id not in current:
            report.missing.append(case_id)
            continue
        cur_score = current[case_id]
        delta = base_score - cur_score  # positive when score dropped
        if delta >= drop_threshold and delta > noise_threshold:
            report.regressions.append(CaseRegression(case_id, base_score, cur_score))
        elif delta > 0 and delta <= noise_threshold:
            # Drop, but within the noise tolerance band — recorded as jitter.
            report.jitter.append(CaseRegression(case_id, base_score, cur_score))
        elif cur_score > base_score:
            report.improvements.append(CaseRegression(case_id, base_score, cur_score))

    for case_id in current:
        if case_id not in baseline:
            report.new_cases.append(case_id)

    report.regressions.sort(key=lambda r: r.case_id)
    report.jitter.sort(key=lambda r: r.case_id)
    report.missing.sort()
    report.new_cases.sort()
    return report
