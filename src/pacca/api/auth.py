"""
JWT authentication and password hashing for PACCA.

Security implementation notes:
  - SECRET_KEY is loaded from the environment at runtime — NEVER hardcoded.
    Generate a secure key with: python -c "import secrets; print(secrets.token_hex(32))"
    Set in .env for local development; use a secrets manager in production.

  - bcrypt is used directly (not passlib) for password hashing. bcrypt
    generates a per-password salt automatically and is resistant to
    GPU-based brute-force attacks.

  - JWT tokens expire after TOKEN_EXPIRE_MINUTES (default 30 minutes).
    30 minutes balances security (limits exposure window if a token is
    intercepted) with usability (typical clinical session length).
    Configurable via TOKEN_EXPIRE_MINUTES environment variable.

  - ALGORITHM is HS256 (HMAC-SHA256) — symmetric signing. For a
    multi-service architecture, RS256 (RSA) would be preferred.
    HS256 is appropriate for a single-service deployment.

HIPAA relevance:
  Authentication controls are required by HIPAA Security Rule 164.312(d)
  (Person or Entity Authentication). Token expiry limits the window during
  which a stolen token could be used to access PHI.
"""

import os

import bcrypt
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

# ── Security constants ────────────────────────────────────────────────────────

# SECRET_KEY must come from the environment.
# Empty string here is a deliberate fail-safe: if no key is set, JWT signing
# will fail immediately rather than silently using an insecure default.
# For local development, set SECRET_KEY in your .env file.
# Generate a secure key: python -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY: str = os.getenv("SECRET_KEY", "")

# HS256 = HMAC-SHA256 symmetric JWT signing.
ALGORITHM = "HS256"

# Token lifetime in minutes. Configurable via environment.
# Default: 30 minutes (shorter than the original 60 minutes,
# appropriate for a system handling PHI).
TOKEN_EXPIRE_MINUTES: int = int(os.getenv("TOKEN_EXPIRE_MINUTES", "30"))

# Tells FastAPI where to direct unauthenticated clients to log in.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/login/")


# ── Startup validation ────────────────────────────────────────────────────────


MIN_SECRET_KEY_LENGTH = 32

# Minimum distinct characters. A 32-char key of "aaaa..." satisfies the length
# rule while carrying almost no entropy.
MIN_SECRET_KEY_DISTINCT_CHARS = 8

# Substrings that identify a key as a shipped placeholder rather than a generated
# secret. Every one of these is published in this repo (.env.example, compose
# files, test fixtures), so a deployment using one has a *public* signing key
# that the length rule alone happily accepts. This is the gap the length-only
# check left: the placeholder in .env.example is 61 characters.
PLACEHOLDER_MARKERS = (
    "replace-this",
    "change-in-production",
    "changeme",
    "your-secret",
    "dev-secret-key",
    "test-secret-key",
    "insecure",
    "placeholder",
    "example",
)

# Environments where a placeholder key is a hard failure. development/test may
# use one — running the suite must not require a real secret — but they say so
# out loud (see _warn_placeholder) rather than passing silently.
STRICT_ENVIRONMENTS = ("production", "staging")

_GENERATE_HINT = (
    'Generate a secure key with: python -c "import secrets; print(secrets.token_hex(32))"'
)


def _placeholder_marker(key: str) -> str | None:
    """Return the placeholder substring found in `key`, or None."""
    lowered = key.lower()
    return next((marker for marker in PLACEHOLDER_MARKERS if marker in lowered), None)


