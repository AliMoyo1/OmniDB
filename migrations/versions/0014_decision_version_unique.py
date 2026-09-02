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

Before creating that constraint, this also reconciles any duplicate pairs the
pre-fix race may already have produced - a unique constraint cannot be created
over data that already violates it, so without this step, upgrading a database
that ever actually hit the race would fail outright. Every job's decisions are
renumbered into a gapless, chronological sequence by (created_at, id): a
decision's version has always equalled its chronological insertion rank (even
under the buggy pre-fix code, since job.decision_version was only ever
incremented, never assigned out of order) - so for any job the race never hit,
this reproduces the exact version numbers already there, a no-op. For a job
the race did hit, it collapses the duplicate into a valid sequence. No
decision row is ever deleted or its content changed: these are audit-relevant
records of who approved or rejected what, and when.

Revision ID: 0014_decision_version_unique
Revises: 0013_campaign_external_code
Create Date: 2026-09-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_decision_version_unique"
down_revision: str | None = "0013_campaign_external_code"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()

    duplicate_count = bind.execute(
        sa.text(
            """
            SELECT COUNT(*) FROM (
                SELECT import_job_id, decision_version
                FROM workforce_import_decisions
                GROUP BY import_job_id, decision_version
                HAVING COUNT(*) > 1
            ) AS dupes
            """
        )
    ).scalar()
    if duplicate_count:
        print(
            f"0014_decision_version_unique: found {duplicate_count} duplicate "
            "(import_job_id, decision_version) pair(s) from the pre-fix race - "
            "renumbering into a gapless sequence before adding the constraint."
        )

    bind.execute(
        sa.text(
            """
            WITH renumbered AS (
                SELECT
                    id,
                    ROW_NUMBER() OVER (
                        PARTITION BY import_job_id ORDER BY created_at, id
                    ) AS new_version
                FROM workforce_import_decisions
            )
            UPDATE workforce_import_decisions AS d
            SET decision_version = r.new_version
            FROM renumbered r
            WHERE d.id = r.id AND d.decision_version != r.new_version
            """
        )
    )
    bind.execute(
        sa.text(
            """
            UPDATE workforce_import_jobs AS j
            SET decision_version = sub.max_version
            FROM (
                SELECT import_job_id, MAX(decision_version) AS max_version
                FROM workforce_import_decisions
                GROUP BY import_job_id
            ) AS sub
            WHERE j.id = sub.import_job_id AND j.decision_version != sub.max_version
            """
        )
    )

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
