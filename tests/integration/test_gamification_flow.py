"""Integration tests for phase 4D Phase D: gamification preferences, private
daily progress, shared campaign milestones, and idempotent achievement
refresh - all against a real database.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.campaigns import retention as campaign_retention
from app.db import SessionLocal
from app.gamification import service as gamification_service
from app.gamification.service import GamificationPolicyError
from app.models.audit import AuditEvent
from app.models.base import utcnow
from app.models.campaign import Campaign, CampaignDispositionDefinition
from app.models.contact import CampaignContact, Contact
from app.models.gamification import AgentAchievement, AgentGamificationPreference
from app.models.work import CallAttempt, WorkItem
from tests.integration.conftest import make_user_with_role

pytestmark = pytest.mark.integration


def _campaign(db, *, timezone: str = "Africa/Harare", status: str = "active") -> Campaign:
    campaign = Campaign(
        owning_scope_type="organization",
        external_code=f"gam-{uuid.uuid4().hex[:8]}",
        name=f"Gamification test {uuid.uuid4().hex[:6]}",
        default_region="ZW",
        timezone=timezone,
        status=status,
    )
    db.add(campaign)
    db.flush()
    return campaign


def _disposition(db, campaign: Campaign) -> CampaignDispositionDefinition:
    disposition = CampaignDispositionDefinition(
        campaign_id=campaign.id, label="Connected", stable_semantic_code="connected",
        next_action="complete", counts_as_connected=True, active=True,
    )
    db.add(disposition)
    db.flush()
    return disposition


def _record_attempt(
    db,
    *,
    agent_id: uuid.UUID,
    campaign: Campaign,
    disposition: CampaignDispositionDefinition,
    source_lease_reason: str | None = None,
    created_at: datetime | None = None,
) -> CallAttempt:
    """Directly builds one distinct contact and one immutable attempt for it,
    bypassing the full lease/complete HTTP pipeline (already covered
    elsewhere) - gamification's own counting logic is what is under test.

    Uses a synthetic, always-unique fingerprint rather than
    zw_numbers()/protect(): this helper is called many times per test (up to
    50 in the achievement-threshold tests), and the realistic-number space
    (4 random digits) is far too small to stay collision-free against this
    suite's shared, never-rolled-back database across repeated runs."""
    unique = uuid.uuid4().hex
    contact = Contact(phone_ciphertext=f"synthetic:{unique}", phone_fingerprint=unique)
    db.add(contact)
    db.flush()
    campaign_contact = CampaignContact(
        campaign_id=campaign.id, contact_id=contact.id, status="completed",
        imported_at=utcnow(), completed_at=utcnow(),
    )
    db.add(campaign_contact)
    db.flush()
    work_item = WorkItem(campaign_contact_id=campaign_contact.id, state="completed", priority=0)
    db.add(work_item)
    db.flush()
    attempt = CallAttempt(
        work_item_id=work_item.id, campaign_contact_id=campaign_contact.id, agent_id=agent_id,
        disposition_definition_id=disposition.id, semantic_outcome=disposition.stable_semantic_code,
        idempotency_key=str(uuid.uuid4()), resulting_work_item_state="completed",
        source_lease_reason=source_lease_reason,
    )
    if created_at is not None:
        attempt.created_at = created_at
    db.add(attempt)
    db.flush()
    return attempt


def _agent() -> uuid.UUID:
    return make_user_with_role(f"gam-agent-{uuid.uuid4().hex[:8]}@example.com", "agent")


# --- preferences -----------------------------------------------------------------


def test_preference_is_none_until_explicitly_set():
    agent_id = _agent()
    with SessionLocal() as db:
        assert gamification_service.get_preference(db, agent_id) is None


def test_set_preference_creates_then_updates_the_same_row():
    agent_id = _agent()
    with SessionLocal() as db:
        gamification_service.set_preference(db, agent_id, enabled=True)
        db.commit()
        pref = db.get(AgentGamificationPreference, agent_id)
        assert pref is not None and pref.enabled is True and pref.celebrations_enabled is True

        gamification_service.set_preference(db, agent_id, celebrations_enabled=False, daily_goal=25)
        db.commit()
        pref = db.get(AgentGamificationPreference, agent_id)
        assert pref.enabled is True  # untouched by the second call
        assert pref.celebrations_enabled is False
        assert pref.daily_goal == 25


