"""Add one-time activation tokens and recent reauthentication timestamps.

Revision ID: 0005_activation_step_up
Revises: 0004_work_item_skip_count
Create Date: 2026-08-21

Note: kept short (<=32 chars) because Alembic's own alembic_version.version_num
bookkeeping column defaults to VARCHAR(32); a longer id raises
StringDataRightTruncation on upgrade, caught only by actually running this
migration against real Postgres (2026-08-21).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_activation_step_up"
down_revision: str | None = "0004_work_item_skip_count"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column("reauthenticated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE sessions SET reauthenticated_at = created_at")

    op.create_table(
        "activation_tokens",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column(
            "purpose",
            sa.String(30),
            server_default=sa.text("'password_activation'"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_activation_tokens_created_by_users"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_activation_tokens_user_id_users"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_activation_tokens"),
        sa.UniqueConstraint("token_hash", name="uq_activation_tokens_token_hash"),
    )
    op.create_index(
        "ix_activation_tokens_user_id", "activation_tokens", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_activation_tokens_user_id", table_name="activation_tokens")
    op.drop_table("activation_tokens")
    op.drop_column("sessions", "reauthenticated_at")
