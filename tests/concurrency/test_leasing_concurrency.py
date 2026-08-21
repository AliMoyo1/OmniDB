"""N agents leasing simultaneously from a small shared pool must never receive the
same work item (invariant 5). This proves the SELECT ... FOR UPDATE SKIP LOCKED
strategy holds under genuinely concurrent Postgres transactions, not a simulation:
each thread opens its own session/connection and races for real against the others.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models.campaign import Campaign, CampaignUserAssignment
from app.models.contact import CampaignContact, Contact
from app.models.identity import User
from app.models.work import WorkItem
from app.security.passwords import hash_password
from app.security.phone import protect
from app.work import service as work_service
from tests.integration.conftest import zw_numbers

pytestmark = pytest.mark.integration

_AGENT_COUNT = 10
_ITEM_COUNT = 6


def _setup(numbers: list[str]) -> tuple[uuid.UUID, list[uuid.UUID]]:
    with SessionLocal() as db:
        campaign = Campaign(
            owning_scope_type="organization",
            name=f"Concurrency test {uuid.uuid4().hex[:6]}",
            default_region="ZW",
            timezone="Africa/Harare",
            status="active",
        )
        db.add(campaign)
        db.flush()

        for number in numbers:
            protected = protect(number, "ZW")
            contact = db.scalar(
                select(Contact).where(Contact.phone_fingerprint == protected.fingerprint)
            )
            if contact is None:
                contact = Contact(
                    phone_ciphertext=protected.ciphertext,
                    phone_fingerprint=protected.fingerprint,
                )
                db.add(contact)
                db.flush()
            campaign_contact = CampaignContact(
                campaign_id=campaign.id,
                contact_id=contact.id,
                status="queued",
                imported_at=datetime.now(UTC),
            )
            db.add(campaign_contact)
            db.flush()
            db.add(WorkItem(campaign_contact_id=campaign_contact.id, state="queued", priority=0))

        agent_ids = []
        for i in range(_AGENT_COUNT):
            unique = uuid.uuid4().hex[:8]
            agent = User(
                workforce_id=f"conc-agent-{i}-{unique}",
                email=f"conc-agent-{i}-{unique}@example.com",
                display_name="Concurrency Agent",
                password_hash=hash_password("not-used-in-this-test"),
            )
            db.add(agent)
            db.flush()
            db.add(
                CampaignUserAssignment(
                    campaign_id=campaign.id,
                    user_id=agent.id,
                    campaign_role="agent",
                    assignment_type="primary",
                    effective_from=datetime.now(UTC),
                    status="active",
                )
            )
            agent_ids.append(agent.id)

        db.commit()
        return campaign.id, agent_ids


def _lease_in_own_session(agent_id: uuid.UUID) -> str | None:
    # A fresh session per thread: SQLAlchemy sessions are not thread-safe to share,
    # and a genuine concurrency test needs genuinely separate DB connections racing.
    with SessionLocal() as db:
        result = work_service.lease_next(db, agent_id)
        db.commit()
        return str(result.work_item_id) if result else None


def test_concurrent_leasing_yields_no_duplicate_active_leases():
    numbers = zw_numbers(_ITEM_COUNT)
    campaign_id, agent_ids = _setup(numbers)

    with ThreadPoolExecutor(max_workers=_AGENT_COUNT) as pool:
        results = list(pool.map(_lease_in_own_session, agent_ids))

    leased = [r for r in results if r is not None]
    assert len(leased) == _ITEM_COUNT, "every item should have been leased to exactly one agent"
    assert len(set(leased)) == _ITEM_COUNT, "no work item was leased to more than one agent"

    with SessionLocal() as db:
        items = db.scalars(
            select(WorkItem)
            .join(CampaignContact, WorkItem.campaign_contact_id == CampaignContact.id)
            .where(CampaignContact.campaign_id == campaign_id)
        ).all()
        assert len(items) == _ITEM_COUNT
        assert all(item.state == "leased" for item in items)
        owners = [item.lease_owner_id for item in items]
        assert len(set(owners)) == _ITEM_COUNT, "each leased item has a distinct owner"
