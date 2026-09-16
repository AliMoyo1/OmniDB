"""Integration tests for the gamification Celery tasks (plan 7.5). Tests run
with CELERY_TASK_ALWAYS_EAGER=true (tests/conftest.py), so .delay() executes
the task body synchronously in-process against the real test database.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.flags import service as flags
from app.gamification.tasks import (
    reconcile_agent_achievements_task,
    refresh_agent_achievements_task,
)
from app.models.campaign import Campaign, CampaignDispositionDefinition
from app.models.contact import CampaignContact, Contact
from app.models.gamification import AgentAchievement
from app.models.work import CallAttempt, WorkItem
from app.security.phone import protect
from tests.integration.conftest import make_user_with_role, zw_numbers

pytestmark = pytest.mark.integration


def _set_flag(enabled: bool) -> None:
    with SessionLocal() as db:
        actor_id = make_user_with_role(f"gam-flag-{uuid.uuid4().hex[:8]}@example.com", "manager")
        flags.set_flag(db, "agent_gamification_enabled", enabled, actor_id=actor_id)
        db.commit()


@pytest.fixture(autouse=True)
def _reset_gamification_flag_after_each_test():
    # This flag is shared, non-rolled-back state (like every other feature
    # flag in this suite) - always leave it back at its seeded default so an
    # unrelated test elsewhere never inherits "enabled" from this file.
    yield
    _set_flag(False)


def _agent_with_one_attempt() -> uuid.UUID:
    agent_id = make_user_with_role(f"gam-task-agent-{uuid.uuid4().hex[:8]}@example.com", "agent")
    with SessionLocal() as db:
        campaign = Campaign(
            owning_scope_type="organization", external_code=f"gam-task-{uuid.uuid4().hex[:8]}",
            name="Gamification task test", default_region="ZW", timezone="Africa/Harare",
            status="active",
        )
        db.add(campaign)
        db.flush()
        disposition = CampaignDispositionDefinition(
            campaign_id=campaign.id, label="Connected", stable_semantic_code="connected",
            next_action="complete", counts_as_connected=True, active=True,
        )
        db.add(disposition)
        db.flush()
        number = zw_numbers(1)[0]
        protected = protect(number, "ZW")
        contact = Contact(
            phone_ciphertext=protected.ciphertext, phone_fingerprint=protected.fingerprint
        )
        db.add(contact)
        db.flush()
        campaign_contact = CampaignContact(
            campaign_id=campaign.id, contact_id=contact.id, status="completed",
            imported_at=datetime.now(UTC), completed_at=datetime.now(UTC),
        )
        db.add(campaign_contact)
        db.flush()
        work_item = WorkItem(campaign_contact_id=campaign_contact.id, state="completed", priority=0)
        db.add(work_item)
        db.flush()
        db.add(
            CallAttempt(
                work_item_id=work_item.id, campaign_contact_id=campaign_contact.id,
                agent_id=agent_id, disposition_definition_id=disposition.id,
                semantic_outcome="connected", idempotency_key=str(uuid.uuid4()),
                resulting_work_item_state="completed",
            )
        )
        db.commit()
    return agent_id


def _achievement_count(agent_id: uuid.UUID) -> int:
    with SessionLocal() as db:
        return len(
            db.scalars(
                select(AgentAchievement).where(AgentAchievement.user_id == agent_id)
            ).all()
        )


def test_refresh_task_noops_when_the_flag_is_disabled():
    agent_id = _agent_with_one_attempt()
    _set_flag(False)
    result = refresh_agent_achievements_task.delay(str(agent_id))
    assert result.get() == []
    assert _achievement_count(agent_id) == 0


def test_refresh_task_awards_when_the_flag_is_enabled():
    agent_id = _agent_with_one_attempt()
    _set_flag(True)
    result = refresh_agent_achievements_task.delay(str(agent_id))
    assert result.get() == ["first_outcome"]
    assert _achievement_count(agent_id) == 1


def test_reconcile_task_noops_when_the_flag_is_disabled():
    agent_id = _agent_with_one_attempt()
    _set_flag(False)
    result = reconcile_agent_achievements_task.delay()
    assert result.get() == 0
    assert _achievement_count(agent_id) == 0


def test_reconcile_task_catches_up_a_missed_award():
    """Simulates a dropped enqueue: an attempt was recorded but the
    per-completion refresh_agent_achievements_task never ran. The periodic
    reconciliation task must still find and award it."""
    agent_id = _agent_with_one_attempt()
    _set_flag(True)
    assert _achievement_count(agent_id) == 0

    result = reconcile_agent_achievements_task.delay()
    assert result.get() >= 1
    assert _achievement_count(agent_id) == 1
