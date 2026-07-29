"""The clinical gate's run/skip decision, executed rather than read.

`clinical-gate` is one of the five required status checks on `main`. It decides for
itself whether to run, via a shell step in `.github/workflows/ci.yml`. A decision step
that wrongly answers "skip" does not fail -- it reports **success** without having run
anything, which is the worst possible failure mode for a required check.

That is not hypothetical. Observed 2026-07-29 on run 30470889996: a push carrying
chg-19 through chg-25 -- every behavioural change of the preceding three days -- was
answered `run=false` with the notice "no agent/rag change or chg- commit", and the job
went green. The cause was the base fallback:

    base="${BASE_SHA:-origin/main}"

`BASE_SHA` comes from `github.event.pull_request.base.sha`, which is empty on a push.
So on a push **to** main, base resolved to the literal ref `origin/main`, which after
`actions/checkout` is HEAD itself. `git diff origin/main...HEAD` is empty by
construction, the `chg-` log scan is empty, and the gate skips -- always, for every
push to main, regardless of content.

These tests execute the real step's real script (parsed out of ci.yml, never a copy
that can drift) against synthetic git repositories that reproduce CI's post-checkout
state, including a `refs/remotes/origin/main` that equals HEAD.

Per AGENT_LESSONS P-010, `test_push_to_main_carrying_a_chg_commit_runs_the_gate` is the
permanent proof: it fails against the pre-fix workflow and passes after. It is a
mutation-style pin, not a one-time demonstration.

**What these tests deliberately do NOT prove.** The `${{ }}` expressions in the step's
`env:` block are evaluated by GitHub, not here, so a wrong expression (say
`github.event.after` where `before` was meant) would still pass the simulation. The
env block is therefore asserted structurally in
`test_step_binds_a_variable_to_the_push_base_sha`, and the two layers together are what
covers it.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CI_WORKFLOW = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
_ZERO_SHA = "0" * 40


# ─────────────────────────────────────────────────────────────────────────────
# Reading the real step out of the real workflow
# ─────────────────────────────────────────────────────────────────────────────
def _decision_step() -> dict:
    """The clinical-gate step that decides run/skip, straight from ci.yml."""
    workflow = yaml.safe_load(_CI_WORKFLOW.read_text())
    steps = workflow["jobs"]["clinical-gate"]["steps"]
    for step in steps:
        if step.get("id") == "gate":
            return step
    raise AssertionError("clinical-gate has no step with id 'gate'")


def _env_bindings(step: dict) -> dict[str, str]:
    """Map each env var the step declares to the GitHub expression behind it."""
    return {name: str(expr) for name, expr in (step.get("env") or {}).items()}


def _resolve_env(
    step: dict, *, event_name: str, pr_base_sha: str, push_before_sha: str
) -> dict[str, str]:
    """Supply each declared env var the value GitHub would supply.

    Driven by the step's own `env:` wiring rather than hardcoded names, so the test
    stays honest whatever the fix chooses to call its variables. A var bound to an
    expression this helper does not recognise is passed through empty, exactly as
    GitHub does for a context field that is absent on the event.
    """
    resolved = {}
    for name, expression in _env_bindings(step).items():
        if "github.event_name" in expression:
            resolved[name] = event_name
        elif "pull_request.base.sha" in expression:
            resolved[name] = pr_base_sha
        elif "github.event.before" in expression:
            resolved[name] = push_before_sha
        else:
            resolved[name] = ""
    return resolved


# ─────────────────────────────────────────────────────────────────────────────
# A synthetic repo in CI's post-checkout state
# ─────────────────────────────────────────────────────────────────────────────
def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _commit(repo: Path, relpath: str, body: str, subject: str) -> str:
    target = repo / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body)
    _git(repo, "add", relpath)
    _git(repo, "commit", "-m", subject)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repo with one base commit, on main, with origin/main tracking it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _commit(repo, "README.md", "base\n", "chore: base commit")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    return repo


def _land_the_push(repo: Path) -> None:
    """Advance origin/main to HEAD, as actions/checkout leaves it after a push to main.

    Load-bearing, and easy to get wrong: point origin/main at HEAD *before* adding the
    pushed commits and the old fallback resolves to a genuine base, the defect vanishes,
    and the regression test passes against unfixed code. That happened on the first
    draft of this file. The push has already landed by the time CI checks out, so
    origin/main IS the new HEAD -- which is precisely why diffing against it is empty.
    """
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")


def _run_decision(
    repo: Path,
    *,
    event_name: str,
    pr_base_sha: str = "",
    push_before_sha: str = "",
) -> str:
    """Execute the decision script; return the value it wrote for `run`."""
    step = _decision_step()
    env = _resolve_env(
        step,
        event_name=event_name,
        pr_base_sha=pr_base_sha,
        push_before_sha=push_before_sha,
    )
    output_file = repo / "_github_output"
    output_file.write_text("")
    env["GITHUB_OUTPUT"] = str(output_file)
    env["PATH"] = "/usr/bin:/bin:/usr/local/bin"
    env["HOME"] = str(repo)

    completed = subprocess.run(
        ["bash", "-c", step["run"]],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,  # asserted below, with the step's own output in the message
    )
    assert completed.returncode == 0, (
        f"decision step exited {completed.returncode}\n"
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )

    matches = re.findall(r"^run=(\S+)$", output_file.read_text(), re.MULTILINE)
    assert matches, (
        "decision step wrote no run= value\n"
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
    # The script exits after its first write; the last value is the effective one.
    return matches[-1]


# ─────────────────────────────────────────────────────────────────────────────
# The regression this file exists for
# ─────────────────────────────────────────────────────────────────────────────
def test_push_to_main_carrying_a_chg_commit_runs_the_gate(repo: Path) -> None:
    """The 2026-07-29 defect: behavioural commits pushed to main skipped the gate.

    Fails against the pre-fix workflow, where base fell back to `origin/main` == HEAD.
    """
    before = _git(repo, "rev-parse", "HEAD")
    _commit(repo, "docs/notes.md", "x\n", "chg-99: a behavioural change")
    _land_the_push(repo)

    assert _run_decision(repo, event_name="push", push_before_sha=before) == "true"


def test_push_to_main_touching_agent_code_runs_the_gate(repo: Path) -> None:
    """The other arm of the same defect: agent/rag edits pushed straight to main."""
    before = _git(repo, "rev-parse", "HEAD")
    _commit(repo, "src/pacca/agents/orchestrator.py", "x = 1\n", "refactor: tweak")
    _land_the_push(repo)

    assert _run_decision(repo, event_name="push", push_before_sha=before) == "true"


def test_push_to_main_with_only_docs_still_skips(repo: Path) -> None:
    """The gate must stay selective. Fixing the skip by always running is not a fix --
    it would spend ~11 minutes of live API calls on every README typo."""
    before = _git(repo, "rev-parse", "HEAD")
    _commit(repo, "docs/notes.md", "x\n", "docs: fix a typo")
    _land_the_push(repo)

    assert _run_decision(repo, event_name="push", push_before_sha=before) == "false"


def test_unresolvable_push_base_runs_the_gate(repo: Path) -> None:
    """An undecidable base must fail OPEN.

    Branch creation sends an all-zero `before`; a force-push can send a sha that no
    longer exists. Either way the pushed range cannot be computed, so the gate cannot
    prove the push is harmless -- and an unprovable push must be gated, not waved
    through. Choosing the cheap answer over the safe one is the original bug.
    """
    _commit(repo, "docs/notes.md", "x\n", "docs: whatever")
    _land_the_push(repo)

    assert _run_decision(repo, event_name="push", push_before_sha=_ZERO_SHA) == "true"
    assert _run_decision(repo, event_name="push", push_before_sha="") == "true"


def test_step_binds_a_variable_to_the_push_base_sha() -> None:
    """Structural cover for what the simulation cannot see.

    The env values are `${{ }}` expressions GitHub evaluates, so the behavioural tests
    above would pass even if the workflow read the wrong context field. This asserts the
    wiring itself: some variable must carry `github.event.before`, or a push event has
    no base to diff against no matter how correct the shell logic is.
    """
    expressions = " ".join(_env_bindings(_decision_step()).values())

    assert "github.event.before" in expressions, (
        "no env var is bound to github.event.before, so on a push the decision step "
        "has no pushed-range base and must fall back -- the 2026-07-29 defect"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Paths that already worked, pinned so the fix does not break them
# ─────────────────────────────────────────────────────────────────────────────
def test_pull_request_touching_agent_code_still_runs_the_gate(repo: Path) -> None:
    base = _git(repo, "rev-parse", "HEAD")
    _commit(repo, "src/pacca/rag/pipeline.py", "x = 1\n", "refactor: tweak retrieval")

    assert _run_decision(repo, event_name="pull_request", pr_base_sha=base) == "true"


def test_pull_request_with_only_docs_still_skips(repo: Path) -> None:
    base = _git(repo, "rev-parse", "HEAD")
    _commit(repo, "docs/notes.md", "x\n", "docs: fix a typo")

    assert _run_decision(repo, event_name="pull_request", pr_base_sha=base) == "false"


@pytest.mark.parametrize("event", ["schedule", "workflow_dispatch"])
def test_scheduled_and_manual_runs_are_unconditional(repo: Path, event: str) -> None:
    assert _run_decision(repo, event_name=event) == "true"
