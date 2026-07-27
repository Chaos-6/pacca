"""Regression: the auth `users` table lives in the SAME database the
/register and /login handlers query — not a separate, hardcoded sync SQLite
engine — AND is schema-managed by Alembic, not `create_all` (C5).

History, part 1 (fixed earlier): main.py used to create `users` via
api/database.py's sync SQLite engine. In local SQLite dev both engines
pointed at ./pacca.db so it worked, but under Postgres they diverged —
`users` was created in SQLite while the handlers queried Postgres, so
/register and /login 500'd with 'relation "users" does not exist'. The fix
created the table via the async engine (get_engine) at startup, via
`Base.metadata.create_all` in the lifespan.

History, part 2 (this change, C5): that `create_all` call was itself the
last place PACCA built schema at runtime. `users` sat on a SEPARATE
declarative Base (pacca.api.database.Base) from the domain tables
(pacca.db.models.Base), so it was invisible to Alembic — the migration
files existed but nothing ever applied them, and `create_all` silently
papered over the gap. The fix: env.py now combines metadata from BOTH
Bases (`target_metadata = [_DomainBase.metadata, _AuthBase.metadata]`), a
new migration (004_create_users) creates `users`, and `create_all` is
gone from both the lifespan and `init_database()` — docker-entrypoint.sh /
`make db-upgrade` run `alembic upgrade head` before the app starts instead.

Mechanism change this test asserts: create_all (app-runtime schema
creation) -> Alembic migrations (deploy-time schema creation). The net
assertion strength is >= the original: the old positive assertion
("lifespan creates users via create_all") is REPLACED by two stronger
ones — (a) the lifespan no longer builds schema at all (create_all is
gone, not just "not on a sync engine"), and (b) `users` is independently
provable to be migration-covered, which no assertion here achieved before.
"""

import inspect
import re
from pathlib import Path

MIGRATIONS_VERSIONS_DIR = (
    Path(__file__).resolve().parents[2] / "src" / "pacca" / "db" / "migrations" / "versions"
)


def test_users_table_created_in_same_async_db_not_separate_sqlite() -> None:
    import pacca.api.database as db_module
    import pacca.api.main as main_module
    import pacca.db.session as session_module

    lifespan_src = inspect.getsource(main_module.lifespan)
    init_database_src = inspect.getsource(session_module.init_database)

    # The app no longer creates schema at runtime anywhere (C5 — Alembic
    # migrations are the single source of truth). This is STRONGER than the
    # pre-C5 assertion ("lifespan must create_all"): now neither the lifespan
    # nor init_database() may call create_all at all.
    assert "create_all" not in lifespan_src, (
        "the lifespan must NOT build schema at runtime — schema is Alembic-"
        "managed (C5); `alembic upgrade head` runs before the app starts "
        "(docker-entrypoint.sh in containers, `make db-upgrade` for local dev)."
    )
    assert "create_all" not in init_database_src, (
        "init_database() must NOT call create_all — it only verifies/warms "
        "the engine. Schema creation belongs to Alembic migrations, not app "
        "startup."
    )
    assert "sync_engine" not in lifespan_src, (
        "users table access must NOT go through a legacy sync SQLite engine; "
        "the async engine (get_engine) is the only database access path so "
        "it lands in the configured database (Postgres in compose, SQLite "
        "in dev)."
    )
    # api/database.py must no longer define a standalone (SQLite) engine or sync
    # session — only the declarative Base remains.
    assert not hasattr(db_module, "engine"), (
        "api/database.py must not define a standalone sync SQLite engine."
    )
    assert not hasattr(db_module, "SessionLocal"), (
        "api/database.py must not define a sync SessionLocal."
    )


def test_users_table_is_migration_covered() -> None:
    """`users` must be provably covered by Alembic, not just app-runtime code.

    Two independent checks, either of which alone would satisfy the C5
    contract ("Alembic is the single source of truth for the schema,
    covering the users table"), asserted together for a strictly stronger
    net check than the pre-C5 test achieved (which never verified Alembic
    coverage at all — only the app-runtime mechanism):

      1. env.py combines metadata from BOTH declarative Bases (domain +
         auth) into `target_metadata`, and imports the User model so it is
         registered on the auth Base before that metadata is read. (env.py
         can't be imported directly in a unit test — `context.config` only
         resolves inside Alembic's own runtime — so this is a source-text
         check, consistent with this repo's style for statically-verifiable
         wiring; see e.g. test_security_and_scalability.py.)
      2. A migration file under versions/ actually creates the users table.
    """
    env_py_path = MIGRATIONS_VERSIONS_DIR.parent / "env.py"
    env_src = env_py_path.read_text()

    assert "pacca.api.models.user" in env_src, (
        "env.py must import pacca.api.models.user so the User model is "
        "registered on the auth Base before its metadata is read — "
        "otherwise `users` is invisible to autogenerate even if the Base "
        "itself is included."
    )
    assert re.search(r"target_metadata\s*=\s*\[", env_src), (
        "env.py's target_metadata must be a LIST combining BOTH Bases' "
        "metadata (domain + auth) — a single Base's metadata (the pre-C5 "
        "state) cannot see `users`, which lives on the other Base."
    )
    assert "pacca.db.models" in env_src and "pacca.api.database" in env_src, (
        "env.py must reference both the domain Base (pacca.db.models.Base) "
        "and the auth Base (pacca.api.database.Base) so target_metadata "
        "actually combines both."
    )

    # A migration file must actually create the table (not just be visible
    # to autogenerate in theory) — grep the versions dir for the DDL call.
    version_files = sorted(MIGRATIONS_VERSIONS_DIR.glob("*.py"))
    assert version_files, f"no migration files found under {MIGRATIONS_VERSIONS_DIR}"

    creates_users = any(
        re.search(r'create_table\(\s*["\']users["\']', path.read_text()) for path in version_files
    )
    assert creates_users, (
        "no migration under db/migrations/versions/ creates the `users` "
        "table — Alembic must own this table's DDL, not create_all."
    )
