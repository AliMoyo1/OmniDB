"""In-app notification inbox (Phase 0 decision: "in-app inbox notifications +
dormant email capability").

Deliberately carries no raw personal data - a notification references an entity
(an import job, a campaign) by id and describes it with operator-chosen text (a
filename, a campaign name), never a contact's phone or other PII, the same
boundary app/models/audit.py holds.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class Notification(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "notifications"
    __table_args__ = (
        # The inbox and the unread badge both query "this recipient's rows,
        # optionally only the unread (read_at IS NULL) ones" - one composite
        # index serves both.
        Index("ix_notifications_recipient_unread", "recipient_user_id", "read_at"),
    )

    recipient_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    category: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # Optional pointer to what the notification is about, so the inbox can link
    # through to it. Untyped id (no FK) because it can reference any of several
    # tables and the notification must outlive the entity it mentions.
    related_entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    related_entity_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
