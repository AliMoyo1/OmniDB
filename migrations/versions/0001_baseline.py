"""Baseline schema: identity, authorization, sessions, audit.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-08-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID_DEFAULT = sa.text("gen_random_uuid()")
_NOW = sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.Uuid(), server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), server_default=sa.text("'active'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_organizations"),
        sa.UniqueConstraint("name", name="uq_organizations_name"),
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("workforce_id", sa.String(150), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=True),
        sa.Column("totp_secret_ciphertext", sa.String(512), nullable=True),
        sa.Column("totp_enrolled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("identity_provider_subject", sa.String(255), nullable=True),
        sa.Column("workforce_status", sa.String(20), server_default=sa.text("'active'"), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("workforce_id", name="uq_users_workforce_id"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    op.create_table(
        "teams",
        sa.Column("id", sa.Uuid(), server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("external_code", sa.String(50), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("parent_team_id", sa.Uuid(), nullable=True),
        sa.Column(
            "default_timezone",
            sa.String(64),
            server_default=sa.text("'Africa/Harare'"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), server_default=sa.text("'active'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_teams"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], name="fk_teams_organization_id_organizations"
        ),
        sa.ForeignKeyConstraint(
            ["parent_team_id"], ["teams.id"], name="fk_teams_parent_team_id_teams"
        ),
        sa.UniqueConstraint("organization_id", "external_code", name="uq_teams_org_external_code"),
    )

    op.create_table(
        "team_memberships",
        sa.Column("id", sa.Uuid(), server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "membership_status", sa.String(20), server_default=sa.text("'active'"), nullable=False
        ),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_team_memberships"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], name="fk_team_memberships_team_id_teams"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_team_memberships_user_id_users"),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_team_memberships_created_by_users"
        ),
    )
    # No overlapping active membership for the same user and team.
    op.create_index(
        "uq_team_memberships_active",
        "team_memberships",
        ["user_id", "team_id"],
        unique=True,
        postgresql_where=sa.text("effective_to IS NULL AND membership_status = 'active'"),
    )

    op.create_table(
        "role_assignments",
        sa.Column("id", sa.Uuid(), server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role_code", sa.String(50), nullable=False),
        sa.Column("scope_type", sa.String(30), nullable=False),
        sa.Column("scope_id", sa.Uuid(), nullable=True),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), server_default=sa.text("'active'"), nullable=False),
        sa.Column(
            "appointment_type",
            sa.String(30),
            server_default=sa.text("'permanent'"),
            nullable=False,
        ),
        sa.Column("reason_code", sa.String(50), nullable=True),
        sa.Column("appointed_by", sa.Uuid(), nullable=True),
        sa.Column("approved_by", sa.Uuid(), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_role_assignments"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_role_assignments_user_id_users"),
        sa.ForeignKeyConstraint(
            ["appointed_by"], ["users.id"], name="fk_role_assignments_appointed_by_users"
        ),
        sa.ForeignKeyConstraint(
            ["approved_by"], ["users.id"], name="fk_role_assignments_approved_by_users"
        ),
    )
    op.create_index(
        "ix_role_assignments_user_role_scope",
        "role_assignments",
        ["user_id", "role_code", "scope_type", "status"],
    )

    op.create_table(
        "reporting_assignments",
        sa.Column("id", sa.Uuid(), server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("subordinate_user_id", sa.Uuid(), nullable=False),
        sa.Column("supervisor_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "context_type", sa.String(30), server_default=sa.text("'organization'"), nullable=False
        ),
        sa.Column("context_id", sa.Uuid(), nullable=True),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "assignment_type", sa.String(30), server_default=sa.text("'primary'"), nullable=False
        ),
        sa.Column("status", sa.String(20), server_default=sa.text("'active'"), nullable=False),
        sa.Column("assigned_by", sa.Uuid(), nullable=True),
        sa.Column("reason_code", sa.String(50), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_reporting_assignments"),
        sa.ForeignKeyConstraint(
            ["subordinate_user_id"], ["users.id"], name="fk_reporting_assignments_subordinate_user_id_users"
        ),
        sa.ForeignKeyConstraint(
            ["supervisor_user_id"], ["users.id"], name="fk_reporting_assignments_supervisor_user_id_users"
        ),
        sa.ForeignKeyConstraint(
            ["assigned_by"], ["users.id"], name="fk_reporting_assignments_assigned_by_users"
        ),
    )
    op.create_index(
        "ix_reporting_assignments_subordinate",
        "reporting_assignments",
        ["subordinate_user_id", "status"],
    )

    op.create_table(
        "delegations",
        sa.Column("id", sa.Uuid(), server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("delegator_user_id", sa.Uuid(), nullable=False),
        sa.Column("delegate_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "capability_set",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("scope_type", sa.String(30), nullable=False),
        sa.Column("scope_id", sa.Uuid(), nullable=True),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason_code", sa.String(50), nullable=True),
        sa.Column("approved_by", sa.Uuid(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_delegations"),
        sa.ForeignKeyConstraint(
            ["delegator_user_id"], ["users.id"], name="fk_delegations_delegator_user_id_users"
        ),
        sa.ForeignKeyConstraint(
            ["delegate_user_id"], ["users.id"], name="fk_delegations_delegate_user_id_users"
        ),
        sa.ForeignKeyConstraint(
            ["approved_by"], ["users.id"], name="fk_delegations_approved_by_users"
        ),
    )

    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(128), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idle_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_summary", sa.String(255), nullable=True),
        sa.Column("mfa_state", sa.String(20), server_default=sa.text("'none'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_sessions"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_sessions_user_id_users"),
        sa.UniqueConstraint("token_hash", name="uq_sessions_token_hash"),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), server_default=_UUID_DEFAULT, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("actor_role", sa.String(50), nullable=True),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("team_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("result", sa.String(20), nullable=False),
        sa.Column("target_type", sa.String(50), nullable=True),
        sa.Column("target_id", sa.Uuid(), nullable=True),
        sa.Column("correlation_id", sa.String(64), nullable=True),
        sa.Column("source_ip", sa.String(64), nullable=True),
        sa.Column("user_agent_summary", sa.String(255), nullable=True),
        sa.Column("reason_code", sa.String(50), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_audit_events"),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], name="fk_audit_events_actor_user_id_users"
        ),
    )
    op.create_index("ix_audit_events_occurred_at", "audit_events", ["occurred_at"])
    op.create_index("ix_audit_events_action", "audit_events", ["action"])
    op.create_index("ix_audit_events_actor_user_id", "audit_events", ["actor_user_id"])


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("sessions")
    op.drop_table("delegations")
    op.drop_table("reporting_assignments")
    op.drop_table("role_assignments")
    op.drop_index("uq_team_memberships_active", table_name="team_memberships")
    op.drop_table("team_memberships")
    op.drop_table("teams")
    op.drop_table("users")
    op.drop_table("organizations")
