"""Reconcile pre-existing index-name drift with the ORM models (C5)

Discovered while building the C5 migration-drift CI guard (`alembic revision
--autogenerate` must produce an empty diff): three indexes that migration
001 hand-named do not match the names SQLAlchemy's default convention
(`ix_<table>_<column>`) generates from the current models, and one column
(`human_reviews.request_id`) has carried `index=True` in the model since it
was written but never got a matching migration. `create_all` masked this in
dev/test (it always builds from the models, so it never noticed the
migrations disagreed) — nothing behavioral changes, this only makes the
column-level indexing that migration-managed (production) DBs already
*should* have match what `create_all`-built dev/test DBs have always had.

Revision ID: 005_reconcile_index_drift
Revises: 004_create_users
Create Date: 2026-07-24

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "005_reconcile_index_drift"
down_revision: str | None = "004_create_users"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_authorization_requests_diagnosis", table_name="authorization_requests")
    op.drop_index("ix_authorization_requests_treatment", table_name="authorization_requests")
    op.create_index(
        "ix_authorization_requests_primary_diagnosis_code",
        "authorization_requests",
        ["primary_diagnosis_code"],
    )
    op.create_index(
        "ix_authorization_requests_treatment_code",
        "authorization_requests",
        ["treatment_code"],
    )
    op.create_index("ix_human_reviews_request_id", "human_reviews", ["request_id"])


def downgrade() -> None:
    op.drop_index("ix_human_reviews_request_id", table_name="human_reviews")
    op.drop_index("ix_authorization_requests_treatment_code", table_name="authorization_requests")
    op.drop_index(
        "ix_authorization_requests_primary_diagnosis_code", table_name="authorization_requests"
    )
    op.create_index(
        "ix_authorization_requests_treatment", "authorization_requests", ["treatment_code"]
    )
    op.create_index(
        "ix_authorization_requests_diagnosis", "authorization_requests", ["primary_diagnosis_code"]
    )
