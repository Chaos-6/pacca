"""
audit_logs.request_id has no foreign key (chg-23, migration 007) — was B3.

History (why this file used to assert the opposite): B3's original fix made
``audit_logs.request_id``'s FK ``DEFERRABLE INITIALLY DEFERRED`` on Postgres,
because the submit route flushes two ``request_id``-bearing audit rows
(``intent.declared``, ``authorization_submitted``) BEFORE the parent
``authorization_requests`` row exists, and a non-deferrable FK is checked at
statement time. Deferring the check to COMMIT worked ONLY because the audit
write and the parent row shared one transaction/commit.

chg-23 made ``AuditRepository.log()`` commit each audit row on an INDEPENDENT
session, specifically so a business-transaction rollback can no longer erase
an already-logged audit row (see ``db/repository.py`` and migration 007's
docstring for the full mechanism). That breaks the shared-commit assumption
B3 depended on: the deferred check now runs at the audit write's OWN,
immediate commit, before the parent row has committed — on EVERY request,
not just failures. Verified against a real Postgres 16 container running the
production-shaped submit path: with the FK still in place, 0 of 7 audit rows
persisted per request.

Migration 007 drops the FK entirely (column and index unchanged) rather than
retry-and-unlink around it: an append-only, HIPAA-relevant audit table should
not have its rows' survival depend on referential integrity to a mutable
business table it exists to outlive.

This test compiles the DDL under the Postgres dialect (no live Postgres
needed to check DDL shape — that verification is done separately, in
``tests/integration/test_submit_postgres.py::test_audit_request_id_has_no_fk_but_keeps_its_index``,
against a real migrated container) and asserts the FK clause is GONE while
the column and its index remain — a contract change, not a weakening of the
original B3 test.
"""

from __future__ import annotations

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from pacca.db.models import AuditLogModel


def _audit_ddl_postgres() -> str:
    return str(CreateTable(AuditLogModel.__table__).compile(dialect=postgresql.dialect()))


def test_audit_request_id_has_no_foreign_key() -> None:
    """Before (B3): this asserted 'REFERENCES authorization_requests' IS
    present with DEFERRABLE INITIALLY DEFERRED. After (chg-23): no FOREIGN
    KEY clause referencing authorization_requests exists at all — dropped by
    migration 007, which this DDL compilation reflects because it's built
    from `AuditLogModel.__table__`, the same model migration 007 keeps in
    sync with."""
    ddl = _audit_ddl_postgres()
    assert "REFERENCES authorization_requests" not in ddl, (
        "audit_logs.request_id still carries a foreign key to "
        "authorization_requests — migration 007 / chg-23 should have "
        "removed it (see that migration's docstring for why)"
    )
    assert "FOREIGN KEY" not in ddl, "audit_logs should carry no foreign key at all"


def test_audit_request_id_column_and_index_are_unchanged() -> None:
    """Dropping the FK must not take the column or its index down with it —
    request_id remains a plain, queryable, indexed value; only the
    referential-integrity constraint is gone."""
    table = AuditLogModel.__table__
    assert "request_id" in table.columns, "the request_id column vanished"
    column = table.columns["request_id"]
    assert column.type.length == 50
    assert column.nullable is True
    assert column.index is True, "request_id must remain indexed"
    assert not column.foreign_keys, "request_id must carry no ForeignKey at the model level"
