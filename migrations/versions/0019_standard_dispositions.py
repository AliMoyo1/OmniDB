"""Add the standard-dispositions manifest schema (phase 4D plan 6.1, 9): a
campaign-level policy version, standard/retry/redial fields on disposition
definitions, and the standard_dispositions_enabled rollout flag.

Purely additive - existing campaigns and their free-form disposition rows are
untouched (is_standard/immediate_redial default false, policy_version stays
null), and the workflow flag seeds false, so no active campaign changes
behavior until a draft campaign is explicitly given the standard policy.

Revision ID: 0019_standard_dispositions
Revises: 0018_deferred_activation_flag
Create Date: 2026-09-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_standard_dispositions"
down_revision: str | None = "0018_deferred_activation_flag"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "campaign_disposition_definitions",
        sa.Column("is_standard", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "campaign_disposition_definitions",
        sa.Column("retry_delay_minutes", sa.Integer(), nullable=True),
    )
    op.add_column(
        "campaign_disposition_definitions",
        sa.Column(
            "immediate_redial", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
    )
    op.add_column(
        "campaign_disposition_definitions",
        sa.Column("policy_version", sa.Integer(), nullable=True),
    )
    # Raw SQL, not op.create_check_constraint(): this build's naming convention
    # (see migrations/env.py's target_metadata) re-runs an explicitly given
    # constraint name through the ck_%(table_name)s_%(constraint_name)s
    # template, doubling the prefix (0017 hit the same thing on campaigns.status
    # and worked around it the same way). Raw SQL keeps the name exactly as
    # given, so downgrade() can drop it by the same literal name.
    op.execute(
        "ALTER TABLE campaign_disposition_definitions "
        "ADD CONSTRAINT ck_campaign_disposition_definitions_retry_delay_range "
        "CHECK (retry_delay_minutes IS NULL OR "
        "(retry_delay_minutes >= 5 AND retry_delay_minutes <= 10080))"
    )
    op.execute(
        "ALTER TABLE campaign_disposition_definitions "
        "ADD CONSTRAINT ck_campaign_disposition_definitions_immediate_redial_exclusive "
        "CHECK (NOT (immediate_redial AND (requires_callback_time OR causes_dnc)))"
    )
    op.add_column(
        "campaigns",
        sa.Column("disposition_policy_version", sa.Integer(), nullable=True),
    )

    flags_table = sa.table(
        "feature_flags", sa.column("flag_key", sa.String), sa.column("enabled", sa.Boolean)
    )
    op.bulk_insert(
        flags_table, [{"flag_key": "standard_dispositions_enabled", "enabled": False}]
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM feature_flags WHERE flag_key = 'standard_dispositions_enabled'"
    )
    op.drop_column("campaigns", "disposition_policy_version")
    op.execute(
        "ALTER TABLE campaign_disposition_definitions "
        "DROP CONSTRAINT ck_campaign_disposition_definitions_immediate_redial_exclusive"
    )
    op.execute(
        "ALTER TABLE campaign_disposition_definitions "
        "DROP CONSTRAINT ck_campaign_disposition_definitions_retry_delay_range"
    )
    op.drop_column("campaign_disposition_definitions", "policy_version")
    op.drop_column("campaign_disposition_definitions", "immediate_redial")
    op.drop_column("campaign_disposition_definitions", "retry_delay_minutes")
    op.drop_column("campaign_disposition_definitions", "is_standard")
