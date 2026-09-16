"""Agent gamification: opt-in preferences and versioned achievements.

Deliberately campaign/contact-free (phase 4D plan 7.4): no campaign-contact ID,
phone fingerprint, contact name, or note ever lands in either table, so retention
purge of a campaign's contact data never has anything gamification-related to
remove, and these rows never carry a re-identification path back to a customer.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin, utcnow


class AgentGamificationPreference(TimestampMixin, Base):
    __tablename__ = "agent_gamification_preferences"
    __table_args__ = (
        CheckConstraint(
            "daily_goal IS NULL OR (daily_goal >= 1 AND daily_goal <= 500)",
            name="ck_agent_gamification_preferences_daily_goal_range",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    celebrations_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    daily_goal: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AgentAchievement(UUIDMixin, Base):
    __tablename__ = "agent_achievements"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "achievement_code", "criteria_version",
            name="uq_agent_achievements_user_code_version",
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    achievement_code: Mapped[str] = mapped_column(String(50))
    criteria_version: Mapped[int] = mapped_column(Integer)
    awarded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