def test_set_preference_rejects_daily_goal_out_of_bounds():
    agent_id = _agent()
    with SessionLocal() as db:
        with pytest.raises(GamificationPolicyError):
            gamification_service.set_preference(db, agent_id, daily_goal=0)
        with pytest.raises(GamificationPolicyError):
            gamification_service.set_preference(db, agent_id, daily_goal=501)


def test_set_preference_can_clear_the_daily_goal():
    agent_id = _agent()
    with SessionLocal() as db:
        gamification_service.set_preference(db, agent_id, daily_goal=10)
        db.commit()
        gamification_service.set_preference(db, agent_id, clear_daily_goal=True)
        db.commit()
        pref = db.get(AgentGamificationPreference, agent_id)
        assert pref.daily_goal is None


def test_set_preference_records_an_audit_event():
    agent_id = _agent()
    with SessionLocal() as db:
        gamification_service.set_preference(db, agent_id, enabled=True, daily_goal=40)
        db.commit()
        event = db.scalar(
            select(AuditEvent).where(AuditEvent.action == "gamification.preference.update")
            .order_by(AuditEvent.occurred_at.desc())
        )
        assert event is not None
        assert event.actor_user_id == agent_id
        assert event.event_metadata["daily_goal"] == 40


# --- daily progress ----------------------------------------------------------------


def test_daily_progress_counts_distinct_contacts_separately_from_attempts():
    agent_id = _agent()
    with SessionLocal() as db:
        campaign = _campaign(db)
        disposition = _disposition(db, campaign)
        _record_attempt(db, agent_id=agent_id, campaign=campaign, disposition=disposition)
        _record_attempt(db, agent_id=agent_id, campaign=campaign, disposition=disposition)
        db.commit()

        progress = gamification_service.get_daily_progress(db, agent_id)
        assert progress.unique_contacts_handled_today == 2
        assert progress.attempts_recorded_today == 2


def test_repeated_attempts_on_one_contact_do_not_inflate_unique_progress():
    agent_id = _agent()
    with SessionLocal() as db:
        campaign = _campaign(db)
        disposition = _disposition(db, campaign)
        unique = uuid.uuid4().hex
        contact = Contact(phone_ciphertext=f"synthetic:{unique}", phone_fingerprint=unique)
        db.add(contact)
        db.flush()
        campaign_contact = CampaignContact(
            campaign_id=campaign.id, contact_id=contact.id, status="queued", imported_at=utcnow()
        )
        db.add(campaign_contact)
        db.flush()
        work_item = WorkItem(campaign_contact_id=campaign_contact.id, state="completed", priority=0)
        db.add(work_item)
        db.flush()
        for _ in range(3):
            db.add(
                CallAttempt(
                    work_item_id=work_item.id, campaign_contact_id=campaign_contact.id,
                    agent_id=agent_id, disposition_definition_id=disposition.id,
                    semantic_outcome="no_answer", idempotency_key=str(uuid.uuid4()),
                    resulting_work_item_state="retry_wait",
                )
            )
        db.commit()

        progress = gamification_service.get_daily_progress(db, agent_id)
        assert progress.unique_contacts_handled_today == 1
        assert progress.attempts_recorded_today == 3


def test_dnc_outcome_counts_toward_progress_like_any_other():
    agent_id = _agent()
    with SessionLocal() as db:
        campaign = _campaign(db)
        dnc_disposition = CampaignDispositionDefinition(
            campaign_id=campaign.id, label="Do Not Call", stable_semantic_code="explicit_dnc",
            causes_dnc=True, active=True,
        )
        db.add(dnc_disposition)
        db.flush()
        _record_attempt(db, agent_id=agent_id, campaign=campaign, disposition=dnc_disposition)
        db.commit()

        progress = gamification_service.get_daily_progress(db, agent_id)
        assert progress.unique_contacts_handled_today == 1  # no penalty, no special-casing


