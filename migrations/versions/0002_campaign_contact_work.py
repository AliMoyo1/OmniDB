"""Campaigns, contacts, suppression, work items, attempts, imports.

Revision ID: 0002_campaign_contact_work
Revises: 0001_baseline
Create Date: 2026-08-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_campaign_contact_work"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID_DEFAULT = sa.text("gen_random_uuid()")
_NOW = sa.text("now()")


def upgrade() -> None:
    # --- campaigns ---
    op.create_table(
        "campaigns",
        sa.Column("id", sa.Uuid(), server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("owning_scope_type", sa.String(30), nullable=False),
        sa.Column("owning_scope_id", sa.Uuid(), nullable=True),
        sa.Column("owner_manager_role_assignment_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.String(2000), nullable=True),
        sa.Column("purpose", sa.String(500), nullable=True),
        sa.Column("data_source", sa.String(300), nullable=True),
        sa.Column("data_obtained_at", sa.Date(), nullable=True),
        sa.Column("lawful_basis_or_consent_reference", sa.String(500), nullable=True),
        sa.Column("default_region", sa.String(2), server_default=sa.text("'ZW'"), nullable=False),
        sa.Column(
            "timezone", sa.String(64), server_default=sa.text("'Africa/Harare'"), nullable=False
        ),
        sa.Column("status", sa.String(20), server_default=sa.text("'draft'"), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("launched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retention_delete_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_campaigns"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="fk_campaigns_created_by_users"),
        sa.CheckConstraint(
            "status in ('draft','active','paused','archived')", name="ck_campaigns_status"
        ),
    )
    op.create_index("ix_campaigns_owning_scope", "campaigns", ["owning_scope_type", "owning_scope_id", "status"])

    op.create_table(
        "campaign_team_assignments",
        sa.Column("id", sa.Uuid(), server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), server_default=sa.text("'active'"), nullable=False),
        sa.Column("staffing_capacity", sa.Integer(), nullable=True),
        sa.Column("assigned_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_campaign_team_assignments"),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], name="fk_cta_campaign_id_campaigns"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], name="fk_cta_team_id_teams"),
        sa.ForeignKeyConstraint(["assigned_by"], ["users.id"], name="fk_cta_assigned_by_users"),
    )
    op.create_index("ix_cta_campaign_team", "campaign_team_assignments", ["campaign_id", "team_id", "status"])

    op.create_table(
        "campaign_user_assignments",
        sa.Column("id", sa.Uuid(), server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=True),
        sa.Column("campaign_role", sa.String(30), nullable=False),
        sa.Column("assignment_type", sa.String(30), server_default=sa.text("'primary'"), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), server_default=sa.text("'active'"), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=True),
        sa.Column("allocation_percentage", sa.Integer(), nullable=True),
        sa.Column("shift_reference", sa.String(100), nullable=True),
        sa.Column("assigned_by", sa.Uuid(), nullable=True),
        sa.Column("approved_by", sa.Uuid(), nullable=True),
        sa.Column("reason_code", sa.String(50), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_campaign_user_assignments"),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], name="fk_cua_campaign_id_campaigns"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_cua_user_id_users"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], name="fk_cua_team_id_teams"),
        sa.ForeignKeyConstraint(["assigned_by"], ["users.id"], name="fk_cua_assigned_by_users"),
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"], name="fk_cua_approved_by_users"),
        sa.CheckConstraint(
            "campaign_role in ('manager','team_leader','team_captain','agent')",
            name="ck_cua_campaign_role",
        ),
    )
    # One active PRIMARY campaign per agent at a time (MVP concurrency rule, D-17).
    op.create_index(
        "uq_cua_one_primary_active",
        "campaign_user_assignments",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("effective_to IS NULL AND status = 'active' AND assignment_type = 'primary'"),
    )
    op.create_index(
        "ix_cua_campaign_user_status", "campaign_user_assignments", ["campaign_id", "user_id", "status"]
    )

    op.create_table(
        "campaign_disposition_definitions",
        sa.Column("id", sa.Uuid(), server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("stable_semantic_code", sa.String(50), nullable=False),
        sa.Column("next_action", sa.String(50), nullable=True),
        sa.Column("requires_notes", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "requires_callback_time", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "counts_as_connected", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "counts_as_conversion", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("causes_dnc", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("display_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_campaign_disposition_definitions"),
        sa.ForeignKeyConstraint(
            ["campaign_id"], ["campaigns.id"], name="fk_cdd_campaign_id_campaigns"
        ),
        sa.UniqueConstraint(
            "campaign_id", "stable_semantic_code", name="uq_disposition_campaign_code"
        ),
    )

    # --- contacts and suppression ---
    op.create_table(
        "contacts",
        sa.Column("id", sa.Uuid(), server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("phone_ciphertext", sa.String(1024), nullable=False),
        sa.Column("phone_fingerprint", sa.String(64), nullable=False),
        sa.Column("phone_key_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_contacts"),
        sa.UniqueConstraint("phone_fingerprint", name="uq_contacts_phone_fingerprint"),
    )

    op.create_table(
        "suppression_entries",
        sa.Column("id", sa.Uuid(), server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("organization_scope", sa.Uuid(), nullable=True),
        sa.Column("phone_fingerprint", sa.String(64), nullable=False),
        sa.Column("protected_phone_value", sa.String(1024), nullable=True),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), server_default=sa.text("'active'"), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("corrected_by", sa.Uuid(), nullable=True),
        sa.Column("corrected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("correction_reason", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_suppression_entries"),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_suppression_entries_created_by_users"
        ),
        sa.ForeignKeyConstraint(
            ["corrected_by"], ["users.id"], name="fk_suppression_entries_corrected_by_users"
        ),
    )
    # Only one ACTIVE suppression per fingerprint within an organization scope.
    op.create_index(
        "uq_suppression_active_fingerprint",
        "suppression_entries",
        ["phone_fingerprint", "organization_scope"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "campaign_contacts",
        sa.Column("id", sa.Uuid(), server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("contact_id", sa.Uuid(), nullable=False),
        sa.Column("original_phone_protected", sa.String(1024), nullable=True),
        sa.Column("campaign_name_value", sa.String(200), nullable=True),
        sa.Column("approved_metadata", postgresql.JSONB(), nullable=True),
        sa.Column("source_row_reference", sa.String(100), nullable=True),
        sa.Column("status", sa.String(20), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_by_agent_id", sa.Uuid(), nullable=True),
        sa.Column("final_disposition_code", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_campaign_contacts"),
        sa.ForeignKeyConstraint(
            ["campaign_id"], ["campaigns.id"], name="fk_campaign_contacts_campaign_id_campaigns"
        ),
        sa.ForeignKeyConstraint(
            ["contact_id"], ["contacts.id"], name="fk_campaign_contacts_contact_id_contacts"
        ),
        sa.ForeignKeyConstraint(
            ["completed_by_agent_id"], ["users.id"], name="fk_campaign_contacts_completed_by_users"
        ),
        sa.UniqueConstraint(
            "campaign_id", "contact_id", name="uq_campaign_contacts_campaign_contact"
        ),
        sa.CheckConstraint(
            "status in ('queued','leased','callback_wait','completed','suppressed','review','cancelled')",
            name="ck_campaign_contacts_status",
        ),
    )
    op.create_index("ix_campaign_contacts_campaign_status", "campaign_contacts", ["campaign_id", "status"])

    # --- batches, work items, attempts ---
    op.create_table(
        "batches",
        sa.Column("id", sa.Uuid(), server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column(
            "assignment_mode", sa.String(20), server_default=sa.text("'shared_pool'"), nullable=False
        ),
        sa.Column("assigned_agent_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(20), server_default=sa.text("'active'"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_batches"),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], name="fk_batches_campaign_id_campaigns"),
        sa.ForeignKeyConstraint(["assigned_agent_id"], ["users.id"], name="fk_batches_assigned_agent_id_users"),
    )

    op.create_table(
        "work_items",
        sa.Column("id", sa.Uuid(), server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("campaign_contact_id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=True),
        sa.Column("campaign_user_assignment_id", sa.Uuid(), nullable=True),
        sa.Column("assigned_agent_id", sa.Uuid(), nullable=True),
        sa.Column("state", sa.String(20), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("priority", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("5"), nullable=False),
        sa.Column("lease_owner_id", sa.Uuid(), nullable=True),
        sa.Column("lease_id", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_work_items"),
        sa.ForeignKeyConstraint(
            ["campaign_contact_id"], ["campaign_contacts.id"], name="fk_work_items_campaign_contact_id"
        ),
        sa.ForeignKeyConstraint(["batch_id"], ["batches.id"], name="fk_work_items_batch_id_batches"),
        sa.ForeignKeyConstraint(
            ["campaign_user_assignment_id"],
            ["campaign_user_assignments.id"],
            name="fk_work_items_cua_id",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_agent_id"], ["users.id"], name="fk_work_items_assigned_agent_id_users"
        ),
        sa.ForeignKeyConstraint(
            ["lease_owner_id"], ["users.id"], name="fk_work_items_lease_owner_id_users"
        ),
        sa.CheckConstraint(
            "state in ('queued','leased','callback_wait','completed','suppressed','review','cancelled')",
            name="ck_work_items_state",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_work_items_attempt_count_nonneg"),
    )
    # Only one current (non-terminal) work item per campaign contact.
    op.create_index(
        "uq_work_items_one_active_per_contact",
        "work_items",
        ["campaign_contact_id"],
        unique=True,
        postgresql_where=sa.text("state not in ('completed','suppressed','cancelled')"),
    )
    # A work item has at most one valid active lease: enforced by lease_id being set
    # only while state='leased', checked in the service layer transaction; this index
    # makes "who currently holds a lease" queries efficient and lease collisions visible.
    op.create_index(
        "ix_work_items_active_lease",
        "work_items",
        ["lease_owner_id", "lease_expires_at"],
        postgresql_where=sa.text("state = 'leased'"),
    )
    op.create_index("ix_work_items_queue", "work_items", ["state", "priority", "due_at"])

    op.create_table(
        "call_attempts",
        sa.Column("id", sa.Uuid(), server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("work_item_id", sa.Uuid(), nullable=False),
        sa.Column("campaign_contact_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("campaign_user_assignment_id", sa.Uuid(), nullable=True),
        sa.Column("disposition_definition_id", sa.Uuid(), nullable=False),
        sa.Column("semantic_outcome", sa.String(50), nullable=False),
        sa.Column("notes_ciphertext", sa.String(4096), nullable=True),
        sa.Column("self_reported_duration_seconds", sa.Integer(), nullable=True),
        sa.Column("explicit_dnc_requested", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("callback_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        sa.Column("correction_of_attempt_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_call_attempts"),
        sa.ForeignKeyConstraint(
            ["work_item_id"], ["work_items.id"], name="fk_call_attempts_work_item_id"
        ),
        sa.ForeignKeyConstraint(
            ["campaign_contact_id"], ["campaign_contacts.id"], name="fk_call_attempts_campaign_contact_id"
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["users.id"], name="fk_call_attempts_agent_id_users"),
        sa.ForeignKeyConstraint(
            ["campaign_user_assignment_id"],
            ["campaign_user_assignments.id"],
            name="fk_call_attempts_cua_id",
        ),
        sa.ForeignKeyConstraint(
            ["disposition_definition_id"],
            ["campaign_disposition_definitions.id"],
            name="fk_call_attempts_disposition_id",
        ),
        sa.ForeignKeyConstraint(
            ["correction_of_attempt_id"], ["call_attempts.id"], name="fk_call_attempts_correction_of"
        ),
        sa.UniqueConstraint("agent_id", "idempotency_key", name="uq_call_attempts_agent_idem"),
    )
    op.create_index(
        "ix_call_attempts_campaign_contact", "call_attempts", ["campaign_contact_id", "created_at"]
    )
    op.create_index("ix_call_attempts_agent", "call_attempts", ["agent_id", "created_at"])

    # --- imports ---
    op.create_table(
        "import_jobs",
        sa.Column("id", sa.Uuid(), server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("uploader_id", sa.Uuid(), nullable=False),
        sa.Column("source_filename_display", sa.String(255), nullable=False),
        sa.Column("generated_storage_key", sa.String(255), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(20), server_default=sa.text("'uploaded'"), nullable=False),
        sa.Column("parser_version", sa.String(20), server_default=sa.text("'1'"), nullable=False),
        sa.Column("total_rows", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("valid_rows", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("invalid_rows", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("duplicate_rows", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("suppression_hits", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("decision_version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("idempotency_key", sa.String(100), nullable=True),
        sa.Column("error_summary", sa.String(1000), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_import_jobs"),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], name="fk_import_jobs_campaign_id"),
        sa.ForeignKeyConstraint(["uploader_id"], ["users.id"], name="fk_import_jobs_uploader_id_users"),
        sa.CheckConstraint(
            "state in ('uploaded','quarantined','parsing','parsed','failed','committed','expired')",
            name="ck_import_jobs_state",
        ),
    )
    op.create_index("ix_import_jobs_campaign_state", "import_jobs", ["campaign_id", "state"])
    # Idempotent commit retry: same key returns the same result.
    op.create_index(
        "uq_import_jobs_idempotency",
        "import_jobs",
        ["uploader_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.create_table(
        "import_rows",
        sa.Column("id", sa.Uuid(), server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("import_job_id", sa.Uuid(), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("raw_phone_protected", sa.String(1024), nullable=True),
        sa.Column("phone_fingerprint", sa.String(64), nullable=True),
        sa.Column("canonical_values", postgresql.JSONB(), nullable=True),
        sa.Column("validation_result", sa.String(20), nullable=False),
        sa.Column("validation_detail", sa.String(500), nullable=True),
        sa.Column("duplicate_category", sa.String(30), nullable=True),
        sa.Column("suppression_match", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_import_rows"),
        sa.ForeignKeyConstraint(
            ["import_job_id"], ["import_jobs.id"], name="fk_import_rows_import_job_id"
        ),
    )
    op.create_index("ix_import_rows_job", "import_rows", ["import_job_id", "row_number"])
    op.create_index("ix_import_rows_job_fingerprint", "import_rows", ["import_job_id", "phone_fingerprint"])

    op.create_table(
        "import_decisions",
        sa.Column("id", sa.Uuid(), server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("import_job_id", sa.Uuid(), nullable=False),
        sa.Column("decision_version", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(30), nullable=False),
        sa.Column("decided_by", sa.Uuid(), nullable=False),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_import_decisions"),
        sa.ForeignKeyConstraint(
            ["import_job_id"], ["import_jobs.id"], name="fk_import_decisions_import_job_id"
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"], ["users.id"], name="fk_import_decisions_decided_by_users"
        ),
    )


def downgrade() -> None:
    op.drop_table("import_decisions")
    op.drop_table("import_rows")
    op.drop_index("uq_import_jobs_idempotency", table_name="import_jobs")
    op.drop_table("import_jobs")
    op.drop_table("call_attempts")
    op.drop_index("uq_work_items_one_active_per_contact", table_name="work_items")
    op.drop_table("work_items")
    op.drop_table("batches")
    op.drop_table("campaign_contacts")
    op.drop_index("uq_suppression_active_fingerprint", table_name="suppression_entries")
    op.drop_table("suppression_entries")
    op.drop_table("contacts")
    op.drop_table("campaign_disposition_definitions")
    op.drop_index("uq_cua_one_primary_active", table_name="campaign_user_assignments")
    op.drop_table("campaign_user_assignments")
    op.drop_table("campaign_team_assignments")
    op.drop_table("campaigns")
