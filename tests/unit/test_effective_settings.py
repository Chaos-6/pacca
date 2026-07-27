"""Unit tests for the shared runtime-override store + effective_settings()."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from pacca.config.settings import (
    Settings,
    active_overrides,
    apply_overrides,
    clear_all_overrides,
    effective_settings,
    get_settings,
)


@pytest.fixture(autouse=True)
def _reset_overrides() -> Iterator[None]:
    clear_all_overrides()
    yield
    clear_all_overrides()


def test_effective_equals_base_when_no_overrides() -> None:
    s = effective_settings()
    base = get_settings()
    assert s.model_dump() == base.model_dump()


def test_apply_override_is_reflected() -> None:
    apply_overrides({"high_cost_threshold": 40_000})
    assert effective_settings().high_cost_threshold == 40_000
    assert "high_cost_threshold" in active_overrides()


def test_apply_override_does_not_mutate_base() -> None:
    base_before = get_settings().high_cost_threshold
    apply_overrides({"high_cost_threshold": 1})
    assert get_settings().high_cost_threshold == base_before  # cached base untouched


def test_rejects_auto_approve_not_above_escalation() -> None:
    # Use the real default escalation threshold (equal → must reject, not just below).
    esc = get_settings().escalation_confidence_threshold
    with pytest.raises(ValueError, match=r"greater than|escalation band"):
        apply_overrides({"auto_approve_confidence_threshold": esc})


def test_invalid_override_is_atomic() -> None:
    apply_overrides({"high_cost_threshold": 70_000})  # valid baseline override
    with pytest.raises(ValueError):
        apply_overrides(
            {
                "llm_retry_max_attempts": 9,  # valid
                "auto_approve_confidence_threshold": 0.10,  # invalid (<= escalation)
            }
        )
    # Neither field from the failed batch applied; the earlier valid override survives.
    assert "llm_retry_max_attempts" not in active_overrides()
    assert active_overrides()["high_cost_threshold"] == 70_000


def test_clear_all_overrides_returns_cleared_keys() -> None:
    apply_overrides({"high_cost_threshold": 40_000, "demo_mode": False})
    cleared = clear_all_overrides()
    assert set(cleared) == {"high_cost_threshold", "demo_mode"}
    assert active_overrides() == {}


def test_rejects_out_of_range_field_value() -> None:
    """Fix 1: model_validate enforces per-field constraints; negative cost threshold must fail."""
    with pytest.raises(ValueError):
        apply_overrides({"high_cost_threshold": -1})
    # Rollback: override must not have been applied.
    assert "high_cost_threshold" not in active_overrides()


def test_rejects_unknown_field() -> None:
    """Fix 2: unknown keys must be rejected before any mutation."""
    with pytest.raises(ValueError, match="Unknown config field"):
        apply_overrides({"totally_bogus_field": 42})
    assert active_overrides() == {}


def test_default_confidence_thresholds_preserve_orchestrator_behavior() -> None:
    # TRIPWIRE: these defaults ARE the live clinical routing policy. The
    # orchestrator reads them via effective_settings() for Branches 1-3 and the
    # Medical-Director gate, so changing them is a clinical-policy change, not a
    # refactor. If you intentionally re-tuned the thresholds and this fails, you
    # MUST also (see ADR-004):
    #   1. update docs/assets/decision_trace.svg + docs/assets/architecture_v2.4.svg
    #      (they render the 0.95 / 0.90 bands),
    #   2. update the Orchestrator module docstring (Branches 1-3) in
    #      src/pacca/agents/orchestrator.py, and
    #   3. re-baseline the live golden-20 gate (make test-clinical) — new
    #      thresholds change which cases auto-approve vs. escalate.
    coupling = (
        "Confidence-threshold DEFAULT changed — this is a clinical-policy change. "
        "Also update decision_trace.svg + architecture_v2.4.svg, the orchestrator "
        "docstring (Branches 1-3), and re-baseline the golden-20 gate (make test-clinical)."
    )
    fields = get_settings().__class__.model_fields
    assert fields["auto_approve_confidence_threshold"].default == 0.95, coupling
    assert fields["escalation_confidence_threshold"].default == 0.90, coupling


def test_rag_degraded_escalates_defaults_to_warn_mode() -> None:
    """
    chg-20: RAG-degradation enforcement must default to False (warn) — audit
    and proceed, not fail-closed — mirroring the P-4 scope guard's warn-first
    rollout. Promoting to True is a deliberate later step once production
    degradation rates are known, never the out-of-the-box behavior.
    """
    assert get_settings().rag_degraded_escalates is False
    assert Settings.model_fields["rag_degraded_escalates"].default is False


def test_env_file_is_anchored_to_repo_root_not_cwd() -> None:
    """env_file must resolve to an absolute repo-root path, not a CWD-relative
    '.env'. Otherwise a process launched from src/pacca/ would load the stray
    nested src/pacca/.env and could silently apply different confidence
    thresholds (the 0.85/0.75 foot-gun). Regression guard for that drift."""
    env_file = Settings.model_config["env_file"]
    repo_root = Path(__file__).resolve().parents[2]  # tests/unit/ -> repo root
    # Narrow the pydantic-settings union type (Path | Sequence | None) -> Path;
    # this also asserts we configured a single Path, not a str or sequence.
    assert isinstance(env_file, Path), f"env_file must be a Path, got {type(env_file)!r}"
    assert env_file.is_absolute(), "env_file must be absolute (CWD-independent)"
    assert env_file == repo_root / ".env"
