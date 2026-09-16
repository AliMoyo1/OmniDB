"""Agent gamification: preferences, private daily progress, shared campaign
milestones, and versioned achievements (phase 4D plan 7).

Everything here is read-scoped to the requesting agent's own rows or to a
campaign's aggregate totals - never another agent's preferences, progress, or
achievements (plan 7.6). Progress is computed live from immutable CallAttempt
rows, the same authoritative source app.reporting.agent_stats already uses,
not a separate mutable counter.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.config import get_settings
from app.models.base import utcnow
from app.models.campaign import Campaign, CampaignUserAssignment
from app.models.contact import CampaignContact
from app.models.gamification import AgentAchievement, AgentGamificationPreference
from app.models.work import CallAttempt

MIN_DAILY_GOAL = 1
MAX_DAILY_GOAL = 500

ACHIEVEMENT_CRITERIA_VERSION = 1


class GamificationPolicyError(Exception):
    pass


@dataclass(frozen=True)
class AchievementDefinition:
    code: str
    display_name: str
    description: str


# Celebrates workflow participation only - never call quality, DNC avoidance,
# speed, or a comparison against another agent (plan 7.3).
ACHIEVEMENTS: tuple[AchievementDefinition, ...] = (
    AchievementDefinition("first_outcome", "First Step", "Recorded your first call outcome."),
    AchievementDefinition(
        "ten_contacts", "Building Momentum", "Handled ten distinct contacts."
    ),
    AchievementDefinition(
        "fifty_contacts", "Steady Contributor", "Handled fifty distinct contacts."
    ),
    AchievementDefinition(
        "callback_follow_through",
        "Follow-through",
        "Handled a scheduled callback right when it was due.",
    ),
)
ACHIEVEMENTS_BY_CODE: dict[str, AchievementDefinition] = {a.code: a for a in ACHIEVEMENTS}


@dataclass(frozen=True)
class DailyProgress:
    unique_contacts_handled_today: int
    attempts_recorded_today: int
    daily_goal: int | None


@dataclass(frozen=True)
class CampaignProgress:
    campaign_id: uuid.UUID
    campaign_name: str
    resolved_contacts: int
    total_contacts: int
    progress_percent: int


def get_agent_primary_campaign(db: Session, agent_id: uuid.UUID) -> Campaign | None:
    """The agent's one active primary campaign assignment, if any - the same
    "active campaign" scope plan 7.6 authorizes for aggregate progress."""
    now = utcnow()
    return db.scalar(
        select(Campaign)
        .join(CampaignUserAssignment, CampaignUserAssignment.campaign_id == Campaign.id)
        .where(
            CampaignUserAssignment.user_id == agent_id,
            CampaignUserAssignment.campaign_role == "agent",
            CampaignUserAssignment.assignment_type == "primary",
            CampaignUserAssignment.status == "active",
            CampaignUserAssignment.effective_from <= now,
            or_(
                CampaignUserAssignment.effective_to.is_(None),
                CampaignUserAssignment.effective_to > now,
            ),
        )
    )


def _agent_timezone(db: Session, agent_id: uuid.UUID) -> ZoneInfo:
    """The timezone of the agent's active primary campaign, or the
    application's configured default when they have none (plan 7.2) - never
    an implicit UTC boundary."""
    campaign = get_agent_primary_campaign(db, agent_id)
    name = campaign.timezone if campaign is not None else get_settings().default_timezone
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def get_preference(db: Session, agent_id: uuid.UUID) -> AgentGamificationPreference | None:
    """None means no row yet - callers treat that as the documented defaults
    (disabled, celebrations on, no goal), the same fail-safe shape as an
    unseeded feature flag."""
    return db.get(AgentGamificationPreference, agent_id)


def set_preference(
    db: Session,
    agent_id: uuid.UUID,
    *,
    enabled: bool | None = None,
    celebrations_enabled: bool | None = None,
    daily_goal: int | None = None,
    clear_daily_goal: bool = False,
) -> AgentGamificationPreference:
    if daily_goal is not None and not clear_daily_goal:
        if not (MIN_DAILY_GOAL <= daily_goal <= MAX_DAILY_GOAL):
            raise GamificationPolicyError(
                f"daily goal must be between {MIN_DAILY_GOAL} and {MAX_DAILY_GOAL}"
            )
    preference = db.get(AgentGamificationPreference, agent_id)
    if preference is None:
        preference = AgentGamificationPreference(user_id=agent_id)
        db.add(preference)
    if enabled is not None:
        preference.enabled = enabled
    if celebrations_enabled is not None:
        preference.celebrations_enabled = celebrations_enabled
    if clear_daily_goal:
        preference.daily_goal = None
    elif daily_goal is not None:
        preference.daily_goal = daily_goal
    db.flush()
    record_audit(
        db, action="gamification.preference.update", result="success", actor_user_id=agent_id,
        target_type="user", target_id=agent_id,
        event_metadata={
            "enabled": preference.enabled,
            "celebrations_enabled": preference.celebrations_enabled,
            "daily_goal": preference.daily_goal,
        },
    )
    return preference


def get_daily_progress(db: Session, agent_id: uuid.UUID) -> DailyProgress:
    zone = _agent_timezone(db, agent_id)
    day_start_utc = datetime.now(zone).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).astimezone(ZoneInfo("UTC"))

    unique_contacts = db.scalar(
        select(func.count(func.distinct(CallAttempt.campaign_contact_id))).where(
            CallAttempt.agent_id == agent_id, CallAttempt.created_at >= day_start_utc
        )
    ) or 0
    attempts = db.scalar(
        select(func.count(CallAttempt.id)).where(
            CallAttempt.agent_id == agent_id, CallAttempt.created_at >= day_start_utc
        )
    ) or 0
    preference = get_preference(db, agent_id)
    return DailyProgress(
        unique_contacts_handled_today=unique_contacts,
        attempts_recorded_today=attempts,
        daily_goal=preference.daily_goal if preference else None,
    )


def get_campaign_progress(db: Session, campaign_id: uuid.UUID) -> CampaignProgress | None:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        return None
    total = db.scalar(
        select(func.count(CampaignContact.id)).where(
            CampaignContact.campaign_id == campaign_id
        )
    ) or 0
    resolved = db.scalar(
        select(func.count(CampaignContact.id)).where(
            CampaignContact.campaign_id == campaign_id, CampaignContact.completed_at.is_not(None)
        )
    ) or 0
    percent = max(0, min(100, int(resolved / total * 100))) if total else 0
    return CampaignProgress(
        campaign_id=campaign.id, campaign_name=campaign.name,
        resolved_contacts=resolved, total_contacts=total, progress_percent=percent,
    )


def list_achievements(db: Session, agent_id: uuid.UUID) -> list[AgentAchievement]:
    return list(
        db.scalars(
            select(AgentAchievement)
            .where(AgentAchievement.user_id == agent_id)
            .order_by(AgentAchievement.awarded_at.desc())
        )
    )


def refresh_agent_achievements(db: Session, agent_id: uuid.UUID) -> list[str]:
    """Idempotent (plan 7.5): safe to call repeatedly - after every
    completion, from the periodic reconciliation task, or by hand. Each
    candidate is inserted with ON CONFLICT DO NOTHING against the
    (user_id, achievement_code, criteria_version) unique constraint, so a
    concurrent or replayed call can never double-award. Never raises for an
    agent with no activity yet - every criterion below is simply false."""
    distinct_contacts = db.scalar(
        select(func.count(func.distinct(CallAttempt.campaign_contact_id))).where(
            CallAttempt.agent_id == agent_id
        )
    ) or 0
    total_attempts = db.scalar(
        select(func.count(CallAttempt.id)).where(CallAttempt.agent_id == agent_id)
    ) or 0
    # The immutable per-attempt snapshot (plan 6.3), not a mutable work-item
    # field - a callback that was later reassigned or reclaimed cannot
    # retroactively change whether this agent actually handled it while due.
    handled_a_due_callback = (
        db.scalar(
            select(CallAttempt.id)
            .where(
                CallAttempt.agent_id == agent_id,
                CallAttempt.source_lease_reason == "scheduled_callback",
            )
            .limit(1)
        )
        is not None
    )

    earned = {
        "first_outcome": total_attempts >= 1,
        "ten_contacts": distinct_contacts >= 10,
        "fifty_contacts": distinct_contacts >= 50,
        "callback_follow_through": handled_a_due_callback,
    }

    awarded_now: list[str] = []
    for code, is_earned in earned.items():
        if not is_earned:
            continue
        stmt = (
            pg_insert(AgentAchievement)
            .values(
                id=uuid.uuid4(),
                user_id=agent_id,
                achievement_code=code,
                criteria_version=ACHIEVEMENT_CRITERIA_VERSION,
                awarded_at=utcnow(),
            )
            .on_conflict_do_nothing(constraint="uq_agent_achievements_user_code_version")
            .returning(AgentAchievement.id)
        )
        if db.execute(stmt).first() is not None:
            awarded_now.append(code)

    for code in awarded_now:
        record_audit(
            db, action="gamification.achievement.award", result="success", actor_user_id=None,
            target_type="user", target_id=agent_id,
            event_metadata={
                "achievement_code": code, "criteria_version": ACHIEVEMENT_CRITERIA_VERSION,
            },
        )
    return awarded_now
