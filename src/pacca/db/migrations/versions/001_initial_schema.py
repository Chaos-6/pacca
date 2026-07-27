"""Initial schema creation

JSON columns use `sa.JSON().with_variant(postgresql.JSONB(), "postgresql")`
(the same dialect-agnostic pattern the ORM models use — see PR #19 / B2),
not a bare `postgresql.JSONB()`. Bare JSONB doesn't compile on SQLite at
all, which silently blocked `alembic upgrade head` on every backend but
Postgres (uncovered while closing C5's migration-drift CI guard). This is
schema-identical on Postgres (still JSONB there) and only changes what
SQLite gets (JSON/TEXT instead of a compile error).

Revision ID: 001_initial
Revises:
Create Date: 2026-02-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Authorization requests table
    op.create_table(
        "authorization_requests",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("request_id", sa.String(50), nullable=False),
        sa.Column("external_reference", sa.String(100), nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("patient_id", sa.String(50), nullable=False),
        sa.Column("patient_age", sa.Integer(), nullable=False),
        sa.Column("patient_gender", sa.String(10), nullable=False),
        sa.Column("primary_diagnosis_code", sa.String(20), nullable=False),
        sa.Column("primary_diagnosis_description", sa.Text(), nullable=False),
        sa.Column(
            "secondary_diagnoses",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=True,
        ),
        sa.Column("treatment_code", sa.String(20), nullable=False),
        sa.Column("treatment_description", sa.Text(), nullable=False),
        sa.Column("treatment_category", sa.String(30), nullable=False),
        sa.Column("estimated_cost", sa.Float(), nullable=True),
        sa.Column("provider_id", sa.String(50), nullable=False),
        sa.Column("provider_name", sa.String(200), nullable=False),
        sa.Column("payer_id", sa.String(50), nullable=False),
        sa.Column("payer_name", sa.String(200), nullable=False),
        sa.Column("member_id", sa.String(50), nullable=False),
        sa.Column("clinical_notes", sa.Text(), nullable=True),
        sa.Column("urgency", sa.String(20), nullable=False),
        sa.Column("complexity", sa.Integer(), nullable=True),
        sa.Column("assigned_specialty", sa.String(50), nullable=True),
        sa.Column("evidence_quality", sa.String(20), nullable=True),
        sa.Column(
            "evidence_data", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=True
        ),
        sa.Column(
            "narrative_data",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=True,
        ),
        sa.Column("submitted_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_authorization_requests_request_id",
        "authorization_requests",
        ["request_id"],
        unique=True,
    )
    op.create_index("ix_authorization_requests_status", "authorization_requests", ["status"])
    op.create_index(
        "ix_authorization_requests_patient_id", "authorization_requests", ["patient_id"]
    )
    op.create_index("ix_authorization_requests_payer_id", "authorization_requests", ["payer_id"])
    op.create_index(
        "ix_authorization_requests_submitted_at", "authorization_requests", ["submitted_at"]
    )
    op.create_index(
        "ix_authorization_requests_diagnosis", "authorization_requests", ["primary_diagnosis_code"]
    )
    op.create_index(
        "ix_authorization_requests_treatment", "authorization_requests", ["treatment_code"]
    )

    # Authorization decisions table
    op.create_table(
        "authorization_decisions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("decision_id", sa.String(50), nullable=False),
        sa.Column("request_id", sa.String(50), nullable=False),
        sa.Column("outcome", sa.String(30), nullable=False),
        sa.Column("confidence_score", sa.Float(), nullable=False),
        sa.Column(
            "rationale_data",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "conditions", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=True
        ),
        sa.Column(
            "required_actions",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=True,
        ),
        sa.Column("effective_date", sa.DateTime(), nullable=True),
        sa.Column("expiration_date", sa.DateTime(), nullable=True),
        sa.Column("authorized_quantity", sa.Integer(), nullable=True),
        sa.Column("authorized_duration_days", sa.Integer(), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=False),
        sa.Column("decided_by", sa.String(100), nullable=False),
        sa.Column("is_autonomous", sa.Boolean(), nullable=False),
        sa.Column("was_escalated", sa.Boolean(), nullable=False),
        sa.Column(
            "escalation_reasons",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=True,
        ),
        sa.Column("total_tokens_used", sa.Integer(), nullable=True),
        sa.Column("processing_time_ms", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["request_id"], ["authorization_requests.request_id"]),
    )
    op.create_index(
        "ix_authorization_decisions_decision_id",
        "authorization_decisions",
        ["decision_id"],
        unique=True,
    )
    op.create_index(
        "ix_authorization_decisions_request_id", "authorization_decisions", ["request_id"]
    )
    op.create_index("ix_authorization_decisions_outcome", "authorization_decisions", ["outcome"])

    # Human reviews table
    op.create_table(
        "human_reviews",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("review_id", sa.String(50), nullable=False),
        sa.Column("decision_id", sa.String(50), nullable=False),
        sa.Column("request_id", sa.String(50), nullable=False),
        sa.Column("reviewer_id", sa.String(50), nullable=False),
        sa.Column("reviewer_role", sa.String(30), nullable=False),
        sa.Column("original_outcome", sa.String(30), nullable=False),
        sa.Column("final_outcome", sa.String(30), nullable=False),
        sa.Column("outcome_changed", sa.Boolean(), nullable=False),
        sa.Column("agrees_with_rationale", sa.Boolean(), nullable=False),
        sa.Column("reviewer_notes", sa.Text(), nullable=True),
        sa.Column("additional_rationale", sa.Text(), nullable=True),
        sa.Column("ai_accuracy_rating", sa.Integer(), nullable=True),
        sa.Column(
            "feedback_tags", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=True
        ),
        sa.Column("reviewed_at", sa.DateTime(), nullable=False),
        sa.Column("time_spent_seconds", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["decision_id"], ["authorization_decisions.decision_id"]),
    )
    op.create_index("ix_human_reviews_review_id", "human_reviews", ["review_id"], unique=True)
    op.create_index("ix_human_reviews_decision_id", "human_reviews", ["decision_id"])
    op.create_index("ix_human_reviews_reviewer_id", "human_reviews", ["reviewer_id"])

    # Audit logs table
    op.create_table(
        "audit_logs",
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
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["request_id"], ["authorization_requests.request_id"]),
    )
    op.create_index("ix_audit_logs_entry_id", "audit_logs", ["entry_id"], unique=True)
    op.create_index("ix_audit_logs_timestamp", "audit_logs", ["timestamp"])
    op.create_index("ix_audit_logs_request_id", "audit_logs", ["request_id"])
    op.create_index("ix_audit_logs_correlation_id", "audit_logs", ["correlation_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])

    # Guidelines table
    op.create_table(
        "guidelines",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("guideline_id", sa.String(50), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("version", sa.String(20), nullable=False),
        sa.Column("source", sa.String(100), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("evidence_level", sa.String(20), nullable=True),
        sa.Column(
            "specialties", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=True
        ),
        sa.Column(
            "treatment_categories",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=True,
        ),
        sa.Column(
            "applicable_diagnoses",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=True,
        ),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("effective_date", sa.DateTime(), nullable=False),
        sa.Column("expiration_date", sa.DateTime(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("tags", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=True),
        sa.Column(
            "vector_store_ids",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_guidelines_guideline_id", "guidelines", ["guideline_id"], unique=True)
    op.create_index("ix_guidelines_source", "guidelines", ["source"])
    op.create_index("ix_guidelines_is_active", "guidelines", ["is_active"])


def downgrade() -> None:
    op.drop_table("guidelines")
    op.drop_table("audit_logs")
    op.drop_table("human_reviews")
    op.drop_table("authorization_decisions")
    op.drop_table("authorization_requests")
