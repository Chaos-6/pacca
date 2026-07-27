"""
PACCA developer-facing CLI.

This module is the entry point referenced by pyproject.toml:
    [project.scripts]
    pacca = "pacca.cli:main"

After `pip install -e .`, the `pacca` command is on PATH. Subcommands:

    pacca sme-author new       — interactive new-case workflow (LLM-drafted)
    pacca sme-author validate  — validate a CaseDraftResponse JSON file
    pacca create-admin         — create the first admin, or promote an
                                  existing user (RBAC — see api/rbac.py)
    pacca --help               — list all subcommands

The CLI is a thin Click router. Each subgroup lives in its own module
(currently: sme_authoring/cli_commands.py). To add a new subgroup, import
its `cli` Click group and `pacca_cli.add_command(...)` it below.
"""

from __future__ import annotations

import asyncio

import click
from sqlalchemy import select

from pacca.agents.sme_authoring.cli_commands import sme_author as sme_author_group


@click.group(name="pacca")
@click.version_option(version="2.4.0", prog_name="pacca")
def pacca_cli() -> None:
    """PACCA developer CLI. Subcommands organize workflows for engineers + SMEs."""


pacca_cli.add_command(sme_author_group, name="sme-author")


# =============================================================================
# create-admin — the ONLY way to mint an admin account (RBAC / api/rbac.py)
# =============================================================================
#
# Deliberately out-of-band: there is no HTTP path to create the first admin.
# `POST /register/` is forced to Role.CLINICIAN (see api/main.py's UserCreate
# / register_user), so bootstrapping the first admin — and any subsequent
# promotion done outside the audited PATCH /admin/users/{username}/role
# endpoint — goes through this CLI, which requires shell access to wherever
# the app is deployed.


def _assign_str(obj: object, field: str, value: str) -> None:
    """
    setattr with a dynamic field name.

    Not a direct `obj.field = value` assignment: without a SQLAlchemy mypy
    plugin, `existing.role = ...` is seen as `Column[str]` under the
    project's full dependency set but as `Any` in an isolated mypy
    environment missing sqlalchemy (e.g. the pre-commit hook) — a
    `# type: ignore[assignment]` would be "needed" in one and "unused" in
    the other. Routing the assignment through a helper with a plain `str`
    parameter sidesteps the mismatch in both. (Not `setattr(obj, "field",
    value)` directly — ruff's B010 flags a literal attribute name as
    equivalent to, and less safe than, normal attribute access; here the
    name is a parameter, not a literal, so the rule does not apply.)
    """
    setattr(obj, field, value)


async def _create_or_promote_admin(username: str, password: str) -> str:
    """
    Create `username` as an admin, or PROMOTE-ONLY if the account exists.

    Idempotent: running this twice for the same username is not an error —
    the second run simply re-promotes (a no-op role-wise).

    VALIDATOR FINDING, FIXED: this used to also overwrite an existing
    user's `hashed_password` with whatever the operator typed, unconditionally
    — an operator granting Alice admin would silently rotate her credential
    to a value the operator now knows, and Alice's own password would stop
    working. Design spec §3.3 only ever authorised "creates the user if
    absent; promotes to admin if present" — never a credential change on an
    existing account. The password this function receives is used ONLY to
    hash a NEW account; an existing account's `hashed_password` is never
    touched. (A password *reset* is a legitimate, separate operation — it
    should be its own command with its own name and its own audit story,
    not a side effect of a role grant.)

    Returns a human-readable summary line for the CLI to echo (never the
    password itself).
    """
    # Local imports: this CLI module is imported at package-import time by
    # pyproject's console-script entry point, and the DB/auth stack pulls in
    # heavier dependencies (SQLAlchemy engine, bcrypt) that a plain
    # `pacca sme-author --help` invocation shouldn't need to pay for.
    import getpass

    from pacca.api.auth import get_password_hash
    from pacca.api.models.user import User
    from pacca.api.rbac import Role
    from pacca.db.repository import AuditRepository
    from pacca.db.session import get_session_context

    async with get_session_context() as session:
        result = await session.execute(select(User).where(User.username == username))
        existing = result.scalar_one_or_none()

        # VALIDATOR FINDING, FIXED: this is the highest-privilege operation
        # in the system (it mints/grants `admin`, PACCA's top role) and it
        # previously wrote no audit trail at all — every other role change
        # (PATCH /admin/users/{username}/role) writes a `user.role_changed`
        # record, but this out-of-band CLI path was invisible. `actor` is
        # prefixed `cli:` (never a bare username) so an audit-log reader can
        # tell an out-of-band CLI grant apart from an in-app admin action at
        # a glance, and `details` records that provenance explicitly rather
        # than leaving it implicit in the actor string alone.
        #
        # Known limitation, not fixed here (queued as its own change): this
        # audit write shares the repo-wide defect where audit rows ride the
        # same transaction as the state change they describe, rather than
        # being flushed durably before it (the pre-write-audit pattern used
        # on the HTTP submit path in routes/authorizations.py). A process
        # crash between `session.add`/`_assign_str` and commit could in
        # theory lose both together. That durability gap is pre-existing
        # and out of scope for this fix.
        audit = AuditRepository(session)

        if existing is None:
            hashed = get_password_hash(password)
            session.add(User(username=username, hashed_password=hashed, role=Role.ADMIN.value))
            await audit.log(
                action="user.role_changed",
                actor=f"cli:{getpass.getuser()}",
                actor_type="user",
                details={
                    "target": username,
                    "from": None,
                    "to": Role.ADMIN.value,
                    "via": "cli_bootstrap",
                },
            )
            return f"Created new admin user '{username}'."

        previous_role = existing.role
        _assign_str(existing, "role", Role.ADMIN.value)
        await audit.log(
            action="user.role_changed",
            actor=f"cli:{getpass.getuser()}",
            actor_type="user",
            details={
                "target": username,
                "from": previous_role,
                "to": Role.ADMIN.value,
                "via": "cli_bootstrap",
            },
        )
        return (
            f"Promoted existing user '{username}' to admin. "
            "Password NOT changed — the existing credential still applies."
        )


@pacca_cli.command(name="create-admin")
@click.option("--username", required=True, help="Username for the admin account.")
@click.option(
    "--password",
    prompt=True,
    hide_input=True,
    confirmation_prompt=True,
    help="Password (omit to be prompted interactively; never echoed or logged).",
)
def create_admin(username: str, password: str) -> None:
    """
    Create an admin user, or promote an existing user to admin.

    This is the ONLY way to mint the first admin account — there is
    intentionally no HTTP registration path that can produce anything but a
    clinician (see `POST /api/v1/register/`). Run this once, out-of-band,
    against the deployed database; subsequent role changes should go through
    the audited `PATCH /api/v1/admin/users/{username}/role` endpoint instead.
    """
    summary = asyncio.run(_create_or_promote_admin(username, password))
    click.echo(summary)


def main() -> None:
    """Console-script entry point used by pyproject.toml."""
    pacca_cli()


if __name__ == "__main__":
    main()