def test_daily_progress_uses_the_campaign_local_day_not_utc():
    """A record made just after local midnight in a timezone well ahead of
    UTC (Africa/Harare, UTC+2) must count as "today" even when it is still
    "yesterday" in UTC - proving the boundary is not an implicit UTC one."""
    agent_id = _agent()
    # Build a moment that is local-midnight-plus-a-minute in Harare but still
    # the previous UTC calendar day (Harare local time = UTC + 2h).
    local_zone = ZoneInfo("Africa/Harare")
    now_local = datetime.now(local_zone)
    local_midnight_plus_one = now_local.replace(
        hour=0, minute=1, second=0, microsecond=0
    )
    attempt_time_utc = local_midnight_plus_one.astimezone(UTC)

    with SessionLocal() as db:
        campaign = _campaign(db, timezone="Africa/Harare")
        disposition = _disposition(db, campaign)
        _record_attempt(
            db, agent_id=agent_id, campaign=campaign, disposition=disposition,
            created_at=attempt_time_utc,
        )
        db.commit()

        progress = gamification_service.get_daily_progress(db, agent_id)
        assert progress.unique_contacts_handled_today == 1


def test_daily_progress_falls_back_to_app_default_timezone_with_no_active_campaign():
    from app.config import get_settings

    agent_id = _agent()
    with SessionLocal() as db:
        campaign = _campaign(db)
        disposition = _disposition(db, campaign)
        _record_attempt(db, agent_id=agent_id, campaign=campaign, disposition=disposition)
        db.commit()

        zone = gamification_service._agent_timezone(db, agent_id)  # noqa: SLF001
        assert zone.key == get_settings().default_timezone


# --- shared campaign progress --------------------------------------------------------


def test_campaign_progress_is_bounded_and_reflects_resolved_contacts():
    with SessionLocal() as db:
        campaign = _campaign(db)
        for i in range(4):
            unique = uuid.uuid4().hex
            contact = Contact(phone_ciphertext=f"synthetic:{unique}", phone_fingerprint=unique)
            db.add(contact)
            db.flush()
            db.add(
                CampaignContact(
                    campaign_id=campaign.id, contact_id=contact.id,
                    status="completed" if i < 3 else "queued",
                    imported_at=utcnow(), completed_at=utcnow() if i < 3 else None,
                )
            )
        db.commit()

        progress = gamification_service.get_campaign_progress(db, campaign.id)
        assert progress is not None
        assert progress.total_contacts == 4
        assert progress.resolved_contacts == 3
        assert progress.progress_percent == 75


def test_campaign_progress_is_none_for_an_unknown_campaign():
    with SessionLocal() as db:
        assert gamification_service.get_campaign_progress(db, uuid.uuid4()) is None


def test_campaign_progress_is_zero_for_an_empty_campaign():
    with SessionLocal() as db:
        campaign = _campaign(db)
        db.commit()
        progress = gamification_service.get_campaign_progress(db, campaign.id)
        assert progress is not None
        assert progress.total_contacts == 0
        assert progress.progress_percent == 0


# --- achievement refresh --------------------------------------------------------------


def test_refresh_awards_first_outcome_after_one_attempt():
    agent_id = _agent()
    with SessionLocal() as db:
        campaign = _campaign(db)
        disposition = _disposition(db, campaign)
        _record_attempt(db, agent_id=agent_id, campaign=campaign, disposition=disposition)
        db.commit()

        awarded = gamification_service.refresh_agent_achievements(db, agent_id)
        db.commit()
        assert awarded == ["first_outcome"]

        codes = {a.achievement_code for a in gamification_service.list_achievements(db, agent_id)}
        assert codes == {"first_outcome"}


def test_refresh_awards_ten_and_fifty_contact_milestones_at_threshold():
    agent_id = _agent()
    with SessionLocal() as db:
        campaign = _campaign(db)
        disposition = _disposition(db, campaign)
        for _ in range(9):
            _record_attempt(db, agent_id=agent_id, campaign=campaign, disposition=disposition)
        db.commit()
        awarded = gamification_service.refresh_agent_achievements(db, agent_id)
        db.commit()
        assert "ten_contacts" not in awarded

        _record_attempt(db, agent_id=agent_id, campaign=campaign, disposition=disposition)
        db.commit()
        awarded = gamification_service.refresh_agent_achievements(db, agent_id)
        db.commit()
        assert "ten_contacts" in awarded
        assert "fifty_contacts" not in awarded

        for _ in range(40):
            _record_attempt(db, agent_id=agent_id, campaign=campaign, disposition=disposition)
        db.commit()
        awarded = gamification_service.refresh_agent_achievements(db, agent_id)
        db.commit()
        assert "fifty_contacts" in awarded
        # Already-earned achievements are never re-awarded.
        assert "ten_contacts" not in awarded


