"""Append-only audit events. No raw personal data is stored here (plan 9.8)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDMixin, utcnow


class AuditEvent(UUIDMixin, Base):
    __tablename__ = "audit_events"

    # Append-only: no updated_at, and rows are never modified or deleted
    # (enforced in the service layer, not just here).
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), index=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    actor_role: Mapped[str | None] = mapped_column(String(50), nullable=True)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    team_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    result: Mapped[str] = mapped_column(String(20))
    target_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    target_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent_summary: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reason_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Stored under DB column "metadata"; attribute renamed to avoid the reserved name.
    event_metadata: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
