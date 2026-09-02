"""A campaign team's staffing_capacity must hold under genuinely concurrent
assignment requests, not just the one-at-a-time case
tests/integration/test_campaign_assignment_flow.py::test_staffing_capacity_is_enforced
already covers. _check_staffing_capacity reads a count and the caller inserts a row
afterward with no lock spanning the two - each thread here opens its own
session/connection and races for real against the others, the same pattern
test_leasing_concurrency.py already established for the leasing invariant.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.campaigns import service as campaign_service
from app.campaigns.service import CampaignAssignmentError
from app.db import SessionLocal
from app.models.campaign import Campaign, CampaignTeamAssignment, CampaignUserAssignment
from app.models.identity import Organization, Team, User
from app.security.passwords import hash_password

pytestmark = pytest.mark.integration

_CAPACITY = 3
_AGENT_COUNT = 10


def _setup() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, list[uuid.UUID]]:
    with SessionLocal() as db:
        org = Organization(name=f"Staffing race org {uuid.uuid4().hex[:8]}", status="active")
        db.add(org)
        db.flush()

        team = Team(
            organization_id=org.id,
            external_code=f"race-team-{uuid.uuid4().hex[:8]}",
            name="Staffing race team",
        )
        db.add(team)
        db.flush()

        campaign = Campaign(
            owning_scope_type="organization",
            external_code=f"c-{uuid.uuid4().hex[:8]}",
            name=f"Staffing race campaign {uuid.uuid4().hex[:6]}",
            default_region="ZW",
            timezone="Africa/Harare",
            status="active",
        )
        db.add(campaign)
        db.flush()

        db.add(
            CampaignTeamAssignment(
                campaign_id=campaign.id,
                team_id=team.id,
                effective_from=datetime.now(UTC),
                staffing_capacity=_CAPACITY,
            )
        )

        actor = User(
            workforce_id=f"race-actor-{uuid.uuid4().hex[:8]}",
            email=f"race-actor-{uuid.uuid4().hex[:8]}@example.com",
            display_name="Staffing Race Actor",
            password_hash=hash_password("not-used-in-this-test"),
        )
        db.add(actor)
        db.flush()

        agent_ids = []
        for i in range(_AGENT_COUNT):
            unique = uuid.uuid4().hex[:8]
            agent = User(
                workforce_id=f"race-agent-{i}-{unique}",
                email=f"race-agent-{i}-{unique}@example.com",
                display_name="Staffing Race Agent",
                password_hash=hash_password("not-used-in-this-test"),
            )
            db.add(agent)
            db.flush()
            agent_ids.append(agent.id)

        db.commit()
        return campaign.id, team.id, actor.id, agent_ids


def _assign_in_own_session(
    campaign_id: uuid.UUID, team_id: uuid.UUID, actor_id: uuid.UUID, agent_id: uuid.UUID
) -> bool:
    """True if the assignment succeeded, False if it was correctly rejected for
    capacity. Any other exception propagates - only a capacity rejection is an
    expected outcome here."""
    with SessionLocal() as db:
        campaign = db.get(Campaign, campaign_id)
        assert campaign is not None
        try:
            campaign_service.assign_agent_to_campaign(
                db, campaign, agent_id=agent_id, team_id=team_id, actor_id=actor_id,
            )
            db.commit()
            return True
        except CampaignAssignmentError as exc:
            db.rollback()
            assert "capacity" in str(exc).lower()
            return False


def test_concurrent_assignment_does_not_exceed_staffing_capacity():
    campaign_id, team_id, actor_id, agent_ids = _setup()

    with ThreadPoolExecutor(max_workers=_AGENT_COUNT) as pool:
        results = list(
            pool.map(
                lambda agent_id: _assign_in_own_session(campaign_id, team_id, actor_id, agent_id),
                agent_ids,
            )
        )

    accepted = sum(1 for r in results if r)
    assert accepted == _CAPACITY, (
        f"expected exactly {_CAPACITY} assignments to succeed under capacity "
        f"{_CAPACITY}, got {accepted} - a higher number means the staffing-capacity "
        f"check does not hold under real concurrency"
    )

    with SessionLocal() as db:
        active_count = len(
            db.scalars(
                select(CampaignUserAssignment).where(
                    CampaignUserAssignment.campaign_id == campaign_id,
                    CampaignUserAssignment.team_id == team_id,
                    CampaignUserAssignment.status == "active",
                    CampaignUserAssignment.effective_to.is_(None),
                )
            ).all()
        )
        assert active_count == _CAPACITY, (
            "the persisted row count must match the reported success count exactly"
        )
