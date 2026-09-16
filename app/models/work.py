"""Batches, work items, and immutable call attempts."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin, utcnow


class Batch(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "batches"

    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id"))
    name: Mapped[str] = mapped_column(String(200))
    assignment_mode: Mapped[str] = mapped_column(String(20), default="shared_pool")
    assigned_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), default="active")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkItem(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "work_items"
    __table_args__ = (
        Index(
            "uq_work_items_one_active_lease",
            "lease_owner_id",
            unique=True,
            postgresql_where=text("state = 'leased' AND lease_owner_id IS NOT NULL"),
        ),
        CheckConstraint(
            "lease_reason IS NULL OR lease_reason IN "
            "('normal', 'scheduled_callback', 'delayed_retry', 'immediate_redial')",
            name="ck_work_items_lease_reason",
        ),
    )

    campaign_contact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaign_contacts.id"))
    batch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("batches.id"), nullable=True)
    campaign_user_assignment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "campaign_user_assignments.id",
            # The naming convention's auto-generated name for this column/table
            # pair is 67 chars, over Postgres's 63-char limit - migration 0002
            # already hand-shortened it in the database; this makes the model
            # describe the same name instead of silently diverging from it
            # (autogenerate would otherwise see this as a rename every time).
            name="fk_work_items_cua_id",
        ),
        nullable=True,
    )
    assigned_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    state: Mapped[str] = mapped_column(String(20), default="queued")
    priority: Mapped[int] = mapped_column(Integer, default=0)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    skip_count: Mapped[int] = mapped_column(Integer, default=0)
    lease_owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    lease_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, default=0)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Why this item is currently (or was most recently) leased - normal,
    # scheduled_callback, delayed_retry, or immediate_redial (phase 4D plan
    # 6.3, 6.8). Set on every transition into "leased"; read back at
    # completion to snapshot CallAttempt.source_lease_reason.
    lease_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)


class CallAttempt(UUIDMixin, Base):
    """Immutable business event. No updates; corrections add a new linked attempt."""

    __tablename__ = "call_attempts"
    __table_args__ = (
        UniqueConstraint("agent_id", "idempotency_key", name="uq_call_attempts_agent_idem"),
        CheckConstraint(
            "source_lease_reason IS NULL OR source_lease_reason IN "
            "('normal', 'scheduled_callback', 'delayed_retry', 'immediate_redial')",
            name="ck_call_attempts_source_lease_reason",
        ),
    )

    work_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("work_items.id"))
    campaign_contact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaign_contacts.id"))
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    campaign_user_assignment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "campaign_user_assignments.id",
            # The naming convention's auto-generated name for this column/table
            # pair is 70 chars, over Postgres's 63-char limit - migration 0002
            # already hand-shortened it in the database; this makes the model
            # describe the same name instead of silently diverging from it
            # (autogenerate would otherwise see this as a rename every time).
            name="fk_call_attempts_cua_id",
        ),
        nullable=True,
    )
    disposition_definition_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(
            "campaign_disposition_definitions.id",
            # The naming convention's auto-generated name for this column/table
            # pair is 75 chars, over Postgres's 63-char limit - migration 0002
            # already hand-shortened it in the database; this makes the model
            # describe the same name instead of silently diverging from it
            # (autogenerate would otherwise see this as a rename every time).
            name="fk_call_attempts_disposition_id",
        )
    )
    semantic_outcome: Mapped[str] = mapped_column(String(50))
    notes_ciphertext: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    self_reported_duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    explicit_dnc_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    callback_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(100))
    resulting_work_item_state: Mapped[str] = mapped_column(String(20))
    correction_of_attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("call_attempts.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    # Non-sensitive event snapshot (phase 4D plan 6.3): the work item's
    # lease_reason at the moment of this attempt. Immutable, unlike the work
    # item itself - used for reproducible callback-achievement criteria and
    # operational reporting rather than inferring it from a mutable row.
    source_lease_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # Set only when this attempt renewed the same agent's lease in place
    # (Hung Up / immediate_redial). Persisted so an idempotent replay of the
    # same completion key returns the original renewed lease rather than
    # minting or rotating a second one.
    resulting_lease_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    resulting_lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
