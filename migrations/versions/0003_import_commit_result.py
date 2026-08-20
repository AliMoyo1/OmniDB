"""Add committed_result to import_jobs for idempotent commit replay.

Revision ID: 0003_import_commit_result
Revises: 0002_campaign_contact_work
Create Date: 2026-08-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_import_commit_result"
down_revision: str | None = "0002_campaign_contact_work"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("import_jobs", sa.Column("committed_result", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("import_jobs", "committed_result")
