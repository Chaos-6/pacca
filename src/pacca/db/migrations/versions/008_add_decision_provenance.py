"""Add model_id and prompt_version to authorization_decisions (SCHEMA-INV-04)

Records which substrate produced each decision. SDD v3.0 requires that every
AuthorizationDecision carry the model identifier and prompt version that
produced it (SCHEMA-INV-04, THREAT-04, CHG-02), because the optimal harness is
model-specific and a vendor can change behaviour behind a pinned identifier.

Without these columns the question "which model decided this case" has no
answer in the data. docs/DECISIONS.md:136-140 records the situation this
prevents: a full accuracy evaluation run on a substituted model
(claude-sonnet-4-6, a floating alias), reconstructable afterwards only from
prose a human typed into a markdown file, while harness/manifests/iter-14.json
still declares the pinned model that did not run.

Backfill choice. Both columns are NOT NULL with
`server_default="unknown:pre-provenance"`. Rows written before this migration
genuinely have unknown provenance, and that is a different statement from
"produced by deterministic code" (which the application writes as
`none:deterministic`, see models.authorization.DETERMINISTIC_PROVENANCE) and
from NULL (which would force every reader to decide what absence means). A
distinct sentinel keeps the three cases separable in any later query, so a
drift investigation can exclude pre-provenance rows honestly rather than
silently counting them as deterministic.

`model_id` is indexed: "which decisions did model X produce" is the query a
model-change gate (CHG-03) and a drift investigation (DRIFT-DEF-05) both run.
`prompt_version` is not indexed — it is read alongside a decision, not
selected on.

Must match `pacca.db.models.AuthorizationDecisionModel` EXACTLY (type,
nullability, server_default, index) or the migration-drift CI job
(`alembic revision --autogenerate` producing a non-empty diff) fails.

Revision ID: 008_add_decision_provenance
Revises: 007_drop_audit_request_id_fk
Create Date: 2026-09-06

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "008_add_decision_provenance"
down_revision: str | None = "007_drop_audit_request_id_fk"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "authorization_decisions",
        sa.Column(
            "model_id",
            sa.String(64),
            nullable=False,
            server_default="unknown:pre-provenance",
        ),
    )
    op.add_column(
        "authorization_decisions",
        sa.Column(
            "prompt_version",
            sa.String(64),
            nullable=False,
            server_default="unknown:pre-provenance",
        ),
    )
    op.create_index(
        "ix_authorization_decisions_model_id",
        "authorization_decisions",
        ["model_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_authorization_decisions_model_id",
        table_name="authorization_decisions",
    )
    op.drop_column("authorization_decisions", "prompt_version")
    op.drop_column("authorization_decisions", "model_id")
