"""
Application settings and configuration management.

Uses Pydantic Settings for type-safe environment variable handling
with validation and sensible defaults.
"""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Anchor the .env path to the repository root so loaded config never depends on
# the process working directory. Without this, launching from src/pacca/ would
# resolve ".env" relative to CWD and pick up a stray nested src/pacca/.env —
# silently loading different confidence thresholds (a HIPAA-relevant routing
# foot-gun). settings.py lives at src/pacca/config/, so repo root is 3 parents
# up. A missing file is skipped by pydantic, so a non-editable/wheel install
# (where __file__ resolves under site-packages and no repo .env exists) simply
# falls back to real env vars — the intended prod behavior.
_REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.

    All settings can be overridden via environment variables.
    Sensitive values use SecretStr to prevent accidental logging.
    """

    model_config = SettingsConfigDict(
        env_file=_REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ==========================================================================
    # Application
    # ==========================================================================
    app_name: str = Field(default="pacca", description="Application name")
    app_env: Literal["development", "staging", "production", "test"] = Field(
        default="development", description="Environment"
    )
    debug: bool = Field(default=True, description="Debug mode")
    scope_guard_mode: Literal["warn", "enforce"] = Field(
        default="enforce",
        description=(
            "Minimum-necessary scope guard (P-4) mode. 'warn' audits a would-be "
            "denial but lets the call proceed; 'enforce' raises ScopeViolation "
            "(→ human review). Promoted warn→enforce at chg-9 once the repaired "
            "persistence writes provided real, identifier-checked deny-capable "
            "call sites. Correct operation always passes the run's own scope, so "
            "enforce does not fire in normal flow; it fails-closed on a leak/bug."
        ),
    )
    rag_degraded_escalates: bool = Field(
        default=False,
        description=(
            "When a RAG retrieval degrades to the direct-ChromaDB fallback "
            "(GuidelineRetriever.query() reports RetrievalOutcome.degraded=True), "
            "should the case be forced to human review? Default False (warn): the "
            "degradation is always audited (action='rag.degraded') and logged, but "
            "the decision proceeds on the fallback context. True (enforce): the case "
            "is routed to AuthorizationStatus.IN_REVIEW / ReviewTier.HUMAN instead. "
            "This mirrors the P-4 scope guard's own iter-8 -> iter-9 rollout: land "
            "in warn mode, observe how often degradation actually fires in "
            "production, then promote to fail-closed in a later iteration. Jumping "
            "straight to enforce risks flooding the human review queue on a "
            "condition nobody has measured yet — that queue-flood risk is itself a "
            "safety problem, not just an inconvenience."
        ),
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO", description="Logging level"
    )

    # ==========================================================================
    # API Keys
    # ==========================================================================
    anthropic_api_key: SecretStr = Field(
        default=SecretStr("sk-ant-placeholder"),
        description="Anthropic API key for Claude",
    )

    # Optional: Pinecone for production vector store
    pinecone_api_key: SecretStr | None = Field(default=None, description="Pinecone API key")
    pinecone_environment: str | None = Field(default=None, description="Pinecone environment")
    pinecone_index_name: str = Field(default="pacca-guidelines", description="Pinecone index name")

    # ==========================================================================
    # Database
    # ==========================================================================
    database_url: str = Field(
        default="sqlite+aiosqlite:///./pacca.db",
        description="Database connection URL",
    )

    # Connection pool settings
    db_pool_size: int = Field(default=5, ge=1, le=20, description="Connection pool size")
    db_max_overflow: int = Field(default=10, ge=0, le=50, description="Max overflow connections")
    db_pool_timeout: int = Field(default=30, ge=5, le=120, description="Pool timeout seconds")

    # ==========================================================================
    # Redis
    # ==========================================================================
    redis_url: str = Field(default="redis://localhost:6379/0", description="Redis connection URL")
    redis_enabled: bool = Field(default=False, description="Enable Redis caching")

    # ==========================================================================
    # Security
    # ==========================================================================
    # There is deliberately no `secret_key` field here. It used to exist with a
    # default of "dev-secret-key-change-in-production-min-32-characters" — long
    # enough to satisfy a length check, published in this repo, and read by no
    # code path. Its only effect was to make the config appear to hold a usable
    # signing key while the real one lives in `pacca.api.auth.SECRET_KEY`
    # (environment-only, no default), validated at startup by
    # `auth.validate_secret_key`. One source, one gate.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default=["http://localhost:3000", "http://localhost:5173"],
        description="Allowed CORS origins",
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: str | list[str]) -> list[str]:
        """Parse CORS origins from comma-separated string or list."""
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    # ==========================================================================
    # LLM / Agent Configuration
    # ==========================================================================
    default_model: str = Field(
        default="claude-sonnet-4-5-20250929",
        description="Default Claude model for agents",
    )
    max_tokens: int = Field(default=4096, ge=256, le=8192, description="Max tokens for responses")
    agent_timeout: int = Field(default=60, ge=10, le=300, description="Agent timeout in seconds")

    # Confidence thresholds
    auto_approve_confidence_threshold: float = Field(
        default=0.95,
        ge=0.5,
        le=1.0,
        description="Confidence threshold for autonomous approval",
    )
    escalation_confidence_threshold: float = Field(
        default=0.90,
        ge=0.3,
        le=1.0,
        description="Confidence threshold below which to escalate",
    )

    # ==========================================================================
    # Clinical Settings
    # ==========================================================================
    complexity_auto_approve_max: int = Field(
        default=2,
        ge=1,
        le=5,
        description="Maximum complexity level for auto-approval",
    )
    complexity_specialist_review_min: int = Field(
        default=4,
        ge=1,
        le=5,
        description="Minimum complexity level requiring specialist",
    )
    high_cost_threshold: int = Field(
        default=100000,
        ge=0,
        description="Cost threshold for medical director review",
    )

    # ==========================================================================
    # Observability
    # ==========================================================================
    enable_tracing: bool = Field(default=True, description="Enable agent tracing")
    metrics_enabled: bool = Field(default=True, description="Enable metrics endpoint")

    # OpenTelemetry configuration
    # When otel_endpoint is set, traces are exported to that collector
    # (Langfuse, Jaeger, Tempo, etc.). When None, traces are logged locally only.
    otel_endpoint: str | None = Field(
        default=None,
        description="OpenTelemetry collector endpoint (e.g. http://localhost:4318)",
    )
    otel_service_name: str = Field(
        default="pacca",
        description="Service name reported in OTel traces",
    )
    otel_enabled: bool = Field(
        default=True,
        description="Enable OpenTelemetry span instrumentation",
    )

    # Retry configuration for LLM API calls
    # These values control tenacity's exponential backoff behaviour.
    llm_retry_max_attempts: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Maximum number of LLM call attempts before giving up",
    )
    llm_retry_wait_min_seconds: float = Field(
        default=1.0,
        ge=0.1,
        le=10.0,
        description="Minimum wait seconds between retries (exponential base)",
    )
    llm_retry_wait_max_seconds: float = Field(
        default=30.0,
        ge=1.0,
        le=120.0,
        description="Maximum wait seconds between retries (exponential cap)",
    )

    # ==========================================================================
    # Rate Limiting
    # ==========================================================================
    rate_limit_rpm: int = Field(default=60, ge=1, le=1000, description="Requests per minute limit")

    # ==========================================================================
    # Feature Flags
    # ==========================================================================
    enable_autonomous_decisions: bool = Field(
        default=True, description="Allow autonomous agent decisions"
    )
    enable_rag: bool = Field(default=True, description="Enable RAG-based guideline retrieval")
    demo_mode: bool = Field(default=True, description="Use mock EHR data for demo")

    # ==========================================================================
    # Computed Properties
    # ==========================================================================
    @property
    def is_production(self) -> bool:
        """Check if running in production environment."""
        return self.app_env == "production"

    @property
    def is_development(self) -> bool:
        """Check if running in development environment."""
        return self.app_env == "development"

    @property
    def use_pinecone(self) -> bool:
        """Check if Pinecone should be used instead of local ChromaDB."""
        return self.pinecone_api_key is not None and self.pinecone_environment is not None


@lru_cache
def get_settings() -> Settings:
    """
    Get cached settings instance.

    Uses lru_cache to ensure settings are only loaded once.
    Clear cache with get_settings.cache_clear() if needed.
    """
    return Settings()


# ── Runtime override store (shared source of truth for tunable settings) ──────
# Set via PATCH /config, merged over the cached Settings by effective_settings().
# Cleared on restart by design (restart == reload from env). Process-local dict;
# safe under the single asyncio loop, same exposure model as before.
_runtime_overrides: dict[str, object] = {}


def effective_settings() -> "Settings":
    """get_settings() with runtime overrides applied and re-validated. Cheap; call per request."""
    base = get_settings()
    if not _runtime_overrides:
        return base
    merged = base.model_dump()
    merged.update(_runtime_overrides)
    return Settings.model_validate(merged)


def active_overrides() -> dict[str, object]:
    """The currently-applied runtime overrides (copy).

    Note: secret fields must never appear here — see ``apply_overrides`` docstring."""
    return dict(_runtime_overrides)


def clear_all_overrides() -> list[str]:
    """Drop all runtime overrides; return the field names that were cleared."""
    cleared = list(_runtime_overrides.keys())
    _runtime_overrides.clear()
    return cleared


def _validate_effective() -> None:
    """Enforce cross-field invariants on the merged effective settings."""
    s = effective_settings()
    if s.auto_approve_confidence_threshold <= s.escalation_confidence_threshold:
        raise ValueError(
            f"auto_approve_confidence_threshold ({s.auto_approve_confidence_threshold}) "
            f"must be greater than escalation_confidence_threshold "
            f"({s.escalation_confidence_threshold}). The Medical Director escalation "
            f"band would collapse to nothing."
        )
    if s.llm_retry_wait_min_seconds > s.llm_retry_wait_max_seconds:
        raise ValueError(
            f"llm_retry_wait_min_seconds ({s.llm_retry_wait_min_seconds}) must not "
            f"exceed llm_retry_wait_max_seconds ({s.llm_retry_wait_max_seconds})."
        )


def apply_overrides(updates: dict[str, object]) -> None:
    """Apply runtime overrides atomically. Validates the merged result; on failure
    no field from `updates` is applied. Raises ValueError with a readable message.

    IMPORTANT: Do NOT include secret fields (e.g. ``anthropic_api_key``,
    ``secret_key``) in ``updates``.  Those values would be stored in
    ``_runtime_overrides`` in plaintext and returned verbatim by
    ``active_overrides()``.  The admin PATCH /config endpoint enforces a
    tunable-field allowlist via the ConfigPatchRequest model (which exposes
    only non-secret tunables); secret fields must never be passed here."""
    unknown = set(updates) - set(Settings.model_fields)
    if unknown:
        raise ValueError(f"Unknown config field(s): {sorted(unknown)}")
    snapshot = dict(_runtime_overrides)
    _runtime_overrides.update(updates)
    try:
        _validate_effective()
    except ValueError:
        _runtime_overrides.clear()
        _runtime_overrides.update(snapshot)
        raise


# Convenience function for common settings access
def get_anthropic_api_key() -> str:
    """Get the Anthropic API key as a string."""
    return get_settings().anthropic_api_key.get_secret_value()
