"""Integration tests for the workforce API: users, roles, teams, reporting lines."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models.authz import ReportingAssignment
from app.models.identity import Organization, Team, TeamMembership
from app.models.work import WorkItem
from tests.integration.conftest import (
    assign_agent_to_campaign,
    csrf_headers,
    login,
    make_user_with_role,
    zw_numbers,
)

pytestmark = pytest.mark.integration


def _me(client: TestClient) -> dict:
    return client.get("/api/v1/auth/me").json()


def _create_team(name: str | None = None) -> uuid.UUID:
    with SessionLocal() as db:
        organization_id = db.execute(select(Team.organization_id).limit(1)).scalar()
        if organization_id is None:
            org = Organization(name=f"Org {uuid.uuid4().hex[:8]}", status="active")
            db.add(org)
            db.flush()
            organization_id = org.id
        team = Team(
            organization_id=organization_id,
            external_code=f"team-{uuid.uuid4().hex[:8]}",
            name=name or f"Team {uuid.uuid4().hex[:6]}",
        )
        db.add(team)
        db.commit()
        return team.id


def _team_scoped_leader_client(team_id: uuid.UUID) -> TestClient:
    from app.main import app

    email = f"leader-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(
        email, "team_leader", scope_type="team", scope_id=team_id
    )
    client = TestClient(app)
    login(client, email)
    return client


def test_manager_creates_user_and_assigns_team_leader_role(manager_client):
    headers = csrf_headers(manager_client)
    email = f"newlead-{uuid.uuid4().hex[:8]}@example.com"
    created = manager_client.post(
        "/api/v1/workforce/users",
        json={"email": email, "display_name": "New Leader"},
        headers=headers,
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["email"] == email
    assert body["workforce_id"] == email.split("@")[0]
    assert body["activation_token"]

    assigned = manager_client.post(
        f"/api/v1/workforce/users/{body['id']}/roles",
        json={"role_code": "team_leader", "scope_type": "organization", "scope_id": None},
        headers=headers,
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["role_code"] == "team_leader"
    assert assigned.json()["status"] == "active"


def test_duplicate_email_is_rejected(manager_client):
    headers = csrf_headers(manager_client)
    email = f"dup-{uuid.uuid4().hex[:8]}@example.com"
    first = manager_client.post(
        "/api/v1/workforce/users", json={"email": email, "display_name": "First"}, headers=headers
    )
    assert first.status_code == 200
    second = manager_client.post(
        "/api/v1/workforce/users", json={"email": email, "display_name": "Second"}, headers=headers
    )
    assert second.status_code == 409


def test_unknown_role_code_is_rejected_at_the_schema(manager_client):
    headers = csrf_headers(manager_client)
    target = make_user_with_role(f"t-{uuid.uuid4().hex[:8]}@example.com", "agent")
    resp = manager_client.post(
        f"/api/v1/workforce/users/{target}/roles",
        json={"role_code": "super_admin", "scope_type": "installation", "scope_id": None},
        headers=headers,
    )
    assert resp.status_code == 422


def test_self_appointment_is_blocked(manager_client):
    headers = csrf_headers(manager_client)
    me = _me(manager_client)
    resp = manager_client.post(
        f"/api/v1/workforce/users/{me['id']}/roles",
        json={"role_code": "team_leader", "scope_type": "organization", "scope_id": None},
        headers=headers,
    )
    assert resp.status_code == 403


def test_reassigning_same_role_scope_supersedes_not_stacks(manager_client):
    headers = csrf_headers(manager_client)
    target = make_user_with_role(f"re-{uuid.uuid4().hex[:8]}@example.com", "agent")
    payload = {"role_code": "team_captain", "scope_type": "organization", "scope_id": None}
    first = manager_client.post(
        f"/api/v1/workforce/users/{target}/roles", json=payload, headers=headers
    )
    assert first.status_code == 200
    second = manager_client.post(
        f"/api/v1/workforce/users/{target}/roles", json=payload, headers=headers
    )
    assert second.status_code == 200
    assert second.json()["id"] != first.json()["id"]

    roles = manager_client.get(f"/api/v1/workforce/users/{target}/roles").json()
    active_team_captain = [
        r for r in roles if r["role_code"] == "team_captain" and r["status"] == "active"
    ]
    assert len(active_team_captain) == 1
    assert active_team_captain[0]["id"] == second.json()["id"]


def test_ending_a_role_assignment_invalidates_the_target_session(manager_client, agent_client):
    headers = csrf_headers(manager_client)
    agent_id = _me(agent_client)["id"]
    assert _me(agent_client).get("id") == agent_id  # session alive before

    roles = manager_client.get(f"/api/v1/workforce/users/{agent_id}/roles").json()
    active_agent_role = next(
        r for r in roles if r["role_code"] == "agent" and r["status"] == "active"
    )

    ended = manager_client.post(
        f"/api/v1/workforce/roles/{active_agent_role['id']}/end",
        json={"reason_code": "test_cleanup"},
        headers=headers,
    )
    assert ended.status_code == 200, ended.text
    assert ended.json()["status"] == "ended"

    after = agent_client.get("/api/v1/auth/me")
    assert after.status_code == 401


def test_team_leader_appoints_team_captain_in_scope_but_not_outside_it(manager_client):
    team_id = _create_team()
    other_team_id = _create_team()
    leader = _team_scoped_leader_client(team_id)
    leader_headers = csrf_headers(leader)
    target = make_user_with_role(f"cap-{uuid.uuid4().hex[:8]}@example.com", "agent")

    in_scope = leader.post(
        f"/api/v1/workforce/users/{target}/roles",
        json={"role_code": "team_captain", "scope_type": "team", "scope_id": str(team_id)},
        headers=leader_headers,
    )
    assert in_scope.status_code == 200, in_scope.text

    out_of_scope = leader.post(
        f"/api/v1/workforce/users/{target}/roles",
        json={
            "role_code": "team_captain", "scope_type": "team", "scope_id": str(other_team_id),
        },
        headers=leader_headers,
    )
    assert out_of_scope.status_code == 403


def test_team_leader_cannot_appoint_another_team_leader(manager_client):
    team_id = _create_team()
    leader = _team_scoped_leader_client(team_id)
    leader_headers = csrf_headers(leader)
    target = make_user_with_role(f"tl-{uuid.uuid4().hex[:8]}@example.com", "agent")

    resp = leader.post(
        f"/api/v1/workforce/users/{target}/roles",
        json={"role_code": "team_leader", "scope_type": "team", "scope_id": str(team_id)},
        headers=leader_headers,
    )
    assert resp.status_code == 403


def test_agent_cannot_perform_any_workforce_write(agent_client):
    headers = csrf_headers(agent_client)
    email = f"blocked-{uuid.uuid4().hex[:8]}@example.com"

    assert agent_client.post(
        "/api/v1/workforce/users", json={"email": email, "display_name": "X"}, headers=headers
    ).status_code == 403
    assert agent_client.post(
        "/api/v1/workforce/teams",
        json={"name": "X", "external_code": f"x-{uuid.uuid4().hex[:6]}"},
        headers=headers,
    ).status_code == 403
    assert agent_client.get("/api/v1/workforce/users").status_code == 403


def test_disable_user_reclaims_active_lease(manager_client, agent_client):
    headers = csrf_headers(manager_client)
    agent_id = _me(agent_client)["id"]

    campaign = manager_client.post(
        "/api/v1/campaigns",
        json={
            "external_code": f"c-{uuid.uuid4().hex[:8]}",
            "name": f"Disable test {uuid.uuid4().hex[:6]}", "owning_scope_type": "organization",
            "default_region": "ZW", "timezone": "Africa/Harare",
            "purpose": "Customer outreach", "data_source": "CRM export",
            "data_obtained_at": "2026-01-01",
            "lawful_basis_or_consent_reference": "consent-ref-123",
        },
        headers=headers,
    ).json()
    number = zw_numbers(1)[0]
    files = {"file": ("c.csv", f"phone,name\n{number},Alice\n".encode(), "text/csv")}
    data = {"phone_column": "phone", "name_column": "name", "metadata_columns": ""}
    upload = manager_client.post(
        f"/api/v1/campaigns/{campaign['id']}/imports", files=files, data=data, headers=headers
    ).json()
    decision = manager_client.patch(
        f"/api/v1/imports/{upload['id']}/decisions", json={"decision": "approve"}, headers=headers
    ).json()
    manager_client.post(
        f"/api/v1/imports/{upload['id']}/commit",
        json={
            "decision_version": decision["decision_version"],
            "idempotency_key": str(uuid.uuid4()),
        },
        headers=headers,
    )
    manager_client.post(f"/api/v1/campaigns/{campaign['id']}/launch", headers=headers)
    assign_agent_to_campaign(uuid.UUID(agent_id), uuid.UUID(campaign["id"]))

    lease = agent_client.post("/api/v1/work/next", headers=csrf_headers(agent_client)).json()
    assert lease["work_item_id"]

    with SessionLocal() as db:
        item = db.get(WorkItem, uuid.UUID(lease["work_item_id"]))
        assert item.state == "leased"

    disabled = manager_client.post(
        f"/api/v1/workforce/users/{agent_id}/disable",
        json={"reason_code": "test_offboarding"},
        headers=headers,
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["active"] is False

    with SessionLocal() as db:
        item = db.get(WorkItem, uuid.UUID(lease["work_item_id"]))
        assert item.state == "queued"
        assert item.lease_owner_id is None

    assert agent_client.get("/api/v1/auth/me").status_code == 401


def test_set_reporting_line_blocks_self_supervision(manager_client):
    # A manager acting on a separate agent, not on themselves: can_manage_user only
    # ever authorizes appointment capability held over a role strictly below the
    # target's, so a self-targeting request 403s before ever reaching the domain
    # check this test means to isolate. This scenario - an admin naming a user as
    # their own supervisor - is also the realistic way this guard gets exercised.
    headers = csrf_headers(manager_client)
    target = make_user_with_role(f"self-{uuid.uuid4().hex[:8]}@example.com", "agent")
    resp = manager_client.post(
        f"/api/v1/workforce/users/{target}/reporting-line",
        json={"supervisor_user_id": str(target)},
        headers=headers,
    )
    assert resp.status_code == 400


def test_set_reporting_line_supersedes_prior_primary(manager_client):
    headers = csrf_headers(manager_client)
    subordinate = make_user_with_role(f"sub-{uuid.uuid4().hex[:8]}@example.com", "agent")
    supervisor_a = make_user_with_role(f"sup-a-{uuid.uuid4().hex[:8]}@example.com", "team_captain")
    supervisor_b = make_user_with_role(f"sup-b-{uuid.uuid4().hex[:8]}@example.com", "team_captain")

    first = manager_client.post(
        f"/api/v1/workforce/users/{subordinate}/reporting-line",
        json={"supervisor_user_id": str(supervisor_a)},
        headers=headers,
    )
    assert first.status_code == 200, first.text
    second = manager_client.post(
        f"/api/v1/workforce/users/{subordinate}/reporting-line",
        json={"supervisor_user_id": str(supervisor_b)},
        headers=headers,
    )
    assert second.status_code == 200, second.text

    with SessionLocal() as db:
        active = db.scalars(
            select(ReportingAssignment).where(
                ReportingAssignment.subordinate_user_id == subordinate,
                ReportingAssignment.status == "active",
            )
        ).all()
        assert len(active) == 1
        assert active[0].supervisor_user_id == supervisor_b


def test_create_team_and_manage_membership(manager_client):
    headers = csrf_headers(manager_client)
    team = manager_client.post(
        "/api/v1/workforce/teams",
        json={
            "name": f"Ops {uuid.uuid4().hex[:6]}",
            "external_code": f"ops-{uuid.uuid4().hex[:6]}",
        },
        headers=headers,
    )
    assert team.status_code == 200, team.text
    team_id = team.json()["id"]

    member = make_user_with_role(f"member-{uuid.uuid4().hex[:8]}@example.com", "agent")
    added = manager_client.post(
        f"/api/v1/workforce/teams/{team_id}/members", json={"user_id": str(member)}, headers=headers
    )
    assert added.status_code == 200, added.text

    with SessionLocal() as db:
        membership = db.scalar(
            select(TeamMembership).where(
                TeamMembership.team_id == uuid.UUID(team_id),
                TeamMembership.user_id == member,
            )
        )
        assert membership is not None
        assert membership.membership_status == "active"

    ended = manager_client.post(
        f"/api/v1/workforce/memberships/{membership.id}/end", headers=headers
    )
    assert ended.status_code == 200, ended.text
    assert ended.json()["membership_status"] == "ended"


def test_list_users_is_scoped_to_team_for_team_leader(manager_client):
    team_a = _create_team()
    team_b = _create_team()
    leader_a = _team_scoped_leader_client(team_a)

    member_a = make_user_with_role(f"a-{uuid.uuid4().hex[:8]}@example.com", "agent")
    member_b = make_user_with_role(f"b-{uuid.uuid4().hex[:8]}@example.com", "agent")
    now = datetime.now(UTC)
    with SessionLocal() as db:
        db.add_all(
            [
                TeamMembership(team_id=team_a, user_id=member_a, effective_from=now),
                TeamMembership(team_id=team_b, user_id=member_b, effective_from=now),
            ]
        )
        db.commit()

    visible = leader_a.get("/api/v1/workforce/users", params={"limit": 200}).json()
    visible_ids = {u["id"] for u in visible}
    assert str(member_a) in visible_ids
    assert str(member_b) not in visible_ids
