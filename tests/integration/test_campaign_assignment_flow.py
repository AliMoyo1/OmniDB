"""Integration tests for campaign-team/user assignment and agent transfer (D-18)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models.identity import Team
from app.models.work import WorkItem
from tests.integration.conftest import csrf_headers, make_user_with_role, zw_numbers

pytestmark = pytest.mark.integration

_DEFAULT_PROVENANCE = {
    "purpose": "Customer outreach",
    "data_source": "CRM export",
    "data_obtained_at": "2026-01-01",
    "lawful_basis_or_consent_reference": "consent-ref-123",
}


def _create_campaign(client: TestClient, headers: dict, **overrides) -> str:
    payload = {
        "name": f"Assignment test {uuid.uuid4().hex[:6]}",
        "owning_scope_type": "organization",
        "default_region": "ZW",
        "timezone": "Africa/Harare",
        **_DEFAULT_PROVENANCE,
        **overrides,
    }
    resp = client.post("/api/v1/campaigns", json=payload, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _commit_and_launch(client: TestClient, headers: dict, campaign_id: str) -> None:
    number = zw_numbers(1)[0]
    files = {"file": ("c.csv", f"phone,name\n{number},Alice\n".encode(), "text/csv")}
    data = {"phone_column": "phone", "name_column": "name", "metadata_columns": ""}
    upload = client.post(
        f"/api/v1/campaigns/{campaign_id}/imports", files=files, data=data, headers=headers
    ).json()
    decision = client.patch(
        f"/api/v1/imports/{upload['id']}/decisions", json={"decision": "approve"}, headers=headers
    ).json()
    commit = client.post(
        f"/api/v1/imports/{upload['id']}/commit",
        json={
            "decision_version": decision["decision_version"],
            "idempotency_key": str(uuid.uuid4()),
        },
        headers=headers,
    )
    assert commit.status_code == 200, commit.text
    launch = client.post(f"/api/v1/campaigns/{campaign_id}/launch", headers=headers)
    assert launch.status_code == 200, launch.text


def _create_disposition(client: TestClient, headers: dict, campaign_id: str, **overrides) -> str:
    payload = {
        "label": "Test disposition",
        "stable_semantic_code": f"code_{uuid.uuid4().hex[:8]}",
        "next_action": "complete",
        "requires_notes": False,
        "requires_callback_time": False,
        "counts_as_connected": False,
        "counts_as_conversion": False,
        "causes_dnc": False,
        **overrides,
    }
    resp = client.post(
        f"/api/v1/campaigns/{campaign_id}/dispositions", json=payload, headers=headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _create_team() -> uuid.UUID:
    with SessionLocal() as db:
        organization_id = db.execute(select(Team.organization_id).limit(1)).scalar()
        if organization_id is None:
            from app.models.identity import Organization

            org = Organization(name=f"Org {uuid.uuid4().hex[:8]}", status="active")
            db.add(org)
            db.flush()
            organization_id = org.id
        team = Team(
            organization_id=organization_id,
            external_code=f"team-{uuid.uuid4().hex[:8]}",
            name=f"Team {uuid.uuid4().hex[:6]}",
        )
        db.add(team)
        db.commit()
        return team.id


def _team_scoped_captain_client(team_id: uuid.UUID) -> TestClient:
    from app.main import app
    from tests.integration.conftest import login

    email = f"captain-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(email, "team_captain", scope_type="team", scope_id=team_id)
    client = TestClient(app)
    login(client, email)
    return client


def test_assign_and_end_team_to_campaign(manager_client):
    headers = csrf_headers(manager_client)
    campaign_id = _create_campaign(manager_client, headers)
    team_id = _create_team()

    assigned = manager_client.post(
        f"/api/v1/campaigns/{campaign_id}/team-assignments",
        json={"team_id": str(team_id), "staffing_capacity": 5},
        headers=headers,
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["status"] == "active"

    listed = manager_client.get(f"/api/v1/campaigns/{campaign_id}/team-assignments").json()
    assert len(listed) == 1

    ended = manager_client.post(
        f"/api/v1/campaigns/{campaign_id}/team-assignments/{assigned.json()['id']}/end",
        headers=headers,
    )
    assert ended.status_code == 200, ended.text
    assert ended.json()["status"] == "ended"


def test_assign_agent_via_api_and_lease(manager_client, agent_client):
    headers = csrf_headers(manager_client)
    campaign_id = _create_campaign(manager_client, headers)
    _commit_and_launch(manager_client, headers, campaign_id)
    agent_id = agent_client.get("/api/v1/auth/me").json()["id"]

    assigned = manager_client.post(
        f"/api/v1/campaigns/{campaign_id}/assignments",
        json={"agent_id": agent_id},
        headers=headers,
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["assignment_type"] == "primary"

    lease = agent_client.post("/api/v1/work/next", headers=csrf_headers(agent_client))
    assert lease.status_code == 200, lease.text


def test_cannot_double_assign_primary_without_transfer(manager_client, agent_client):
    headers = csrf_headers(manager_client)
    campaign_a = _create_campaign(manager_client, headers)
    campaign_b = _create_campaign(manager_client, headers)
    agent_id = agent_client.get("/api/v1/auth/me").json()["id"]

    first = manager_client.post(
        f"/api/v1/campaigns/{campaign_a}/assignments", json={"agent_id": agent_id}, headers=headers
    )
    assert first.status_code == 200, first.text

    second = manager_client.post(
        f"/api/v1/campaigns/{campaign_b}/assignments", json={"agent_id": agent_id}, headers=headers
    )
    assert second.status_code == 409
    assert "transfer" in second.text.lower()


def test_staffing_capacity_is_enforced(manager_client, agent_client):
    headers = csrf_headers(manager_client)
    campaign_id = _create_campaign(manager_client, headers)
    team_id = _create_team()
    manager_client.post(
        f"/api/v1/campaigns/{campaign_id}/team-assignments",
        json={"team_id": str(team_id), "staffing_capacity": 1},
        headers=headers,
    )
    agent_a = agent_client.get("/api/v1/auth/me").json()["id"]
    agent_b = make_user_with_role(f"cap-{uuid.uuid4().hex[:8]}@example.com", "agent")

    first = manager_client.post(
        f"/api/v1/campaigns/{campaign_id}/assignments",
        json={"agent_id": agent_a, "team_id": str(team_id)},
        headers=headers,
    )
    assert first.status_code == 200, first.text

    second = manager_client.post(
        f"/api/v1/campaigns/{campaign_id}/assignments",
        json={"agent_id": str(agent_b), "team_id": str(team_id)},
        headers=headers,
    )
    assert second.status_code == 409
    assert "capacity" in second.text.lower()


def test_transfer_moves_assignment_and_releases_leased_work(manager_client, agent_client):
    headers = csrf_headers(manager_client)
    campaign_a = _create_campaign(manager_client, headers)
    campaign_b = _create_campaign(manager_client, headers)
    _commit_and_launch(manager_client, headers, campaign_a)
    _commit_and_launch(manager_client, headers, campaign_b)
    agent_id = agent_client.get("/api/v1/auth/me").json()["id"]

    manager_client.post(
        f"/api/v1/campaigns/{campaign_a}/assignments", json={"agent_id": agent_id}, headers=headers
    )
    lease = agent_client.post("/api/v1/work/next", headers=csrf_headers(agent_client)).json()
    with SessionLocal() as db:
        item = db.get(WorkItem, uuid.UUID(lease["work_item_id"]))
        assert item.state == "leased"

    transfer = manager_client.post(
        f"/api/v1/campaigns/{campaign_a}/transfer",
        json={"agent_id": agent_id, "to_campaign_id": campaign_b},
        headers=headers,
    )
    assert transfer.status_code == 200, transfer.text
    assert transfer.json()["campaign_id"] == campaign_b

    with SessionLocal() as db:
        item = db.get(WorkItem, uuid.UUID(lease["work_item_id"]))
        assert item.state == "queued"
        assert item.lease_owner_id is None

    assignments_a = manager_client.get(f"/api/v1/campaigns/{campaign_a}/assignments").json()
    active_a = [a for a in assignments_a if a["status"] == "active"]
    assert active_a == []

    second_lease = agent_client.post("/api/v1/work/next", headers=csrf_headers(agent_client))
    assert second_lease.status_code == 200, second_lease.text
    assert second_lease.json()["campaign_id"] == campaign_b


def test_transfer_releases_pending_callback_instead_of_orphaning_it(manager_client, agent_client):
    headers = csrf_headers(manager_client)
    campaign_a = _create_campaign(manager_client, headers)
    campaign_b = _create_campaign(manager_client, headers)
    _commit_and_launch(manager_client, headers, campaign_a)
    _commit_and_launch(manager_client, headers, campaign_b)
    agent_id = agent_client.get("/api/v1/auth/me").json()["id"]
    disposition_id = _create_disposition(
        manager_client, headers, campaign_a,
        stable_semantic_code="callback_requested", requires_callback_time=True,
    )

    manager_client.post(
        f"/api/v1/campaigns/{campaign_a}/assignments", json={"agent_id": agent_id}, headers=headers
    )
    lease = agent_client.post("/api/v1/work/next", headers=csrf_headers(agent_client)).json()
    callback_at = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    complete = agent_client.post(
        f"/api/v1/work/{lease['work_item_id']}/complete",
        json={
            "lease_id": lease["lease_id"], "disposition_id": disposition_id,
            "callback_at": callback_at, "idempotency_key": str(uuid.uuid4()),
        },
        headers=csrf_headers(agent_client),
    )
    assert complete.status_code == 200, complete.text
    with SessionLocal() as db:
        item = db.get(WorkItem, uuid.UUID(lease["work_item_id"]))
        assert item.state == "callback_wait"
        assert item.assigned_agent_id == uuid.UUID(agent_id)

    transfer = manager_client.post(
        f"/api/v1/campaigns/{campaign_a}/transfer",
        json={"agent_id": agent_id, "to_campaign_id": campaign_b},
        headers=headers,
    )
    assert transfer.status_code == 200, transfer.text

    with SessionLocal() as db:
        item = db.get(WorkItem, uuid.UUID(lease["work_item_id"]))
        assert item.state == "queued"
        assert item.assigned_agent_id is None
        assert item.due_at is None


def test_transfer_requires_capability_on_both_campaigns(manager_client, agent_client):
    headers = csrf_headers(manager_client)
    team_a = _create_team()
    team_b = _create_team()
    campaign_a = _create_campaign(
        manager_client, headers, owning_scope_type="team", owning_scope_id=str(team_a)
    )
    campaign_b = _create_campaign(
        manager_client, headers, owning_scope_type="team", owning_scope_id=str(team_b)
    )
    _commit_and_launch(manager_client, headers, campaign_a)
    _commit_and_launch(manager_client, headers, campaign_b)
    agent_id = agent_client.get("/api/v1/auth/me").json()["id"]
    manager_client.post(
        f"/api/v1/campaigns/{campaign_a}/assignments",
        json={"agent_id": agent_id, "team_id": str(team_a)},
        headers=headers,
    )

    captain_a = _team_scoped_captain_client(team_a)
    transfer = captain_a.post(
        f"/api/v1/campaigns/{campaign_a}/transfer",
        json={"agent_id": agent_id, "to_campaign_id": campaign_b},
        headers=csrf_headers(captain_a),
    )
    assert transfer.status_code == 403


def test_transfer_fails_if_destination_not_active(manager_client, agent_client):
    headers = csrf_headers(manager_client)
    campaign_a = _create_campaign(manager_client, headers)
    campaign_b = _create_campaign(manager_client, headers)  # left in draft
    _commit_and_launch(manager_client, headers, campaign_a)
    agent_id = agent_client.get("/api/v1/auth/me").json()["id"]
    manager_client.post(
        f"/api/v1/campaigns/{campaign_a}/assignments", json={"agent_id": agent_id}, headers=headers
    )

    transfer = manager_client.post(
        f"/api/v1/campaigns/{campaign_a}/transfer",
        json={"agent_id": agent_id, "to_campaign_id": campaign_b},
        headers=headers,
    )
    assert transfer.status_code == 409
    assert "not active" in transfer.text.lower()


def test_agent_cannot_assign_agents(manager_client, agent_client):
    headers = csrf_headers(manager_client)
    campaign_id = _create_campaign(manager_client, headers)
    other_agent = make_user_with_role(f"other-{uuid.uuid4().hex[:8]}@example.com", "agent")

    resp = agent_client.post(
        f"/api/v1/campaigns/{campaign_id}/assignments",
        json={"agent_id": str(other_agent)},
        headers=csrf_headers(agent_client),
    )
    assert resp.status_code == 403


def test_team_captain_can_assign_agent_within_scope(manager_client, agent_client):
    headers = csrf_headers(manager_client)
    team_id = _create_team()
    campaign_id = _create_campaign(
        manager_client, headers, owning_scope_type="team", owning_scope_id=str(team_id)
    )
    manager_client.post(
        f"/api/v1/campaigns/{campaign_id}/team-assignments",
        json={"team_id": str(team_id)},
        headers=headers,
    )
    agent_id = agent_client.get("/api/v1/auth/me").json()["id"]
    captain = _team_scoped_captain_client(team_id)

    resp = captain.post(
        f"/api/v1/campaigns/{campaign_id}/assignments",
        json={"agent_id": agent_id, "team_id": str(team_id)},
        headers=csrf_headers(captain),
    )
    assert resp.status_code == 200, resp.text
