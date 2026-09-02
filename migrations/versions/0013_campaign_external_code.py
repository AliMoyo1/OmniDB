"""Add campaigns.external_code, a stable operator-chosen identifier.

Mirrors Team.external_code (already unique, already the identifier every other
bulk-import template uses instead of a raw UUID). Existing campaigns are
backfilled with a deterministic, guaranteed-unique code derived from their name
plus a short slice of their own id - not meant to be a good code, just a real
one, so an operator can rename it later without anything breaking (nothing
else references external_code by value yet).

Revision ID: 0013_campaign_external_code
Revises: 0012_workforce_import_flag
Create Date: 2026-09-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_campaign_external_code"
down_revision: str | None = "0012_workforce_import_flag"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("campaigns", sa.Column("external_code", sa.String(length=50), nullable=True))
    op.execute(
        """
        UPDATE campaigns
        SET external_code = substring(
            regexp_replace(lower(name), '[^a-z0-9]+', '-', 'g') from 1 for 40
        ) || '-' || substring(id::text from 1 for 8)
        WHERE external_code IS NULL
        """
    )
    op.alter_column("campaigns", "external_code", nullable=False)
    op.create_unique_constraint("uq_campaigns_external_code", "campaigns", ["external_code"])


def downgrade() -> None:
    op.drop_constraint("uq_campaigns_external_code", "campaigns", type_="unique")
    op.drop_column("campaigns", "external_code")
