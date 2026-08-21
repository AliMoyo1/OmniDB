"""Enforce one active role assignment per scope and one active primary report line.

Revision ID: 0008_role_assignment_unique
Revises: 0007_completion_idempotent
Create Date: 2026-08-21

role_assignments and reporting_assignments have existed since the baseline migration
but, until Phase 4A-1, nothing ever wrote to them outside test fixtures, so this gap
was latent rather than exercised. Same pattern already used for campaign_user_
assignments (uq_cua_one_primary_active) and team_memberships (uq_team_memberships_
active): a partial unique index, not just application-level checking, so a race
between two concurrent appointments cannot create two overlapping active grants.
scope_id/context_id are nullable (organization/installation-wide scope), so this
uses NULLS NOT DISTINCT the same way 0006 fixed suppression_entries.

Note: kept short (<=32 chars); see 0005 for why (VARCHAR(32) alembic_version).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_role_assignment_unique"
down_revision: str | None = "0007_completion_idempotent"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_role_assignments_active",
        "role_assignments",
        ["user_id", "role_code", "scope_type", "scope_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active' AND effective_to IS NULL"),
        postgresql_nulls_not_distinct=True,
    )
    op.create_index(
        "uq_reporting_assignments_primary",
        "reporting_assignments",
        ["subordinate_user_id", "context_type", "context_id"],
        unique=True,
        postgresql_where=sa.text(
            "status = 'active' AND effective_to IS NULL AND assignment_type = 'primary'"
        ),
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    op.drop_index("uq_reporting_assignments_primary", table_name="reporting_assignments")
    op.drop_index("uq_role_assignments_active", table_name="role_assignments")
