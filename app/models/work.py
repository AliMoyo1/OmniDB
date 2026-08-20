"""Batches, work items, and immutable call attempts."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
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

    campaign_contact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaign_contacts.id"))
    batch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("batches.id"), nullable=True)
    campaign_user_assignment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("campaign_user_assignments.id"), nullable=True
    )
    assigned_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    state: Mapped[str] = mapped_column(String(20), default="queued")
    priority: Mapped[int] = mapped_column(Integer, default=0)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    lease_owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    lease_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, default=0)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CallAttempt(UUIDMixin, Base):
    """Immutable business event. No updates; corrections add a new linked attempt."""

    __tablename__ = "call_attempts"
    __table_args__ = (
        UniqueConstraint("agent_id", "idempotency_key", name="uq_call_attempts_agent_idem"),
    )

    work_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("work_items.id"))
    campaign_contact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaign_contacts.id"))
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    campaign_user_assignment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("campaign_user_assignments.id"), nullable=True
    )
    disposition_definition_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaign_disposition_definitions.id")
    )
    semantic_outcome: Mapped[str] = mapped_column(String(50))
    notes_ciphertext: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    self_reported_duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    explicit_dnc_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    callback_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(100))
    correction_of_attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("call_attempts.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
