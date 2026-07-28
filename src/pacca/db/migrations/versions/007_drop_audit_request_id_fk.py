"""Drop audit_logs.request_id foreign key to authorization_requests (chg-23)

Supersedes migration 003 (`003_deferrable_audit_fk`, B3). 003 made
`audit_logs.request_id`'s FK DEFERRABLE INITIALLY DEFERRED on Postgres so the
deferred check would pass at COMMIT — which worked ONLY because, at the time,
the audit write and the parent `authorization_requests` row shared a single
transaction/commit (the request's own session): the submit route writes
`intent.declared` / `authorization_submitted` (both request_id-bearing) BEFORE
the parent row is created, and the deferred check doesn't run until that
shared commit, by which point the parent row (flushed mid-transaction) exists.

chg-23 makes `AuditRepository.log()` commit independently of the caller's
transaction (see `db/repository.py` / `db/session.get_independent_session`),
specifically so a business-transaction rollback can no longer erase an
already-logged audit row. That breaks 003's shared-commit assumption: the
deferred check now runs at the audit write's OWN, immediate commit, before the
parent row has committed — which is the common case for those same two
pre-write events. Verified against a real Postgres 16 container running the
production-shaped submit path: with the FK still in place, 0 of 7 audit rows
persisted per request (the deferred check rejected every one).

The structural conflict: pre-write auditing requires the audit row to exist
BEFORE the business row; a foreign key requires the parent to exist FIRST.
Deferral reconciled those only as long as both writes shared one transaction.
Once the audit write is independent, deferral buys nothing — the retry/unlink
path considered as a stopgap is not a rare fallback, it is every request.

More fundamentally: an append-only, HIPAA-relevant audit table should not have
its rows' survival depend on referential integrity to a mutable business
table it exists to outlive — including the case where the business row never
lands at all (the exact failure this audit trail is supposed to capture).

This migration drops the FK constraint entirely. The `request_id` column and
its index (`ix_audit_logs_request_id`) are UNCHANGED — request_id remains a
plain, queryable, indexed value; correlation_id (never FK'd) remains the
join key guaranteed to tie every record of one run together.

Dialect handling: migration 003 was Postgres-only (deferrable constraints are
a Postgres feature; SQLite never enforced this FK regardless of the DDL). This
migration removes the constraint on BOTH dialects, because the ORM model
(`AuditLogModel.request_id`, `db/models.py`) drops `ForeignKey(...)` outright
— a single Python definition, not dialect-conditional — so a SQLite DB built
via `alembic upgrade head` must match it too (the `migration-drift` CI guard
compares models to migrations on Postgres, but keeping SQLite's migration
history internally consistent with the model is still the right invariant).

SQLite has no `ALTER TABLE ... DROP CONSTRAINT`; `batch_alter_table` rebuilds
the table (the standard Alembic SQLite technique, also used in migration
002). The target/source table shapes below are spelled out explicitly (not
imported from `pacca.db.models`) so this migration stays a self-contained,
historically-accurate snapshot independent of later model changes — matching
migration 001's inline `sa.Table`/`op.create_table` style.

DOWNGRADE IS A ONE-WAY DOOR ONCE ROLLBACK-SURVIVAL HAS BEEN EXERCISED — READ
BEFORE RUNNING IT DURING AN INCIDENT.

The whole point of chg-23 is that an audit row can now survive its business
transaction rolling back — which means it is EXPECTED, by design, to sometimes
carry a `request_id` for which no `authorization_requests` row exists (the
request never landed). That is not a bug; it is the durability guarantee
working. But it means `downgrade()`, which re-adds the FK, will encounter
exactly the orphans that guarantee produces:

  * Postgres: `downgrade()` fails on the FIRST such orphan with
    `IntegrityError ... Key (request_id)=(...) is not present in table
    "authorization_requests"`. This fails SAFE — Postgres DDL is
    transactional, so the failed `create_foreign_key` rolls back and
    `alembic_version` stays at `007_drop_audit_request_id_fk`, not
    half-applied — but it gives no cleanup guidance on its own. Before
    downgrading a Postgres DB that has been in service since this migration
    landed, find and clear (or NULL out) the orphans first:

        SELECT a.id, a.request_id, a.action, a.timestamp
        FROM audit_logs a
        LEFT JOIN authorization_requests r ON a.request_id = r.request_id
        WHERE a.request_id IS NOT NULL AND r.request_id IS NULL;

    then either delete those rows (NOT recommended — they're the audit
    trail of the requests that failed) or `UPDATE audit_logs SET
    request_id = NULL WHERE id IN (...)` for the orphaned ids only, THEN
    downgrade.

  * SQLite: `downgrade()` does NOT fail on the same orphans — SQLite never
    enforces the FK it accepts into the schema (that's the entire reason
    migration 003 was Postgres-only), so `batch_alter_table`'s table-rebuild
    copy silently ACCEPTS orphaned request_id values that would reject on
    Postgres. The two dialects diverge here: a downgrade that fails loudly
    on Postgres succeeds quietly on SQLite with the same data. Don't infer
    downgrade safety on Postgres from a clean SQLite downgrade.

Revision ID: 007_drop_audit_request_id_fk
Revises: 006_add_user_role
Create Date: 2026-07-28

"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "007_drop_audit_request_id_fk"
down_revision: str | None = "006_add_user_role"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Postgres auto-generated name for the FK created (unnamed) in migration 001,
# reused/re-created by migration 003 under this same name.
_FK = "audit_logs_request_id_fkey"


def _columns() -> list[sa.Column[Any]]:
    """The audit_logs column list, exactly as migration 001 defined it —
    shared by both the FK and no-FK Table shapes below so the two can only
    differ in the constraint, never drift on a column definition."""
    return [
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("entry_id", sa.String(50), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("request_id", sa.String(50), nullable=True),
        sa.Column("decision_id", sa.String(50), nullable=True),
        sa.Column("correlation_id", sa.String(50), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("actor_type", sa.String(30), nullable=False),
        sa.Column(
            "details", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=True
        ),
        sa.Column("input_summary", sa.Text(), nullable=True),
        sa.Column("output_summary", sa.Text(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column(
            "token_usage", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=True
        ),
    ]


def _table_without_fk() -> sa.Table:
    return sa.Table(
        "audit_logs",
        sa.MetaData(),
        *_columns(),
        sa.PrimaryKeyConstraint("id"),
    )


def _table_with_fk() -> sa.Table:
    return sa.Table(
        "audit_logs",
        sa.MetaData(),
        *_columns(),
        sa.PrimaryKeyConstraint("id"),
        # Named explicitly (migration 001 left this unnamed) so batch mode's
        # drop_constraint(_FK, ...) below has a name to match against — batch
        # mode operates against the `copy_from` Table object we hand it, not
        # against whatever SQLite's own catalog happens to call the FK.
        sa.ForeignKeyConstraint(["request_id"], ["authorization_requests.request_id"], name=_FK),
    )


def _reindex() -> None:
    """(Re)create the audit_logs indexes. batch_alter_table's table rebuild
    does not reliably carry indexes across on every SQLite/Alembic version,
    so both migration directions re-assert them explicitly rather than
    depend on that; op.create_index is a no-op error only if the index
    already exists, which it won't right after a table rebuild."""
    op.create_index("ix_audit_logs_entry_id", "audit_logs", ["entry_id"], unique=True)
    op.create_index("ix_audit_logs_timestamp", "audit_logs", ["timestamp"])
    op.create_index("ix_audit_logs_request_id", "audit_logs", ["request_id"])
    op.create_index("ix_audit_logs_correlation_id", "audit_logs", ["correlation_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.drop_constraint(_FK, "audit_logs", type_="foreignkey")
    else:
        with op.batch_alter_table(
            "audit_logs", recreate="always", copy_from=_table_with_fk()
        ) as batch_op:
            batch_op.drop_constraint(_FK, type_="foreignkey")
        _reindex()


def downgrade() -> None:
    """ONE-WAY DOOR once rollback-survival has fired in production — see the
    module docstring for the full explanation, the exact orphan-finding
    query, and the SQLite/Postgres asymmetry (SQLite accepts the same
    orphans this rejects on Postgres). Short version: this re-adds the
    request_id FK, and any audit row whose business transaction rolled back
    (the durability guarantee working as intended) now has an orphaned
    request_id that will raise IntegrityError HERE on Postgres — fails safe
    (transactional DDL, no half-applied state) but with no cleanup
    guidance unless you've read the module docstring first. Clear/NULL the
    orphans (query above) before downgrading a Postgres DB that has been
    live since this migration.
    """
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Restore the migration-003 shape: DEFERRABLE INITIALLY DEFERRED.
        op.create_foreign_key(
            _FK,
            "audit_logs",
            "authorization_requests",
            ["request_id"],
            ["request_id"],
            deferrable=True,
            initially="DEFERRED",
        )
    else:
        with op.batch_alter_table(
            "audit_logs", recreate="always", copy_from=_table_without_fk()
        ) as batch_op:
            batch_op.create_foreign_key(
                _FK,
                "authorization_requests",
                ["request_id"],
                ["request_id"],
            )
        _reindex()
