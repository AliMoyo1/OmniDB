"""Effective-dated authority: role assignments, reporting lines, delegations."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class RoleAssignment(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "role_assignments"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    role_code: Mapped[str] = mapped_column(String(50))
    scope_type: Mapped[str] = mapped_column(String(30))
    scope_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    appointment_type: Mapped[str] = mapped_column(String(30), default="permanent")
    reason_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    appointed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class ReportingAssignment(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "reporting_assignments"

    subordinate_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    supervisor_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    context_type: Mapped[str] = mapped_column(String(30), default="organization")
    context_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    assignment_type: Mapped[str] = mapped_column(String(30), default="primary")
    status: Mapped[str] = mapped_column(String(20), default="active")
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reason_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Delegation(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "delegations"

    delegator_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    delegate_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    capability_set: Mapped[list[str]] = mapped_column(JSONB, default=list)
    scope_type: Mapped[str] = mapped_column(String(30))
    scope_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reason_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
