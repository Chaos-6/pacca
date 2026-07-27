"""Create users table (C5 — Alembic single-source-of-truth)

The `users` table (auth: username + bcrypt hash) has existed since early PACCA
but was invisible to Alembic — it is registered on a SEPARATE declarative Base
(pacca.api.database.Base) from the domain tables (pacca.db.models.Base), and
env.py only read the domain Base's metadata. Schema was instead built by
`create_all` at app startup across both Bases (see api/main.py's lifespan /
db/session.py's init_database). This migration is the first Alembic-tracked
definition of `users`, matching pacca.api.models.user.User exactly. Columns are
dialect-agnostic (Integer/String only — no Postgres-only types), so this runs
cleanly on both Postgres and SQLite.

Revision ID: 004_create_users
Revises: 003_deferrable_audit_fk
Create Date: 2026-07-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "004_create_users"
down_revision: str | None = "003_deferrable_audit_fk"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(), nullable=True),
        sa.Column("hashed_password", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_id", "users", ["id"])
    op.create_index("ix_users_username", "users", ["username"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_username", table_name="users")
    op.drop_index("ix_users_id", table_name="users")
    op.drop_table("users")
