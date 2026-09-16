"""Seed the deferred_bulk_activation_enabled rollout flag (admin user management plan 6.5).

Seeded false, matching 0012's precedent for a brand-new high-risk behavior
change: while off, bulk-imported users keep getting an activation token issued
immediately at commit (today's behavior, unchanged). Turning it on is a
deliberate, audited step taken only after the read-only directory and
credential-administration actions have been verified in production (plan 13).

Revision ID: 0018_deferred_activation_flag
Revises: 0017_campaign_completed_status
Create Date: 2026-09-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_deferred_activation_flag"
down_revision: str | None = "0017_campaign_completed_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    flags_table = sa.table(
        "feature_flags", sa.column("flag_key", sa.String), sa.column("enabled", sa.Boolean)
    )
    op.bulk_insert(
        flags_table, [{"flag_key": "deferred_bulk_activation_enabled", "enabled": False}]
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM feature_flags WHERE flag_key = 'deferred_bulk_activation_enabled'"
    )
