"""Add retry_wait work-item state, delayed-retry indexing, and immediate-redial
snapshot fields on call attempts (phase 4D plan 6.2, 6.3).

Purely additive - the two new nullable CallAttempt columns and the new
lease_reason column stay null on every existing row, and 'retry_wait' is only
ever set by app.work.service, which nothing calls into that state yet outside
tests until Phase C wires an agent-facing standard campaign.

Revision ID: 0020_retry_wait_and_redial
Revises: 0019_standard_dispositions
Create Date: 2026-09-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_retry_wait_and_redial"
down_revision: str | None = "0019_standard_dispositions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The live name of 0002's ck_work_items_state constraint, after this build's
# naming convention (migrations/env.py) doubled the prefix on creation - the
# same effect 0017 already documented and worked around for campaigns.status.
_STATE_CONSTRAINT = "ck_work_items_ck_work_items_state"
_STATE_WITHOUT_RETRY_WAIT = (
    "state in ('queued','leased','callback_wait','completed','suppressed','review','cancelled')"
)
_STATE_WITH_RETRY_WAIT = (
    "state in ('queued','leased','callback_wait','retry_wait',"
    "'completed','suppressed','review','cancelled')"
)

_LEASE_REASON_VALUES = "('normal', 'scheduled_callback', 'delayed_retry', 'immediate_redial')"


def upgrade() -> None:
    op.add_column(
        "work_items",
        sa.Column("lease_reason", sa.String(length=30), nullable=True),
    )
    op.execute(
        "ALTER TABLE work_items ADD CONSTRAINT ck_work_items_lease_reason "
        f"CHECK (lease_reason IS NULL OR lease_reason IN {_LEASE_REASON_VALUES})"
    )
    op.execute(f"ALTER TABLE work_items DROP CONSTRAINT {_STATE_CONSTRAINT}")
    op.execute(
        f"ALTER TABLE work_items ADD CONSTRAINT {_STATE_CONSTRAINT} "
        f"CHECK ({_STATE_WITH_RETRY_WAIT})"
    )
    # Due retries become leaseable once due_at <= now, ordered oldest-due,
    # highest-priority, oldest-created (plan 5) - matches the query in
    # app.work.service exactly so the index actually serves it.
    op.execute(
        "CREATE INDEX ix_work_items_due_retry ON work_items "
        "(due_at, priority, created_at) WHERE state = 'retry_wait'"
    )

    op.add_column(
        "call_attempts",
        sa.Column("source_lease_reason", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "call_attempts",
        sa.Column("resulting_lease_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "call_attempts",
        sa.Column("resulting_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "ALTER TABLE call_attempts ADD CONSTRAINT ck_call_attempts_source_lease_reason "
        f"CHECK (source_lease_reason IS NULL OR source_lease_reason IN {_LEASE_REASON_VALUES})"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE call_attempts DROP CONSTRAINT ck_call_attempts_source_lease_reason"
    )
    op.drop_column("call_attempts", "resulting_lease_expires_at")
    op.drop_column("call_attempts", "resulting_lease_id")
    op.drop_column("call_attempts", "source_lease_reason")

    op.execute("DROP INDEX ix_work_items_due_retry")
    # Any live retry_wait row cannot survive the narrower constraint below -
    # map it back to queued (the closest pre-existing shared-pool state) so
    # the downgrade cannot leave an already-committed row constraint-invalid.
    op.execute(
        "UPDATE work_items SET state = 'queued', due_at = NULL "
        "WHERE state = 'retry_wait'"
    )
    op.execute(f"ALTER TABLE work_items DROP CONSTRAINT {_STATE_CONSTRAINT}")
    op.execute(
        f"ALTER TABLE work_items ADD CONSTRAINT {_STATE_CONSTRAINT} "
        f"CHECK ({_STATE_WITHOUT_RETRY_WAIT})"
    )
    op.execute("ALTER TABLE work_items DROP CONSTRAINT ck_work_items_lease_reason")
    op.drop_column("work_items", "lease_reason")