def validate_secret_key(key: str | None = None, app_env: str | None = None) -> None:
    """
    Validate the JWT signing key at application startup.

    Runs in EVERY environment. It previously ran only when `app_env != "test"`,
    which meant `APP_ENV=test` in a production deployment skipped the check
    entirely — a bypass reachable by one environment variable. The environment
    now selects the *strictness*, never whether validation happens at all:

      production / staging — missing, short, low-entropy, or placeholder keys
                             all raise. Nothing publicly known can sign a token.
      development / test   — missing, short, and low-entropy still raise; a
                             placeholder is permitted and logged at warning.

    Args:
        key: Key to validate. Defaults to the module's SECRET_KEY (read from the
            environment at import). Injectable so tests need not mutate globals.
        app_env: Environment name. Defaults to the configured `app_env`.

    Raises:
        RuntimeError: If the key is unusable for the given environment.
    """
    key = SECRET_KEY if key is None else key

    if app_env is None:
        from pacca.config import get_settings

        app_env = get_settings().app_env

    if not key:
        raise RuntimeError(
            "SECRET_KEY environment variable is not set. "
            f"{_GENERATE_HINT} and set it in your .env file or deployment secrets manager."
        )

    if len(key) < MIN_SECRET_KEY_LENGTH:
        raise RuntimeError(
            f"SECRET_KEY is only {len(key)} characters. "
            f"Minimum {MIN_SECRET_KEY_LENGTH} characters required for HS256 security. "
            f"{_GENERATE_HINT}"
        )

    if len(set(key)) < MIN_SECRET_KEY_DISTINCT_CHARS:
        raise RuntimeError(
            f"SECRET_KEY has only {len(set(key))} distinct characters, which is too "
            f"little entropy regardless of its length. {_GENERATE_HINT}"
        )

    marker = _placeholder_marker(key)
    if marker is None:
        return

    if app_env in STRICT_ENVIRONMENTS:
        raise RuntimeError(
            f"SECRET_KEY looks like a shipped placeholder (it contains {marker!r}) "
            f"and APP_ENV is {app_env!r}. Placeholder keys are published in this "
            f"repository, so anyone can forge a token signed with one. "
            f"{_GENERATE_HINT}"
        )

    _warn_placeholder(marker, app_env)


def _warn_placeholder(marker: str, app_env: str) -> None:
    """Log that a placeholder key is in use — permitted here, fatal in production."""
    from pacca.config import get_logger

    get_logger(__name__).warning(
        "secret_key_is_placeholder",
        marker=marker,
        app_env=app_env,
        detail="permitted in this environment; production/staging startup would refuse",
    )


# ── Password hashing ──────────────────────────────────────────────────────────


def get_password_hash(password: str) -> str:
    """
    Hash a plaintext password using bcrypt.

    bcrypt generates a unique random salt for each hash automatically,
    so two hashes of the same password will differ. This prevents
    rainbow table attacks.

    Args:
        password: Plaintext password to hash

    Returns:
        bcrypt hash string suitable for database storage
    """
    pwd_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(pwd_bytes, salt)
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a plaintext password against a stored bcrypt hash.

    bcrypt's checkpw is timing-safe — it does not short-circuit on the
    first mismatched character, preventing timing side-channel attacks.

    Args:
        plain_password:  The password the user provided
        hashed_password: The hash stored in the database

    Returns:
        True if the password matches, False otherwise
    """
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            hashed_password.encode("utf-8"),
        )
    except ValueError:
        # Malformed hash — treat as mismatch
        return False


# ── JWT token validation ──────────────────────────────────────────────────────


def verify_token(token: str = Depends(oauth2_scheme)) -> str:
    """
    FastAPI dependency that validates a JWT Bearer token.

    Extracts the username from the token's 'sub' claim. Called automatically
    by FastAPI for any route that declares `Depends(verify_token)`.

    Args:
        token: JWT token from the Authorization: Bearer header (injected by FastAPI)

    Returns:
        The authenticated username

    Raises:
        HTTPException(401): If the token is missing, invalid, expired, or
                            has no 'sub' claim
    """
    credentials_exception = HTTPException(
        status_code=401,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str | None = payload.get("sub")
        if username is None:
            raise credentials_exception
        return username
    except JWTError:
        raise credentials_exception from None
