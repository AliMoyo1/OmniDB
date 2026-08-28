"""A larger-scale, timed version of tests/concurrency/test_leasing_concurrency.py's
race: same invariant (no duplicate leases under real concurrent Postgres
transactions), but sized to say something about throughput under contention, not
just correctness. Not run by CI (see the `performance` marker in pyproject.toml) -
shared CI runners give noisy, meaningless throughput numbers, and the master plan
itself expects real performance validation to happen on approved hardware during
Phase 5 pilot prep, not on commodity CI. Run it yourself with:

    pytest tests/performance/test_leasing_throughput.py -m performance -s

The wall-clock bound asserted here is deliberately loose (a sanity ceiling, not an
SLA) - it exists to catch a catastrophic regression (e.g. lock contention gone
quadratic), not to gate on a specific number. The printed throughput is the actual
signal, for a human to read and compare across runs/hardware.
"""

from __future__ import annotations

import time
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

pytestmark = pytest.mark.performance

_AGENT_COUNT = 60
_ITEM_COUNT = 60
_DRAIN_SANITY_BOUND_SECONDS = 30


def _setup(numbers: list[str]) -> tuple[uuid.UUID, list[uuid.UUID]]:
    with SessionLocal() as db:
        campaign = Campaign(
            owning_scope_type="organization",
            name=f"Throughput test {uuid.uuid4().hex[:6]}",
            default_region="ZW",
            timezone="Africa/Harare",
            status="active",
        )
        db.add(campaign)
        db.flush()

        for number in numbers:
            protected = protect(number, "ZW")
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
                workforce_id=f"perf-agent-{i}-{unique}",
                email=f"perf-agent-{i}-{unique}@example.com",
                display_name="Throughput Agent",
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
    with SessionLocal() as db:
        result = work_service.lease_next(db, agent_id)
        db.commit()
        return str(result.work_item_id) if result else None


def test_leasing_throughput_under_full_contention():
    """Every agent races every other agent for the same pool at once - harder than
    a realistic call floor (agents don't all hit lease at the same instant in
    practice), deliberately, as a worst-case contention signal."""
    numbers = zw_numbers(_ITEM_COUNT)
    campaign_id, agent_ids = _setup(numbers)

    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=_AGENT_COUNT) as pool:
        results = list(pool.map(_lease_in_own_session, agent_ids))
    elapsed = time.perf_counter() - start

    leased = [r for r in results if r is not None]
    throughput = _ITEM_COUNT / elapsed if elapsed > 0 else float("inf")
    print(
        f"\nleasing throughput: {_ITEM_COUNT} items / {_AGENT_COUNT} agents "
        f"drained in {elapsed:.3f}s ({throughput:.1f} leases/sec)"
    )

    assert len(leased) == _ITEM_COUNT, "every item should have been leased to exactly one agent"
    assert len(set(leased)) == _ITEM_COUNT, "no work item was leased to more than one agent"
    assert elapsed < _DRAIN_SANITY_BOUND_SECONDS, (
        f"drain took {elapsed:.1f}s, over the {_DRAIN_SANITY_BOUND_SECONDS}s sanity "
        f"ceiling - not a hard perf gate, but this is slow enough to investigate"
    )

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
