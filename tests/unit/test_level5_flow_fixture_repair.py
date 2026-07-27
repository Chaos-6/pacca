"""
Deterministic regression test for `tests.test_level5_flow._ensure_users_role_column`.

tests/test_level5_flow.py is entirely `pytest.mark.clinical` (real Claude
calls; requires ANTHROPIC_API_KEY; skipped by `make test`), so a bug in its
`client` fixture's schema-repair step would otherwise go undetected by the
deterministic suite until someone happened to run `make test-clinical`
against a pre-006 local database.

The finding this pins: `AuthBase.metadata.create_all(sync_engine)` cannot
ALTER an already-existing `users` table to add the `role` column migration
006 introduced (`checkfirst` only skips tables that already exist wholesale
— it never diffs columns). Against a real pre-006 `users` table this
crashed the fixture with `OperationalError: no such column: users.role` on
the very next SELECT, erroring out every test in the module — including
GC-018/GC-019, the anti-hallucination safety invariants.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import create_engine, inspect, text

from tests.test_level5_flow import _ensure_users_role_column

if TYPE_CHECKING:
    from pathlib import Path

_PRE_006_USERS_DDL = text(
    "CREATE TABLE users (id INTEGER PRIMARY KEY, username VARCHAR, hashed_password VARCHAR)"
)


def test_repairs_a_pre_006_users_table_missing_the_role_column(tmp_path: Path) -> None:
    """A `users` table built before migration 006 (no `role` column at all)."""
    db_path = tmp_path / "pre_006.db"
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            conn.execute(_PRE_006_USERS_DDL)
        before = {c["name"] for c in inspect(engine).get_columns("users")}
        assert "role" not in before, "test setup did not reproduce the pre-006 shape"

        _ensure_users_role_column(engine)

        after = {c["name"] for c in inspect(engine).get_columns("users")}
        assert "role" in after, "repair did not add the role column"

        # The exact failure mode this pins: a SELECT * against users must
        # now succeed instead of raising "no such column: users.role".
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO users (username, hashed_password) VALUES ('x', 'y')"))
            row = conn.execute(
                text("SELECT id, username, hashed_password, role FROM users")
            ).first()
        assert row is not None
        assert row.role == "clinician", "existing rows must backfill to the least-privileged role"
    finally:
        engine.dispose()


def test_is_a_no_op_against_a_table_that_already_has_the_role_column(tmp_path: Path) -> None:
    """A fresh table built by the CURRENT model already has `role` — no-op, no crash."""
    db_path = tmp_path / "fresh.db"
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE users (id INTEGER PRIMARY KEY, username VARCHAR, "
                    "hashed_password VARCHAR, role VARCHAR(30) NOT NULL DEFAULT 'clinician')"
                )
            )
        before = {c["name"] for c in inspect(engine).get_columns("users")}

        _ensure_users_role_column(engine)  # must not raise "duplicate column"

        after = {c["name"] for c in inspect(engine).get_columns("users")}
        assert after == before
    finally:
        engine.dispose()


def test_is_idempotent_when_called_twice_against_a_repaired_table(tmp_path: Path) -> None:
    """Calling it again after a repair (e.g. module-scoped fixture re-entry) must not error."""
    db_path = tmp_path / "repaired_twice.db"
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            conn.execute(_PRE_006_USERS_DDL)
        _ensure_users_role_column(engine)
        _ensure_users_role_column(
            engine
        )  # second call — must be a no-op, not a duplicate-column error
    finally:
        engine.dispose()