def test_refresh_awards_callback_follow_through_only_for_scheduled_callback_source():
    agent_id = _agent()
    with SessionLocal() as db:
        campaign = _campaign(db)
        disposition = _disposition(db, campaign)
        _record_attempt(
            db, agent_id=agent_id, campaign=campaign, disposition=disposition,
            source_lease_reason="normal",
        )
        db.commit()
        awarded = gamification_service.refresh_agent_achievements(db, agent_id)
        db.commit()
        assert "callback_follow_through" not in awarded

        _record_attempt(
            db, agent_id=agent_id, campaign=campaign, disposition=disposition,
            source_lease_reason="scheduled_callback",
        )
        db.commit()
        awarded = gamification_service.refresh_agent_achievements(db, agent_id)
        db.commit()
        assert "callback_follow_through" in awarded


def test_refresh_is_idempotent_across_repeated_calls():
    agent_id = _agent()
    with SessionLocal() as db:
        campaign = _campaign(db)
        disposition = _disposition(db, campaign)
        _record_attempt(db, agent_id=agent_id, campaign=campaign, disposition=disposition)
        db.commit()

        first = gamification_service.refresh_agent_achievements(db, agent_id)
        db.commit()
        second = gamification_service.refresh_agent_achievements(db, agent_id)
        db.commit()
        assert first == ["first_outcome"]
        assert second == []

        rows = db.scalars(
            select(AgentAchievement).where(AgentAchievement.user_id == agent_id)
        ).all()
        assert len(rows) == 1


def test_refresh_records_an_audit_event_for_each_new_award():
    agent_id = _agent()
    with SessionLocal() as db:
        campaign = _campaign(db)
        disposition = _disposition(db, campaign)
        _record_attempt(db, agent_id=agent_id, campaign=campaign, disposition=disposition)
        db.commit()
        gamification_service.refresh_agent_achievements(db, agent_id)
        db.commit()

        event = db.scalar(
            select(AuditEvent).where(AuditEvent.action == "gamification.achievement.award")
            .order_by(AuditEvent.occurred_at.desc())
        )
        assert event is not None
        assert event.event_metadata["achievement_code"] == "first_outcome"
        assert event.actor_user_id is None  # system-issued, not attributed to a caller


def test_list_achievements_is_scoped_to_one_agent():
    agent_a = _agent()
    agent_b = _agent()
    with SessionLocal() as db:
        campaign = _campaign(db)
        disposition = _disposition(db, campaign)
        _record_attempt(db, agent_id=agent_a, campaign=campaign, disposition=disposition)
        db.commit()
        gamification_service.refresh_agent_achievements(db, agent_a)
        db.commit()

        assert len(gamification_service.list_achievements(db, agent_a)) == 1
        assert gamification_service.list_achievements(db, agent_b) == []


# --- retention interaction --------------------------------------------------------


def test_achievements_survive_a_campaign_data_purge():
    """Plan Phase D: retention purge must leave no customer-linked game data
    - proven the other direction here, by showing a real purge that deletes
    the campaign's contacts and attempts has nothing to cascade into, because
    AgentAchievement never referenced them."""
    agent_id = _agent()
    with SessionLocal() as db:
        campaign = _campaign(db, status="completed")
        disposition = _disposition(db, campaign)
        _record_attempt(db, agent_id=agent_id, campaign=campaign, disposition=disposition)
        db.commit()
        gamification_service.refresh_agent_achievements(db, agent_id)
        db.commit()
        assert len(gamification_service.list_achievements(db, agent_id)) == 1

        campaign_retention.delete_completed_campaign_data(db, campaign, actor_id=None)
        db.commit()

        remaining_attempts = db.scalar(
            select(CallAttempt.id).join(CampaignContact).where(
                CampaignContact.campaign_id == campaign.id
            )
        )
        assert remaining_attempts is None  # the source data really is gone

        achievements = gamification_service.list_achievements(db, agent_id)
        assert len(achievements) == 1
        assert achievements[0].achievement_code == "first_outcome"
