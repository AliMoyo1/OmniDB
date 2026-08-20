"""Contacts, campaign contacts, and do-not-call suppression."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class Contact(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "contacts"
    __table_args__ = (
        UniqueConstraint("phone_fingerprint", name="uq_contacts_phone_fingerprint"),
    )

    # The number is encrypted at rest; the fingerprint is a keyed HMAC for exact matching.
    phone_ciphertext: Mapped[str] = mapped_column(String(1024))
    phone_fingerprint: Mapped[str] = mapped_column(String(64))
    phone_key_version: Mapped[int] = mapped_column(Integer, default=1)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)


class CampaignContact(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "campaign_contacts"
    __table_args__ = (
        UniqueConstraint(
            "campaign_id", "contact_id", name="uq_campaign_contacts_campaign_contact"
        ),
    )

    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id"))
    contact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contacts.id"))
    original_phone_protected: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    campaign_name_value: Mapped[str | None] = mapped_column(String(200), nullable=True)
    approved_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    source_row_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Completion tracking for retention (ADR-020): disposition + calling agent.
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_by_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    final_disposition_code: Mapped[str | None] = mapped_column(String(50), nullable=True)


class SuppressionEntry(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "suppression_entries"

    organization_scope: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    phone_fingerprint: Mapped[str] = mapped_column(String(64))
    protected_phone_value: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    source: Mapped[str] = mapped_column(String(40))
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    corrected_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    corrected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    correction_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
