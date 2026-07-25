"""
Tests for Week 6: Security hardening, async consolidation, and RAG pipeline.

Covers:
  1. SECRET_KEY loaded from environment (not hardcoded)
  2. validate_secret_key() raises on missing/short key
  3. Token expiry uses TOKEN_EXPIRE_MINUTES from environment
  4. Auth routes use async session (not sync SessionLocal)
  5. GuidelineRetriever delegates to RAGPipeline
  6. Fallback to direct ChromaDB when pipeline unavailable
  7. add_guideline() and add_precedent() work correctly
  8. Application startup fails fast on missing SECRET_KEY

Teaching note — testing security properties:

  Security tests are unusual because you're testing the ABSENCE of bad behavior
  as much as the presence of good behavior. Key patterns here:

  "The key must NOT be in source code" — we verify SECRET_KEY reads from
  os.getenv, not that a specific value is present.

  "validate_secret_key() must fail fast" — we verify it raises, not
  that it silently passes. Fail-fast is a safety property.

  "Token expiry must be configurable" — we verify the expiry uses the
  configured value, not a hardcoded constant.

These tests are fast (no API calls, no database) and run in the standard
unit suite on every commit.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

# =============================================================================
# Security: SECRET_KEY from environment
# =============================================================================


class TestSecretKeyFromEnvironment:
    """
    Verify SECRET_KEY is loaded from the environment, not hardcoded.

    This is the most important security test in the suite. If SECRET_KEY
    is hardcoded, anyone who clones the repository can forge JWT tokens.
    """

    def test_secret_key_reads_from_environment(self, monkeypatch):
        """
        SECRET_KEY must be the value from the environment variable,
        not a hardcoded string from source code.
        """
        test_key = "a" * 32  # 32-char test key
        monkeypatch.setenv("SECRET_KEY", test_key)

        # Force re-import to pick up the new env var
        import importlib

        import pacca.api.auth as auth_module

        importlib.reload(auth_module)

        assert test_key == auth_module.SECRET_KEY, (
            f"SECRET_KEY should equal the environment variable value. "
            f"Got: {auth_module.SECRET_KEY[:8]!r}... "
            f"If this is a hardcoded string, the key is visible to anyone "
            f"who reads the source code."
        )

    def test_secret_key_not_hardcoded_sentinel(self):
        """
        The known-bad hardcoded value must not be the default.

        The original code had: SECRET_KEY = "your-super-secret-development-key"
        This test verifies that specific string is gone.
        """
        import inspect

        import pacca.api.auth as auth_module

        source = inspect.getsource(auth_module)
        assert "your-super-secret-development-key" not in source, (
            "Hardcoded SECRET_KEY found in auth.py source code. "
            "The key must come from os.getenv('SECRET_KEY'), never from source."
        )

    def test_secret_key_not_present_in_source_as_string_literal(self):
        """
        No string that looks like a JWT secret should be in auth.py source.
        """
        import inspect

        import pacca.api.auth as auth_module

        source = inspect.getsource(auth_module)
        # No long string literals that could be a hardcoded key
        # (excludes docstrings and comments by checking for = assignment)
        import re

        suspicious = re.findall(r'SECRET_KEY\s*=\s*"[^"]{8,}"', source)
        assert not suspicious, (
            f"Found suspicious SECRET_KEY assignment with string literal: {suspicious}. "
            "SECRET_KEY must be assigned from os.getenv(), not a string literal."
        )


class TestValidateSecretKey:
    """
    Verify validate_secret_key() fails fast on bad configuration.
    """

    def test_raises_on_empty_key(self):
        """Empty SECRET_KEY must raise RuntimeError at startup."""
        from pacca.api.auth import validate_secret_key

        with patch("pacca.api.auth.SECRET_KEY", ""):
            with pytest.raises(RuntimeError) as exc_info:
                validate_secret_key()
            assert "SECRET_KEY" in str(exc_info.value)

    def test_raises_on_short_key(self):
        """Keys shorter than 32 characters must raise RuntimeError."""
        from pacca.api.auth import validate_secret_key

        with patch("pacca.api.auth.SECRET_KEY", "tooshort"):
            with pytest.raises(RuntimeError) as exc_info:
                validate_secret_key()
            assert "32" in str(exc_info.value), (
                "Error message should mention the 32-character minimum."
            )

    def test_passes_on_adequate_key(self):
        """A 32-character key must pass validation without raising."""
        from pacca.api.auth import validate_secret_key

        with patch("pacca.api.auth.SECRET_KEY", "x" * 32):
            # Should not raise
            validate_secret_key()

    def test_passes_on_long_key(self):
        """Keys longer than 32 characters must also pass."""
        from pacca.api.auth import validate_secret_key

        with patch("pacca.api.auth.SECRET_KEY", "x" * 64):
            validate_secret_key()

    def test_error_message_includes_generation_command(self):
        """
        The error message must tell the developer how to fix it.
        An error without guidance is only half useful.
        """
        from pacca.api.auth import validate_secret_key

        with patch("pacca.api.auth.SECRET_KEY", ""):
            with pytest.raises(RuntimeError) as exc_info:
                validate_secret_key()
            # Error should tell the user how to generate a proper key
            assert "secrets" in str(exc_info.value).lower() or "token_hex" in str(exc_info.value), (
                "Error message should include the key generation command: "
                'python -c "import secrets; print(secrets.token_hex(32))"'
            )


class TestTokenExpiry:
    """
    Verify token expiry uses TOKEN_EXPIRE_MINUTES from environment.
    """

    def test_token_expire_minutes_readable_from_env(self, monkeypatch):
        """TOKEN_EXPIRE_MINUTES should be configurable via environment."""
        monkeypatch.setenv("TOKEN_EXPIRE_MINUTES", "15")

        import importlib

        import pacca.api.auth as auth_module

        importlib.reload(auth_module)

        assert auth_module.TOKEN_EXPIRE_MINUTES == 15, (
            "TOKEN_EXPIRE_MINUTES should equal the environment variable value."
        )

    def test_default_expiry_is_30_minutes_or_less(self):
        """
        Default token expiry must be <= 30 minutes.

        The original code had 60-minute expiry. The PRD claims 15 minutes.
        30 minutes is a reasonable default for clinical sessions.
        PHI access tokens should not have multi-hour lifetimes.
        """
        import pacca.api.auth as auth_module

        # Reset to default by unsetting env var
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TOKEN_EXPIRE_MINUTES", None)
            import importlib

            importlib.reload(auth_module)
            default_expiry = auth_module.TOKEN_EXPIRE_MINUTES

        assert default_expiry <= 30, (
            f"Default TOKEN_EXPIRE_MINUTES is {default_expiry}. "
            f"Must be <= 30 minutes for HIPAA-conscious PHI access. "
            f"The original 60-minute default is too long."
        )


# =============================================================================
# Async session consolidation
# =============================================================================


class TestAsyncSessionConsolidation:
    """
    Verify that main.py auth routes use async database sessions.

    The critical property: no route handler should import or use
    SessionLocal (the sync session from api/database.py) directly.
    """

    def test_main_login_route_uses_async_session(self):
        """
        The /login route must use AsyncSession from db/session.py,
        not the sync SessionLocal from api/database.py.

        We verify this by inspecting the login function's type annotations
        and dependency declarations.
        """
        import inspect

        import pacca.api.main as main_module

        source = inspect.getsource(main_module.login)

        # The login route must use get_session (async), not get_db (sync)
        assert "get_session" in source, (
            "login() must use 'get_session' (async) not 'get_db' (sync). "
            "Using get_db blocks the async event loop during database queries."
        )
        assert "get_db" not in source, (
            "login() must not use 'get_db' (sync session). "
            "get_db uses SessionLocal which blocks the event loop."
        )

    def test_main_register_route_uses_async_session(self):
        """The /register route must also use the async session."""
        import inspect

        import pacca.api.main as main_module

        source = inspect.getsource(main_module.register_user)
        assert "get_session" in source
        assert "get_db" not in source

    def test_main_does_not_define_sync_get_db(self):
        """
        main.py must not define a sync get_db() function.

        The legacy get_db() yielded a sync SessionLocal.
        If it is still defined, routes might accidentally depend on it.
        """
        import pacca.api.main as main_module

        assert not hasattr(main_module, "get_db"), (
            "main.py still defines get_db() (sync session). "
            "This function should be removed to prevent accidental use. "
            "All session management should use get_session() from db/session.py."
        )

    def test_login_route_uses_sqlalchemy_select_not_dot_query(self):
        """
        Async SQLAlchemy uses session.execute(select(...)), not session.query(...).

        session.query() is the sync ORM API and does not work with AsyncSession.
        Verify the login route uses the async pattern.
        """
        import inspect

        import pacca.api.main as main_module

        source = inspect.getsource(main_module.login)

        # Async SQLAlchemy pattern: await session.execute(select(Model))
        assert "select(" in source, (
            "login() should use sqlalchemy.select() for async queries, "
            "not session.query() which is sync-only."
        )

    def test_no_sync_session_import_in_main(self):
        """
        main.py must not import SessionLocal for use in route handlers.
        It may import it for the startup table creation, but not for routes.
        """
        import inspect

        import pacca.api.main as main_module

        # Get the source of main.py
        inspect.getsource(main_module)

        # SessionLocal may appear in the import for Base.metadata.create_all
        # but must NOT appear inside route function bodies
        login_source = inspect.getsource(main_module.login)
        register_source = inspect.getsource(main_module.register_user)

        assert "SessionLocal" not in login_source, (
            "login() must not use SessionLocal (sync). Use get_session (async)."
        )
        assert "SessionLocal" not in register_source, (
            "register_user() must not use SessionLocal (sync). Use get_session (async)."
        )


# =============================================================================
# RAG Pipeline integration
# =============================================================================


class TestRAGPipelineIntegration:
    """
    Verify GuidelineRetriever delegates to RAGPipeline.
    """

    def test_guideline_retriever_attempts_rag_pipeline(self):
        """
        GuidelineRetriever.query() must route through RAGPipeline.

        The pipeline handle now lives on the instance (self._pipeline) rather
        than a module singleton, so the mock is attached there.
        """
        from pacca.integrations import vector_store as vs_module

        mock_pipeline = MagicMock()
        mock_pipeline.retrieve_relevant_guidelines_sync.return_value = (
            "NCCN: Pembrolizumab recommended for PD-L1 >= 50%."
        )

        retriever = vs_module.GuidelineRetriever.__new__(vs_module.GuidelineRetriever)
        # Patch the chromadb client to avoid filesystem access
        retriever._client = MagicMock()
        retriever._embedding_fn = MagicMock()
        retriever._guidelines = MagicMock()
        retriever._precedents = MagicMock()
        retriever._precedents.query.return_value = {"documents": [[]], "metadatas": [[]]}
        retriever._pipeline = mock_pipeline

        result = retriever.query("Guidelines for C34.1 and J9271")

        # The pipeline was actually invoked — not silently skipped. The old
        # assertion allowed `len(result) > 0`, which the fallback also satisfies,
        # so it passed for months while the pipeline never ran at all.
        mock_pipeline.retrieve_relevant_guidelines_sync.assert_called_once()
        assert "Pembrolizumab" in result
        # The direct-ChromaDB fallback must NOT have been used.
        retriever._guidelines.query.assert_not_called()

    def test_query_parses_diagnosis_and_procedure_from_route_query(self):
        """The route's 'Guidelines for {dx} and {cpt}' string is split into codes."""
        from pacca.integrations import vector_store as vs_module

        mock_pipeline = MagicMock()
        mock_pipeline.retrieve_relevant_guidelines_sync.return_value = "guidelines"

        retriever = vs_module.GuidelineRetriever.__new__(vs_module.GuidelineRetriever)
        retriever._client = MagicMock()
        retriever._embedding_fn = MagicMock()
        retriever._guidelines = MagicMock()
        retriever._precedents = MagicMock()
        retriever._precedents.query.return_value = {"documents": [[]], "metadatas": [[]]}
        retriever._pipeline = mock_pipeline

        retriever.query("Guidelines for C34.1 and J9271")

        kwargs = mock_pipeline.retrieve_relevant_guidelines_sync.call_args.kwargs
        assert kwargs["diagnosis_code"] == "C34.1"
        assert kwargs["treatment_code"] == "J9271"
        # No fabricated "general" category — it filtered out every document and
        # forced a wasted unfiltered retry on every single query.
        assert kwargs.get("treatment_category") is None

    def test_guideline_retriever_falls_back_when_pipeline_unavailable(self):
        """
        When RAGPipeline is unavailable, query() degrades to direct ChromaDB
        queries without raising.
        """
        from pacca.integrations import vector_store as vs_module

        retriever = vs_module.GuidelineRetriever.__new__(vs_module.GuidelineRetriever)
        retriever._client = MagicMock()
        retriever._embedding_fn = MagicMock()
        retriever._guidelines = MagicMock()
        retriever._guidelines.query.return_value = {
            "documents": [["NCCN guideline text"]],
            "metadatas": [[{"source": "NCCN"}]],
        }
        retriever._precedents = MagicMock()
        retriever._precedents.query.return_value = {
            "documents": [[]],
            "metadatas": [[]],
        }
        retriever._pipeline = None

        assert retriever.pipeline_available is False

        result = retriever.query("Guidelines for C34.1 and J9271")

        assert "OFFICIAL GUIDELINES" in result
        assert "NCCN guideline text" in result

    def test_guideline_retriever_falls_back_when_pipeline_raises(self):
        """A pipeline error must degrade to the direct path, not surface to the caller."""
        from pacca.integrations import vector_store as vs_module

        mock_pipeline = MagicMock()
        mock_pipeline.retrieve_relevant_guidelines_sync.side_effect = RuntimeError("chroma down")

        retriever = vs_module.GuidelineRetriever.__new__(vs_module.GuidelineRetriever)
        retriever._client = MagicMock()
        retriever._embedding_fn = MagicMock()
        retriever._guidelines = MagicMock()
        retriever._guidelines.query.return_value = {
            "documents": [["NCCN guideline text"]],
            "metadatas": [[{"source": "NCCN"}]],
        }
        retriever._precedents = MagicMock()
        retriever._precedents.query.return_value = {"documents": [[]], "metadatas": [[]]}
        retriever._pipeline = mock_pipeline

        result = retriever.query("Guidelines for C34.1 and J9271")

        assert "OFFICIAL GUIDELINES" in result
        assert "NCCN guideline text" in result

    def test_add_guideline_upserts_to_collection(self):
        """add_guideline() must call upsert on the guidelines collection."""
        from pacca.integrations import vector_store as vs_module

        retriever = vs_module.GuidelineRetriever.__new__(vs_module.GuidelineRetriever)
        retriever._client = MagicMock()
        retriever._embedding_fn = MagicMock()
        retriever._guidelines = MagicMock()
        retriever._precedents = MagicMock()

        retriever.add_guideline(
            guideline_text="NCCN: Pembrolizumab recommended.",
            source_id="NCCN-NSCLC-001",
            metadata={"source": "AI_EVOLUTION_APPROVED"},
        )

        retriever._guidelines.upsert.assert_called_once()
        call_kwargs = retriever._guidelines.upsert.call_args
        assert "NCCN-NSCLC-001" in str(call_kwargs)

    def test_add_precedent_adds_to_precedents_collection(self):
        """add_precedent() must add to the case_precedents collection."""
        from pacca.integrations import vector_store as vs_module

        retriever = vs_module.GuidelineRetriever.__new__(vs_module.GuidelineRetriever)
        retriever._client = MagicMock()
        retriever._embedding_fn = MagicMock()
        retriever._guidelines = MagicMock()
        retriever._precedents = MagicMock()

        retriever.add_precedent(
            case_summary="MRI spine with foot drop, 3 weeks symptoms",
            rationale="Neurological emergency overrides 6-week rule",
            outcome="AUTO_APPROVED",
        )

        # chg-14: upsert (not add) with a deterministic content id, so the same
        # override is idempotent instead of colliding/duplicating.
        retriever._precedents.upsert.assert_called_once()
        call_args = retriever._precedents.upsert.call_args
        # The document should contain the scenario details
        documents = call_args.kwargs.get("documents", call_args.args[0] if call_args.args else [])
        doc_text = str(documents)
        assert "foot drop" in doc_text or "AUTO_APPROVED" in doc_text
        # Deterministic content id (not a per-process hash).
        ids = call_args.kwargs.get("ids", [])
        assert ids and ids[0].startswith("prec_")


