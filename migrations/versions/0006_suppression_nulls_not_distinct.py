"""Enforce one active suppression when organization scope is null.

Revision ID: 0006_suppression_nulls_not_distinct
Revises: 0005_one_time_activation_and_step_up
Create Date: 2026-08-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_suppression_nulls_not_distinct"
down_revision: str | None = "0005_one_time_activation_and_step_up"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY phone_fingerprint, organization_scope
                       ORDER BY effective_at, created_at, id
                   ) AS duplicate_rank
            FROM suppression_entries
            WHERE status = 'active'
        )
        UPDATE suppression_entries AS suppression
        SET status = 'corrected',
            corrected_at = now(),
            correction_reason = COALESCE(
                suppression.correction_reason,
                'Deduplicated while enforcing active suppression uniqueness'
            )
        FROM ranked
        WHERE suppression.id = ranked.id
          AND ranked.duplicate_rank > 1
        """
    )
    op.drop_index("uq_suppression_active_fingerprint", table_name="suppression_entries")
    op.create_index(
        "uq_suppression_active_fingerprint",
        "suppression_entries",
        ["phone_fingerprint", "organization_scope"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    op.drop_index("uq_suppression_active_fingerprint", table_name="suppression_entries")
    op.create_index(
        "uq_suppression_active_fingerprint",
        "suppression_entries",
        ["phone_fingerprint", "organization_scope"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
