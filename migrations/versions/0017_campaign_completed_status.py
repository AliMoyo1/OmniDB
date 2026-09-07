"""Allow the campaigns.status value 'completed' (ADR-020 retention).

Completion detection moves a fully-worked campaign into a new 'completed' state
that starts the retention countdown. The status CHECK constraint from 0002 only
permitted draft/active/paused/archived, so it is widened to include 'completed'.

Revision ID: 0017_campaign_completed_status
Revises: 0016_notifications
Create Date: 2026-09-02
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0017_campaign_completed_status"
down_revision: str | None = "0016_notifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The live constraint name (0002's CheckConstraint name run through this
# build's ck_%(table_name)s_%(constraint_name)s convention, hence the doubled
# prefix). Raw SQL is used so the exact name is preserved rather than re-run
# through the convention (which would triple-prefix it).
_CONSTRAINT = "ck_campaigns_ck_campaigns_status"
_WITH_COMPLETED = "status in ('draft','active','paused','archived','completed')"
_WITHOUT_COMPLETED = "status in ('draft','active','paused','archived')"


def upgrade() -> None:
    op.execute(f"ALTER TABLE campaigns DROP CONSTRAINT {_CONSTRAINT}")
    op.execute(f"ALTER TABLE campaigns ADD CONSTRAINT {_CONSTRAINT} CHECK ({_WITH_COMPLETED})")


def downgrade() -> None:
    # Any campaign already moved to 'completed' maps back to 'archived' (the
    # closest terminal state) so the stricter constraint can be restored.
    op.execute("UPDATE campaigns SET status = 'archived' WHERE status = 'completed'")
    op.execute(f"ALTER TABLE campaigns DROP CONSTRAINT {_CONSTRAINT}")
    op.execute(f"ALTER TABLE campaigns ADD CONSTRAINT {_CONSTRAINT} CHECK ({_WITHOUT_COMPLETED})")