# =============================================================================
# End-to-end security smoke test
# =============================================================================


class TestPasswordSecurity:
    """Verify password hashing properties."""

    def test_identical_passwords_produce_different_hashes(self):
        """
        bcrypt must produce a different hash each time due to random salting.
        This prevents rainbow table attacks.
        """
        from pacca.api.auth import get_password_hash

        hash1 = get_password_hash("same_password")
        hash2 = get_password_hash("same_password")

        assert hash1 != hash2, (
            "Two hashes of the same password should differ (bcrypt random salt). "
            "If they're identical, bcrypt is not generating a new salt each time."
        )

    def test_verify_password_returns_true_for_correct_password(self):
        """verify_password() must return True for the correct plaintext."""
        from pacca.api.auth import get_password_hash, verify_password

        password = "correct_horse_battery_staple"
        hashed = get_password_hash(password)

        assert verify_password(password, hashed) is True

    def test_verify_password_returns_false_for_wrong_password(self):
        """verify_password() must return False for an incorrect password."""
        from pacca.api.auth import get_password_hash, verify_password

        hashed = get_password_hash("correct_password")
        assert verify_password("wrong_password", hashed) is False

    def test_verify_password_returns_false_for_malformed_hash(self):
        """verify_password() must not raise on a malformed hash."""
        from pacca.api.auth import verify_password

        # Should return False, not raise ValueError
        result = verify_password("any_password", "not_a_valid_bcrypt_hash")
        assert result is False
