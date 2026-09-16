"""Add agent gamification tables (phase 4D plan 7.4) and the
agent_gamification_enabled rollout flag.

Purely additive and campaign/contact-free - neither new table references a
campaign, contact, or work item, so this migration touches nothing an agent's
existing calling permissions or history depend on. The flag seeds false, so
no UI or task behavior changes until the pilot explicitly turns it on.

Revision ID: 0021_agent_gamification
Revises: 0020_retry_wait_and_redial
Create Date: 2026-09-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_agent_gamification"
down_revision: str | None = "0020_retry_wait_and_redial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_gamification_preferences",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "celebrations_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("daily_goal", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("user_id", name="pk_agent_gamification_preferences"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_agent_gamification_preferences_user_id_users"
        ),
    )
    op.execute(
        "ALTER TABLE agent_gamification_preferences "
        "ADD CONSTRAINT ck_agent_gamification_preferences_daily_goal_range "
        "CHECK (daily_goal IS NULL OR (daily_goal >= 1 AND daily_goal <= 500))"
    )

    op.create_table(
        "agent_achievements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("achievement_code", sa.String(length=50), nullable=False),
        sa.Column("criteria_version", sa.Integer(), nullable=False),
        sa.Column("awarded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_agent_achievements"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_agent_achievements_user_id_users"
        ),
        sa.UniqueConstraint(
            "user_id", "achievement_code", "criteria_version",
            name="uq_agent_achievements_user_code_version",
        ),
    )
    op.create_index(
        "ix_agent_achievements_user_id", "agent_achievements", ["user_id"]
    )

    flags_table = sa.table(
        "feature_flags", sa.column("flag_key", sa.String), sa.column("enabled", sa.Boolean)
    )
    op.bulk_insert(
        flags_table, [{"flag_key": "agent_gamification_enabled", "enabled": False}]
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM feature_flags WHERE flag_key = 'agent_gamification_enabled'"
    )
    op.drop_index("ix_agent_achievements_user_id", table_name="agent_achievements")
    op.drop_table("agent_achievements")
    op.drop_table("agent_gamification_preferences")
