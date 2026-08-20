"""Add skip_count to work_items, separate from attempt_count.

Revision ID: 0004_work_item_skip_count
Revises: 0003_import_commit_result
Create Date: 2026-08-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_work_item_skip_count"
down_revision: str | None = "0003_import_commit_result"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "work_items",
        sa.Column("skip_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("work_items", "skip_count")
