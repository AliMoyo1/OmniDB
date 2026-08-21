"""Persist the original completion state for stable idempotent responses.

Revision ID: 0007_completion_idempotency_result
Revises: 0006_suppression_nulls_not_distinct
Create Date: 2026-08-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_completion_idempotency_result"
down_revision: str | None = "0006_suppression_nulls_not_distinct"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "call_attempts",
        sa.Column("resulting_work_item_state", sa.String(20), nullable=True),
    )
    op.execute(
        """
        UPDATE call_attempts AS attempt
        SET resulting_work_item_state = work_item.state
        FROM work_items AS work_item
        WHERE work_item.id = attempt.work_item_id
        """
    )
    op.alter_column(
        "call_attempts",
        "resulting_work_item_state",
        existing_type=sa.String(20),
        nullable=False,
    )


def downgrade() -> None:
    op.drop_column("call_attempts", "resulting_work_item_state")
