"""
Role-based access control (RBAC) for the PACCA API.

Kept separate from `auth.py` on purpose: `auth.py` owns JWT signing/verification
and password hashing (identity — "who is this token for?"). This module owns
authorization (permission — "what is this user allowed to do?"). The two are
different concerns and different failure modes; conflating them makes both
harder to reason about.

Role taxonomy (hierarchical by rank, not a flat set of permissions):

    clinician < medical_director < admin

A role at rank N is authorized for anything requiring rank <= N.

Why the database is the source of truth for role, not a JWT claim
-------------------------------------------------------------------
Tokens live for TOKEN_EXPIRE_MINUTES (30 minutes by default). If role were
embedded in the token at login, a demotion during a security incident
("revoke this compromised/rogue account's admin rights right now") would not
take effect until the token expired — up to half an hour of continued
elevated access. Looking up the role fresh from the database on every
guarded request means a demotion takes effect on the *next* request, at the
cost of one indexed lookup. A role claim in the token would also create a
second source of truth that could silently disagree with the database (e.g.
after an out-of-band promotion/demotion), which is worse than the extra
query. See test_rbac.py::test_forged_admin_claim_in_jwt_grants_no_elevation
for the adversarial case this rules out directly.

Fail-closed contract (mandatory, each condition has its own test)
-------------------------------------------------------------------
- Stored role string is not a valid `Role` member  -> 403, never coerced to
  a default.
- Stored role is NULL / empty                       -> 403.
- Role comparison is exact and case-sensitive: "Admin" is not Role.ADMIN.

An unparseable role is a DENIAL, not a demotion to some default role. Never
write `except Exception: return DEFAULT_ROLE` anywhere in this module.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, cast

from fastapi import Depends, HTTPException
from sqlalchemy import select

from ..db.session import get_session
from .auth import verify_token
from .models.user import User

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from sqlalchemy.ext.asyncio import AsyncSession


class Role(str, Enum):  # noqa: UP042 — design spec §3.1 pins this exact
    # form (not enum.StrEnum) for the role taxonomy; equivalent for our
    # purposes (value equality, .value access, JSON serialization).
    """The three PACCA roles, in ascending order of privilege."""

    CLINICIAN = "clinician"
    MEDICAL_DIRECTOR = "medical_director"
    ADMIN = "admin"


# Rank table backing the hierarchy. Higher number = more privilege.
_RANK: dict[Role, int] = {
    Role.CLINICIAN: 1,
    Role.MEDICAL_DIRECTOR: 2,
    Role.ADMIN: 3,
}

# What a newly registered account is assigned. Registration can never set a
# role other than this (see `UserCreate.model_config = ConfigDict(extra="forbid")`
# in api/main.py, which rejects a "role" key in the request body outright).
DEFAULT_ROLE = Role.CLINICIAN

# The 403 body for an insufficient role never echoes the caller's actual
# role — that would leak information about the account to a caller who is,
# by definition, not authorized to see it.
_INSUFFICIENT_ROLE_DETAIL = "Insufficient role for this operation"


def parse_role(value: str | None) -> Role | None:
    """
    Parse a stored role string into a `Role`, or `None` if it cannot be trusted.

    Fail-closed: returns `None` (never a default) for `NULL`/empty, an
    unrecognized string, or a wrong-case match ("Admin" != Role.ADMIN,
    because `Role` values are lowercase and Enum value-lookup is exact).
    Callers MUST treat `None` as "deny", never as "assume default role".
    """
    if not value:
        return None
    try:
        return Role(value)
    except ValueError:
        return None


def meets_minimum(role: Role | None, minimum: Role) -> bool:
    """Return whether `role` is at or above `minimum` in the rank table."""
    if role is None:
        return False
    return _RANK[role] >= _RANK[minimum]


async def get_current_user(
    username: str = Depends(verify_token),
    session: AsyncSession = Depends(get_session),
) -> User:
    """
    Resolve the authenticated JWT subject to the current database row.

    This is the ONE place a request's role becomes trustworthy: the JWT only
    proves *who the caller claims to be* (a username); this dependency proves
    *what that account currently is*, read fresh from the database on every
    call. A token can outlive the account it names (the user was deleted
    after the token was issued) — that is a 401, not a 403, because there is
    no account to authorize, valid or not.
    """
    result = await session.execute(select(User).where(User.username == username))
    # Explicit annotation (not inferred) so this stays `User | None` even in
    # an environment where sqlalchemy's own types aren't resolvable (e.g. the
    # pre-commit mypy hook's isolated dependency set) and scalar_one_or_none()
    # would otherwise be seen as returning `Any`.
    user: User | None = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_min_role(minimum: Role) -> Callable[..., Awaitable[User]]:
    """
    Dependency factory: build an async FastAPI dependency requiring `minimum`.

    Resolves `get_current_user`, parses the stored role string via
    `parse_role` (fail-closed — see module docstring), and raises 403 when
    the parsed role is missing or ranks below `minimum`. The 403 body never
    reveals the caller's actual role.
    """

    async def _dependency(user: User = Depends(get_current_user)) -> User:
        # cast(), not a `# type: ignore` comment: whether `user.role` is seen
        # as `Column[str]` (full dependency set, no sqlalchemy mypy plugin)
        # or as `Any` (an isolated environment where sqlalchemy itself is an
        # unresolved import — e.g. the pre-commit mypy hook), an ignore
        # comment would be "needed" in one of those and "unused" in the
        # other. cast is a no-op in both.
        role = parse_role(cast("str | None", user.role))
        if not meets_minimum(role, minimum):
            raise HTTPException(status_code=403, detail=_INSUFFICIENT_ROLE_DETAIL)
        return user

    return _dependency
