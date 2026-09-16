"""Two concurrent refresh calls for the same agent, both seeing the same
earned criterion, must still produce exactly one achievement row (plan 7.5:
"insert with the unique constraint so retries cannot duplicate an award").
Real threads and real connections, mirroring test_leasing_concurrency.py.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.gamification import service as gamification_service
from app.models.campaign import Campaign, CampaignDispositionDefinition
from app.models.contact import CampaignContact, Contact
from app.models.gamification import AgentAchievement
from app.models.identity import User
from app.models.work import CallAttempt, WorkItem
from app.security.passwords import hash_password
from app.security.phone import protect
from tests.integration.conftest import zw_numbers

pytestmark = pytest.mark.integration


def _setup() -> uuid.UUID:
    with SessionLocal() as db:
        unique = uuid.uuid4().hex[:8]
        agent = User(
            workforce_id=f"conc-gam-agent-{unique}",
            email=f"conc-gam-agent-{unique}@example.com",
            display_name="Concurrency Agent",
            password_hash=hash_password("not-used-in-this-test"),
        )
        db.add(agent)
        db.flush()

        campaign = Campaign(
            owning_scope_type="organization",
            external_code=f"gam-conc-{unique}",
            name="Gamification concurrency test",
            default_region="ZW",
            timezone="Africa/Harare",
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
                agent_id=agent.id, disposition_definition_id=disposition.id,
                semantic_outcome="connected", idempotency_key=str(uuid.uuid4()),
                resulting_work_item_state="completed",
            )
        )
        db.commit()
        return agent.id


def _refresh_in_own_session(agent_id: uuid.UUID) -> list[str]:
    with SessionLocal() as db:
        awarded = gamification_service.refresh_agent_achievements(db, agent_id)
        db.commit()
        return awarded


def test_concurrent_refresh_awards_the_achievement_exactly_once():
    agent_id = _setup()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(_refresh_in_own_session, [agent_id, agent_id]))

    awarded_by_someone = [code for result in results for code in result]
    assert awarded_by_someone == ["first_outcome"], (
        "exactly one of the two concurrent calls should have won the insert"
    )

    with SessionLocal() as db:
        rows = db.scalars(
            select(AgentAchievement).where(
                AgentAchievement.user_id == agent_id,
                AgentAchievement.achievement_code == "first_outcome",
            )
        ).all()
        assert len(rows) == 1
