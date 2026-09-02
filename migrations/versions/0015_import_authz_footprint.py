"""Add workforce_import_jobs.authorization_footprint.

The object-level access check (can_access_job / visible_jobs) previously
loaded and re-derived every WorkforceImportRow's authorization requirement
each time it ran - once per candidate job on the list view, up to 200 of
them, at up to the 100,000-row-per-job maximum. This column stores the
distinct requirement set computed once at parse time, so the access check
reads one small JSONB value instead of materializing millions of rows.

Nullable and not backfilled on purpose: NULL means "not computed" (a job
still parsing, one that failed to parse, or one that predates this column),
which can_access_job treats as uploader-only - never fail-open. A job
parsed before this migration keeps working via the row-loading fallback
until it expires; only newly parsed jobs get the cheap path, which is the
entire ongoing surface.

Revision ID: 0015_import_authz_footprint
Revises: 0014_decision_version_unique
Create Date: 2026-09-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015_import_authz_footprint"
down_revision: str | None = "0014_decision_version_unique"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workforce_import_jobs",
        sa.Column("authorization_footprint", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workforce_import_jobs", "authorization_footprint")
