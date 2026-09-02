"""Staged bulk-workforce import jobs, rows, and decisions (Phase 4B).

Revision ID: 0011_workforce_imports
Revises: 0010_feature_flags
Create Date: 2026-09-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_workforce_imports"
down_revision: str | None = "0010_feature_flags"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workforce_import_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("import_type", sa.String(length=30), nullable=False),
        sa.Column("uploader_id", sa.Uuid(), nullable=False),
        sa.Column("source_filename_display", sa.String(length=255), nullable=False),
        sa.Column("generated_storage_key", sa.String(length=255), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False, server_default="quarantined"),
        sa.Column("total_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("valid_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("warning_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("invalid_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("high_risk_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("decision_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("idempotency_key", sa.String(length=100), nullable=True),
        sa.Column("error_summary", sa.String(length=1000), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("committed_result", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["uploader_id"], ["users.id"], name="fk_workforce_import_jobs_uploader_id_users"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workforce_import_jobs"),
    )
    op.create_index(
        "ix_workforce_import_jobs_uploader_state",
        "workforce_import_jobs",
        ["uploader_id", "state"],
    )
    op.create_index(
        "ix_workforce_import_jobs_type_state",
        "workforce_import_jobs",
        ["import_type", "state"],
    )
    op.create_index(
        "ix_workforce_import_jobs_expires_at", "workforce_import_jobs", ["expires_at"]
    )

    op.create_table(
        "workforce_import_rows",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("import_job_id", sa.Uuid(), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=True),
        sa.Column("external_workforce_id", sa.String(length=150), nullable=True),
        sa.Column("normalized_identity", sa.Uuid(), nullable=True),
        sa.Column("parsed_values", postgresql.JSONB(), nullable=True),
        sa.Column("validation_result", sa.String(length=20), nullable=False),
        sa.Column("validation_detail", sa.String(length=500), nullable=True),
        sa.Column("conflict_type", sa.String(length=30), nullable=True),
        sa.Column("risk_level", sa.String(length=20), nullable=False, server_default="routine"),
        sa.Column("decision", sa.String(length=20), nullable=True),
        sa.Column("committed_entity_type", sa.String(length=30), nullable=True),
        sa.Column("committed_entity_id", sa.Uuid(), nullable=True),
        sa.Column("pre_commit_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["import_job_id"],
            ["workforce_import_jobs.id"],
            name="fk_workforce_import_rows_import_job_id_workforce_import_jobs",
        ),
        sa.ForeignKeyConstraint(
            ["normalized_identity"],
            ["users.id"],
            name="fk_workforce_import_rows_normalized_identity_users",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workforce_import_rows"),
    )
    op.create_index(
        "ix_workforce_import_rows_job_id", "workforce_import_rows", ["import_job_id"]
    )
    op.create_index(
        "ix_workforce_import_rows_job_result",
        "workforce_import_rows",
        ["import_job_id", "validation_result"],
    )

    op.create_table(
        "workforce_import_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("import_job_id", sa.Uuid(), nullable=False),
        sa.Column("decision_version", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(length=30), nullable=False),
        sa.Column("decision_tier", sa.String(length=20), nullable=False, server_default="standard"),
        sa.Column("decided_by", sa.Uuid(), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["import_job_id"],
            ["workforce_import_jobs.id"],
            name="fk_workforce_import_decisions_import_job_id_workforce_import_jobs",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"], ["users.id"], name="fk_workforce_import_decisions_decided_by_users"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workforce_import_decisions"),
    )
    op.create_index(
        "ix_workforce_import_decisions_job_version",
        "workforce_import_decisions",
        ["import_job_id", "decision_version"],
    )


def downgrade() -> None:
    op.drop_table("workforce_import_decisions")
    op.drop_table("workforce_import_rows")
    op.drop_table("workforce_import_jobs")
