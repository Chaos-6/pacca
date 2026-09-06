"""
`latest_baseline` picks the newest recorded baseline, numerically.

The bug this exists to prevent is a string sort. Baseline files are named
`iter-N-baseline.json`, and lexically "iter-10" sorts BEFORE "iter-6" — so a
sorted()[-1] implementation looks correct for as long as the count stays in
single digits and then silently starts reporting against a stale baseline the
first time it reaches ten. Nothing would fail; the report would just quietly
compare against the wrong thing, which is the same failure mode as the gate
never having been wired at all.

The repo is at iter-26 in its manifests, so double digits are not hypothetical.
"""

from __future__ import annotations

from pathlib import Path

from tests.clinical.regression_gate import latest_baseline


def _touch(directory: Path, name: str) -> Path:
    path = directory / name
    path.write_text('{"iteration_tag": "x", "scores": {}}')
    return path


class TestLatestBaseline:
    def test_picks_numerically_highest_not_lexically(self, tmp_path: Path) -> None:
        _touch(tmp_path, "iter-6-baseline.json")
        expected = _touch(tmp_path, "iter-10-baseline.json")
        _touch(tmp_path, "iter-9-baseline.json")
        assert latest_baseline(tmp_path) == expected, (
            "a lexical sort puts iter-6 last and would report against a stale baseline"
        )

    def test_handles_suffixed_iterations(self, tmp_path: Path) -> None:
        """iter-3-chg1-baseline.json exists in the repo; its leading int orders it."""
        _touch(tmp_path, "iter-3-baseline.json")
        _touch(tmp_path, "iter-3-chg1-baseline.json")
        expected = _touch(tmp_path, "iter-4-baseline.json")
        assert latest_baseline(tmp_path) == expected

    def test_tie_breaks_deterministically(self, tmp_path: Path) -> None:
        _touch(tmp_path, "iter-3-baseline.json")
        _touch(tmp_path, "iter-3-chg1-baseline.json")
        first = latest_baseline(tmp_path)
        assert first == latest_baseline(tmp_path), "selection must not vary between calls"

    def test_missing_directory_is_none_not_an_error(self, tmp_path: Path) -> None:
        """The report degrades to 'skipped'; it must not break the clinical gate."""
        assert latest_baseline(tmp_path / "does-not-exist") is None

    def test_empty_directory_is_none(self, tmp_path: Path) -> None:
        assert latest_baseline(tmp_path) is None

    def test_ignores_unrelated_files(self, tmp_path: Path) -> None:
        _touch(tmp_path, "README.md")
        _touch(tmp_path, "iter-notanumber-baseline.json")
        expected = _touch(tmp_path, "iter-2-baseline.json")
        assert latest_baseline(tmp_path) == expected

    def test_finds_the_repo_baseline(self) -> None:
        """Wired against the real directory the clinical gate reads."""
        repo_baselines = Path(__file__).resolve().parents[1] / "clinical" / "baselines"
        found = latest_baseline(repo_baselines)
        assert found is not None and found.name.startswith("iter-")
