"""Seed the workforce_import_enabled rollout flag (Phase 4B).

Seeded false, not true: unlike the flags migration 0010 seeded (existing,
already-shipped features that would regress if defaulted off), bulk workforce
import is a brand-new high-risk feature - it starts off and is turned on
deliberately, matching the master plan's own "gradual pilot rollout" framing.

Revision ID: 0012_workforce_import_flag
Revises: 0011_workforce_imports
Create Date: 2026-09-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_workforce_import_flag"
down_revision: str | None = "0011_workforce_imports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    flags_table = sa.table(
        "feature_flags", sa.column("flag_key", sa.String), sa.column("enabled", sa.Boolean)
    )
    op.bulk_insert(flags_table, [{"flag_key": "workforce_import_enabled", "enabled": False}])


def downgrade() -> None:
    op.execute("DELETE FROM feature_flags WHERE flag_key = 'workforce_import_enabled'")
