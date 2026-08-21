from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.authz.capabilities import ROLE_TEAM_LEADER
from app.db import SessionLocal
from app.main import app
from app.models.identity import Organization, Team
from tests.integration.conftest import csrf_headers, login, make_user_with_role

pytestmark = pytest.mark.integration


def _campaign_payload(name: str, team_id: uuid.UUID) -> dict[str, str]:
    return {
        "name": name,
        "owning_scope_type": "team",
        "owning_scope_id": str(team_id),
        "default_region": "ZW",
        "timezone": "Africa/Harare",
    }


def _create_teams() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    suffix = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        organization = Organization(name=f"Scope Test {suffix}", status="active")
        db.add(organization)
        db.flush()
        team_a = Team(
            organization_id=organization.id,
            external_code=f"A-{suffix}",
            name="Team A",
            status="active",
        )
        team_b = Team(
            organization_id=organization.id,
            external_code=f"B-{suffix}",
            name="Team B",
            status="active",
        )
        db.add_all([team_a, team_b])
        db.commit()
        return organization.id, team_a.id, team_b.id


def test_team_scoped_leader_cannot_cross_campaign_boundaries(manager_client: TestClient):
    organization_id, team_a_id, team_b_id = _create_teams()
    manager_headers = csrf_headers(manager_client)

    campaign_a = manager_client.post(
        "/api/v1/campaigns",
        json=_campaign_payload("Team A campaign", team_a_id),
        headers=manager_headers,
    )
    campaign_b = manager_client.post(
        "/api/v1/campaigns",
        json=_campaign_payload("Team B campaign", team_b_id),
        headers=manager_headers,
    )
    assert campaign_a.status_code == 200, campaign_a.text
    assert campaign_b.status_code == 200, campaign_b.text

    leader_email = f"leader-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(
        leader_email,
        ROLE_TEAM_LEADER,
        scope_type="team",
        scope_id=team_a_id,
    )
    with TestClient(app) as leader_client:
        login(leader_client, leader_email)
        leader_headers = csrf_headers(leader_client)

        own_create = leader_client.post(
            "/api/v1/campaigns",
            json=_campaign_payload("Leader-owned campaign", team_a_id),
            headers=leader_headers,
        )
        assert own_create.status_code == 200, own_create.text

        cross_team_create = leader_client.post(
            "/api/v1/campaigns",
            json=_campaign_payload("Forbidden campaign", team_b_id),
            headers=leader_headers,
        )
        assert cross_team_create.status_code == 403

        organization_create = leader_client.post(
            "/api/v1/campaigns",
            json={
                "name": "Forbidden organization campaign",
                "owning_scope_type": "organization",
                "owning_scope_id": str(organization_id),
            },
            headers=leader_headers,
        )
        assert organization_create.status_code == 403

        listing = leader_client.get("/api/v1/campaigns")
        assert listing.status_code == 200, listing.text
        listed_ids = {row["id"] for row in listing.json()}
        assert campaign_a.json()["id"] in listed_ids
        assert own_create.json()["id"] in listed_ids
        assert campaign_b.json()["id"] not in listed_ids

        direct_cross_team_access = leader_client.get(
            f"/api/v1/campaigns/{campaign_b.json()['id']}"
        )
        assert direct_cross_team_access.status_code == 403
