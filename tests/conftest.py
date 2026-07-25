"""
Pytest shared configuration and fixtures for the PACCA test suite.

This conftest provides:
  - Environment setup so tests run without a real API key or OTel collector
  - Common fixtures shared across test modules

Unit tests (tests/unit/) are self-contained — they define their own mocks.

Clinical tests (tests/clinical/) require ANTHROPIC_API_KEY and are
marked @pytest.mark.clinical to run separately from the fast suite.
"""

import os

import pytest

# =============================================================================
# Collection scope
# =============================================================================

# collect_ignore paths resolve relative to THIS file's directory, so the former
# "tests/..." prefixes matched nothing and both files were collected anyway. That
# is how test_level5_flow.py came to contribute 3 collection errors to every run
# while appearing to be excluded. Both entries are now unnecessary:
# test_level5_flow.py is fixed and marked `clinical`, and unit/test_api.py is a
# stub containing no tests. Nothing is excluded from collection.
collect_ignore: list[str] = []


# =============================================================================
# Environment setup — runs before any test imports
# =============================================================================


def pytest_configure(config):
    """
    Set test-safe environment variables before any tests run.

    This runs before imports, so modules that read os.getenv() at import
    time receive safe test values rather than raising at startup.
    """
    # Prevent validate_secret_key() from raising during test collection.
    if not os.getenv("SECRET_KEY"):
        os.environ["SECRET_KEY"] = "test-secret-key-min-32-chars-for-unit-tests"

    # Disable OTel during tests — no collector running in CI.
    os.environ.setdefault("OTEL_ENABLED", "false")

    # Mark as test environment.
    # 'test' is now a valid app_env value (added to Settings Literal).
    os.environ.setdefault("APP_ENV", "test")

    # Clear the lru_cache on Settings so the test env vars above take effect.
    # Without this, a previously cached Settings instance (with APP_ENV=development)
    # would be reused and the test env vars would be ignored.
    try:
        from pacca.config.settings import get_settings

        get_settings.cache_clear()
    except Exception:
        pass  # Module not yet importable at this stage — that's fine


# =============================================================================
# Shared fixtures
# =============================================================================


@pytest.fixture
def any_authorized_user() -> str:
    """A dummy authenticated username for routes that require JWT."""
    return "test_provider"
