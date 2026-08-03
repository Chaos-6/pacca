"""Every recorded iteration must have a narrative entry in DECISIONS.md.

Why this file exists
---------------------
PACCA keeps three overlapping records of the same work:

  * `harness/manifests/iter-N.json` — structured. JSON schema, a validator
    (`validate_manifest --all`), a REQUIRED CI check, and a PR-template field
    demanding the path. Complete and current.
  * `docs/DECISIONS.md` — narrative. Per-iteration analysis, verdict reasoning,
    and the things a schema cannot hold: "the one red test, and why it is not
    attributed to this iteration", "limitations of this evaluation".
  * `docs/ITERATIONS.md` — a second narrative log, AHE Appendix-C style.

Only the first was enforced. On 2026-08-03 the other two were found dead:
DECISIONS.md stopped at iter-14 (2026-07-26) and ITERATIONS.md at iter-7
(2026-05-31), while the manifests ran to iter-24. Nobody decided to retire
them; they simply stopped being the path of least resistance once the manifest
schema grew a `verdicts[]` array that absorbed most of their content.

That is this repo's most-repeated lesson arriving again (AGENT_LESSONS P-014,
P-016): what is not checked does not happen. Prose obligations without a gate
decay silently, and "try harder" is not a fix.

David's call, 2026-08-03: keep DECISIONS.md and enforce it. This is the gate.
It asserts coverage, not quality — it cannot tell whether an entry says anything
useful, only that the iteration was not skipped. That is the weak but real
guarantee, and it is stated rather than implied.

NOT covered here: ITERATIONS.md, which is 17 iterations behind and whose role
overlaps this file's almost entirely. Reviving or retiring it is a separate
decision and is deliberately not made by this test.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST_DIR = _REPO_ROOT / "harness" / "manifests"
_DECISIONS = _REPO_ROOT / "docs" / "DECISIONS.md"


def _recorded_iterations() -> list[int]:
    """Iteration numbers that have a manifest, i.e. that actually happened."""
    return sorted(
        int(m.group(1))
        for p in _MANIFEST_DIR.glob("iter-*.json")
        if (m := re.fullmatch(r"iter-(\d+)", p.stem))
    )


def _documented_iterations() -> set[int]:
    """Iteration numbers with a top-level narrative section in DECISIONS.md."""
    text = _DECISIONS.read_text()
    # Two heading shapes, both historical and both legitimate. iter-1 predates
    # the convention and is titled "## chg-1 (iter-1) — ...". Normalising it
    # would break an existing anchor for no gain, so the matcher accepts both
    # rather than the document being rewritten to satisfy the test.
    return {
        int(m.group(1) or m.group(2))
        for m in re.finditer(
            r"^##\s+iter-(\d+)\s+—|^##\s+chg-\d+\s+\(iter-(\d+)\)\s+—",
            text,
            re.MULTILINE,
        )
    }


def test_every_recorded_iteration_has_a_decisions_entry() -> None:
    """GATE: a manifest without a narrative entry is an undocumented iteration.

    The manifest holds WHAT changed and the verdict. It has no room for why an
    iteration was shaped the way it was, what an unexplained red test meant, or
    what the evaluation could not establish. That reasoning is the thing most
    expensive to reconstruct later and the first thing lost when a prose log
    goes unenforced.
    """
    recorded = _recorded_iterations()
    documented = _documented_iterations()
    missing = [n for n in recorded if n not in documented]

    assert missing == [], (
        "iterations with a manifest but no DECISIONS.md entry: "
        + ", ".join(f"iter-{n}" for n in missing)
        + ". Add a `## iter-N — <title>` section; the manifest records the verdict, "
        "this records the reasoning."
    )


def test_the_gate_is_reading_real_data() -> None:
    """Denominator check (AGENT_LESSONS P-016).

    A coverage gate that enumerates nothing passes vacuously and looks identical
    to one that passes honestly. If either side collapses to empty, this fails
    rather than reporting perfect coverage of nothing.
    """
    assert len(_recorded_iterations()) >= 20, "manifest enumeration collapsed"
    assert len(_documented_iterations()) >= 10, "DECISIONS.md section parsing collapsed"
