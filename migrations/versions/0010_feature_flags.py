"""Server-enforced rollout flags (master plan 21.2).

Revision ID: 0010_feature_flags
Revises: 0009_single_active_agent_lease
Create Date: 2026-08-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_feature_flags"
down_revision: str | None = "0009_single_active_agent_lease"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Seeded true: already-shipped, currently-working features - a flag defaulting
# to off here would be a regression, not a rollout gate. Seeded false: nothing
# built yet to gate (retention execution, analytics) or permanently false by
# design (ai_enabled, enforced again in app/flags/service.py so it can't be
# flipped later just because someone edits this row directly).
_SEEDED_FLAGS = {
    "campaign_import_enabled": True,
    "campaign_launch_enabled": True,
    "shared_pool_enabled": True,
    "callbacks_enabled": True,
    "viewer_enabled": True,
    "retention_execution_enabled": False,
    "analytics_enabled": False,
    "ai_enabled": False,
}


def upgrade() -> None:
    op.create_table(
        "feature_flags",
        sa.Column("flag_key", sa.String(length=50), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"], name="fk_feature_flags_updated_by_users"),
        sa.PrimaryKeyConstraint("flag_key", name="pk_feature_flags"),
    )
    flags_table = sa.table(
        "feature_flags", sa.column("flag_key", sa.String), sa.column("enabled", sa.Boolean)
    )
    op.bulk_insert(
        flags_table,
        [{"flag_key": key, "enabled": value} for key, value in _SEEDED_FLAGS.items()],
    )


def downgrade() -> None:
    op.drop_table("feature_flags")
