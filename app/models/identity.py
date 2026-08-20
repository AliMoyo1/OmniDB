"""Organization, team, user, and team-membership models."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class Organization(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(200), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="active")


class Team(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "teams"
    __table_args__ = (
        UniqueConstraint("organization_id", "external_code", name="uq_teams_org_external_code"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"))
    external_code: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(200))
    parent_team_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("teams.id"), nullable=True
    )
    default_timezone: Mapped[str] = mapped_column(String(64), default="Africa/Harare")
    status: Mapped[str] = mapped_column(String(20), default="active")


class User(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "users"

    # Workforce ID is the username (local part) of the login email, immutable once set (ADR-005C).
    workforce_id: Mapped[str] = mapped_column(String(150), unique=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    display_name: Mapped[str] = mapped_column(String(200))

    # Local accounts (ADR-004A). Nullable so a future OIDC account can exist without a password.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    totp_secret_ciphertext: Mapped[str | None] = mapped_column(String(512), nullable=True)
    totp_enrolled: Mapped[bool] = mapped_column(Boolean, default=False)
    identity_provider_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)

    workforce_status: Mapped[str] = mapped_column(String(20), default="active")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TeamMembership(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "team_memberships"

    team_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("teams.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    membership_status: Mapped[str] = mapped_column(String(20), default="active")
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
