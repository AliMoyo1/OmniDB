"""Enforce one active leased contact per agent.

Revision ID: 0009_single_active_agent_lease
Revises: 0008_role_assignment_unique
Create Date: 2026-08-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_single_active_agent_lease"
down_revision: str | None = "0008_role_assignment_unique"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Older builds allowed repeated /work/next calls to create more than one active
    # lease for the same agent. Keep the newest lease and safely return any older
    # duplicates to their pre-lease state before adding the database invariant.
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY lease_owner_id
                       ORDER BY updated_at DESC, id DESC
                   ) AS lease_rank
            FROM work_items
            WHERE state = 'leased' AND lease_owner_id IS NOT NULL
        )
        UPDATE work_items AS item
        SET state = CASE
                WHEN item.assigned_agent_id IS NOT NULL THEN 'callback_wait'
                ELSE 'queued'
            END,
            lease_owner_id = NULL,
            lease_id = NULL,
            lease_expires_at = NULL,
            version = item.version + 1,
            updated_at = now()
        FROM ranked
        WHERE item.id = ranked.id AND ranked.lease_rank > 1
        """
    )
    op.create_index(
        "uq_work_items_one_active_lease",
        "work_items",
        ["lease_owner_id"],
        unique=True,
        postgresql_where=sa.text("state = 'leased' AND lease_owner_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_work_items_one_active_lease", table_name="work_items")
