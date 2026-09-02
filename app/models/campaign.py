"""Campaign, campaign-team/user assignments, and disposition definitions."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class Campaign(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "campaigns"

    owning_scope_type: Mapped[str] = mapped_column(String(30))
    owning_scope_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    owner_manager_role_assignment_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    # Stable operator-chosen identifier (mirrors Team.external_code) - bulk-import
    # templates reference a campaign by this, never by raw id.
    external_code: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    # Provenance (D-13): required before launch, validated in the service layer.
    purpose: Mapped[str | None] = mapped_column(String(500), nullable=True)
    data_source: Mapped[str | None] = mapped_column(String(300), nullable=True)
    data_obtained_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    lawful_basis_or_consent_reference: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )

    default_region: Mapped[str] = mapped_column(String(2), default="ZW")
    timezone: Mapped[str] = mapped_column(String(64), default="Africa/Harare")
    status: Mapped[str] = mapped_column(String(20), default="draft")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    launched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Completion-triggered retention (ADR-020).
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retention_delete_after: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class CampaignTeamAssignment(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "campaign_team_assignments"

    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id"))
    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id"))
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    staffing_capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class CampaignUserAssignment(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "campaign_user_assignments"

    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    team_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("teams.id"), nullable=True)
    campaign_role: Mapped[str] = mapped_column(String(30))
    assignment_type: Mapped[str] = mapped_column(String(30), default="primary")
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    priority: Mapped[int | None] = mapped_column(Integer, nullable=True)
    allocation_percentage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shift_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reason_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class CampaignDispositionDefinition(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "campaign_disposition_definitions"
    __table_args__ = (
        UniqueConstraint(
            "campaign_id", "stable_semantic_code", name="uq_disposition_campaign_code"
        ),
    )

    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id"))
    label: Mapped[str] = mapped_column(String(100))
    stable_semantic_code: Mapped[str] = mapped_column(String(50))
    next_action: Mapped[str | None] = mapped_column(String(50), nullable=True)
    requires_notes: Mapped[bool] = mapped_column(Boolean, default=False)
    requires_callback_time: Mapped[bool] = mapped_column(Boolean, default=False)
    counts_as_connected: Mapped[bool] = mapped_column(Boolean, default=False)
    counts_as_conversion: Mapped[bool] = mapped_column(Boolean, default=False)
    # Marks the protected explicit-DNC disposition (ADR-009). Only approved codes may set this.
    causes_dnc: Mapped[bool] = mapped_column(Boolean, default=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
