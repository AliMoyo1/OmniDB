"""Make workforce_import_decisions.(import_job_id, decision_version) unique.

record_decision previously incremented an already-loaded, unlocked job object,
and this index was only ever a plain (non-unique) lookup index - two concurrent
approve/reject requests on the same job could both read the same
decision_version and both insert a row claiming it, leaving "the latest
decision" nondeterministic. record_decision now locks the job row with
SELECT ... FOR UPDATE before incrementing (service-layer fix), and this
migration adds the database-level backstop: the old lookup index is replaced
with a unique constraint covering the same two columns, so the race is
rejected by Postgres even if the locking discipline above it is ever bypassed.

Revision ID: 0014_decision_version_unique
Revises: 0013_campaign_external_code
Create Date: 2026-09-02
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0014_decision_version_unique"
down_revision: str | None = "0013_campaign_external_code"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_workforce_import_decisions_job_version", table_name="workforce_import_decisions")
    op.create_unique_constraint(
        "uq_workforce_import_decisions_job_version",
        "workforce_import_decisions",
        ["import_job_id", "decision_version"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_workforce_import_decisions_job_version",
        "workforce_import_decisions",
        type_="unique",
    )
    op.create_index(
        "ix_workforce_import_decisions_job_version",
        "workforce_import_decisions",
        ["import_job_id", "decision_version"],
    )
