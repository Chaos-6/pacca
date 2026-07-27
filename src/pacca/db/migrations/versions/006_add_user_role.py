"""Add role column to users (RBAC)

Adds the `role` column backing PACCA's hierarchical RBAC (clinician <
medical_director < admin — see `pacca.api.rbac`). `server_default="clinician"`
backfills every pre-existing row to the least-privileged role rather than
leaving it NULL, which matters here specifically: the RBAC guard is
fail-closed on a NULL/unparseable role (403, never a default), so a bare
`ADD COLUMN role ... NULL` would lock every existing account out of every
guarded endpoint the moment this migration ran. The server-side default
means upgrade is a live, backward-compatible change for existing users.

Must match `pacca.api.models.user.User.role` EXACTLY (same type, nullability,
and server_default) or the migration-drift CI job (`alembic revision
--autogenerate` producing a non-empty diff) fails.

Revision ID: 006_add_user_role
Revises: 005_reconcile_index_drift
Create Date: 2026-07-27

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "006_add_user_role"
down_revision: str | None = "005_reconcile_index_drift"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("role", sa.String(30), nullable=False, server_default="clinician"),
    )


def downgrade() -> None:
    op.drop_column("users", "role")
