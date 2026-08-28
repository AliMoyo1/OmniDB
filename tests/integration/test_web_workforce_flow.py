"""Integration tests for the Workforce Control Room (/workforce): the web-layer
lifecycle actions - disable/reactivate a user, end a role assignment, end a team
membership, set a reporting line - that the original dashboard-embedded workforce
section never exposed, only ever wiring up the create half of the already-tested
service layer (app/workforce/service.py, already covered at the JSON-API level by
tests/integration/test_workforce_flow.py).
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models.authz import ReportingAssignment, RoleAssignment
from app.models.identity import TeamMembership, User
from tests.integration.conftest import login, make_user_with_role

pytestmark = pytest.mark.integration


def _csrf(client: TestClient) -> str:
    token = client.cookies.get("cc_csrf")
    assert token, "csrf cookie not set; did you log in first?"
    return token


def _manager(email_prefix: str = "wfmgr") -> tuple[TestClient, str]:
    from app.main import app

    email = f"{email_prefix}-{uuid.uuid4().hex[:8]}@example.com"
    user_id = make_user_with_role(email, "manager")
    client = TestClient(app, follow_redirects=False)
    login(client, email)
    return client, str(user_id)


def _create_user(client: TestClient, email_prefix: str = "wfuser") -> str:
    # Query back by the exact (unique, uuid4-suffixed) email rather than "most
    # recently created" - the database is shared and not rolled back between
    # tests, so a recency query is racy against any other test creating a user
    # around the same time (the same class of isolation bug this session's own
    # zw_numbers fix addressed for phone numbers).
    email = f"{email_prefix}-{uuid.uuid4().hex[:8]}@example.com"
    resp = client.post(
        "/workforce/users",
        data={
            "csrf_token": _csrf(client),
            "email": email,
            "display_name": "Workforce Test User",
        },
    )
    assert resp.status_code == 200, resp.text
    assert "activation" in resp.text.lower()
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        assert user is not None
        return str(user.id)


def test_unauthenticated_workforce_redirects_to_login():
    from app.main import app

    client = TestClient(app, follow_redirects=False)
    resp = client.get("/workforce")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_agent_without_appointment_capability_is_redirected_away():
    from app.main import app

    email = f"wfagent-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(email, "agent")
    client = TestClient(app, follow_redirects=False)
    login(client, email)
    resp = client.get("/workforce")
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/dashboard")


def test_agent_cannot_view_a_team_detail_page_by_url():
    """Regression test for a real gap found in review: team_detail only gated its
    management actions, not viewing itself - any authenticated user, including a
    plain Agent with no appointment capability, could browse straight to another
    team's roster by URL. Viewing must be gated the same way workforce_list already
    gates whether a team's tile (and link) appears at all."""
    from app.main import app

    manager, _ = _manager()
    resp = manager.post(
        "/workforce/teams",
        data={
            "csrf_token": _csrf(manager),
            "name": f"Private Team {uuid.uuid4().hex[:6]}",
            "external_code": f"priv-{uuid.uuid4().hex[:8]}",
        },
    )
    assert resp.status_code == 303
    team_id = resp.headers["location"].split("/workforce/teams/")[1].split("?")[0]

    email = f"wfagentview-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(email, "agent")
    agent_client = TestClient(app, follow_redirects=False)
    login(agent_client, email)
    resp = agent_client.get(f"/workforce/teams/{team_id}")
    assert resp.status_code == 303
    location = resp.headers["location"]
    assert location.startswith("/workforce")
    assert not location.startswith("/workforce/teams/")


def test_manager_sees_workforce_list_with_create_forms():
    client, _ = _manager()
    resp = client.get("/workforce")
    assert resp.status_code == 200
    assert "Create a user" in resp.text
    assert "Create a team" in resp.text


def test_disable_and_reactivate_user():
    client, _ = _manager()
    user_id = _create_user(client)

    resp = client.post(
        f"/workforce/users/{user_id}/disable",
        data={"csrf_token": _csrf(client), "reason_code": "test_disable"},
    )
    assert resp.status_code == 303
    with SessionLocal() as db:
        user = db.get(User, uuid.UUID(user_id))
        assert user is not None
        assert user.active is False
        assert user.disabled_at is not None

    detail = client.get(f"/workforce/users/{user_id}", follow_redirects=True)
    assert "Reactivate" in detail.text

    resp = client.post(
        f"/workforce/users/{user_id}/reactivate",
        data={"csrf_token": _csrf(client), "reason_code": "test_reactivate"},
    )
    assert resp.status_code == 303
    with SessionLocal() as db:
        user = db.get(User, uuid.UUID(user_id))
        assert user is not None
        assert user.active is True
        assert user.disabled_at is None


def test_assign_role_then_end_it():
    client, _ = _manager()
    user_id = _create_user(client)

    resp = client.post(
        f"/workforce/users/{user_id}/roles",
        data={
            "csrf_token": _csrf(client),
            "role_code": "agent",
            "scope_type": "organization",
            "scope_id": "",
        },
    )
    assert resp.status_code == 303
    assert "error" not in (resp.headers.get("location") or "")

    with SessionLocal() as db:
        assignment = db.scalar(
            select(RoleAssignment).where(
                RoleAssignment.user_id == uuid.UUID(user_id),
                RoleAssignment.status == "active",
            )
        )
        assert assignment is not None
        assert assignment.role_code == "agent"
        assignment_id = str(assignment.id)

    detail = client.get(f"/workforce/users/{user_id}", follow_redirects=True)
    assert "agent" in detail.text

    resp = client.post(
        f"/workforce/roles/{assignment_id}/end",
        data={"csrf_token": _csrf(client), "reason_code": "test_end_role"},
    )
    assert resp.status_code == 303
    with SessionLocal() as db:
        ended = db.get(RoleAssignment, uuid.UUID(assignment_id))
        assert ended is not None
        assert ended.status == "ended"
        assert ended.effective_to is not None


def test_create_team_add_member_then_remove():
    client, _ = _manager()
    user_id = _create_user(client)

    resp = client.post(
        "/workforce/teams",
        data={
            "csrf_token": _csrf(client),
            "name": f"Workforce Test Team {uuid.uuid4().hex[:6]}",
            "external_code": f"wf-{uuid.uuid4().hex[:8]}",
        },
    )
    assert resp.status_code == 303
    location = resp.headers["location"]
    assert location.startswith("/workforce/teams/")
    team_id = location.split("/workforce/teams/")[1].split("?")[0]

    resp = client.post(
        f"/workforce/teams/{team_id}/members",
        data={"csrf_token": _csrf(client), "member_user_id": user_id},
    )
    assert resp.status_code == 303

    with SessionLocal() as db:
        membership = db.scalar(
            select(TeamMembership).where(
                TeamMembership.team_id == uuid.UUID(team_id),
                TeamMembership.user_id == uuid.UUID(user_id),
                TeamMembership.membership_status == "active",
            )
        )
        assert membership is not None
        membership_id = str(membership.id)

    detail = client.get(f"/workforce/teams/{team_id}", follow_redirects=True)
    assert "Workforce Test User" in detail.text

    resp = client.post(
        f"/workforce/memberships/{membership_id}/end",
        data={"csrf_token": _csrf(client)},
    )
    assert resp.status_code == 303
    with SessionLocal() as db:
        ended = db.get(TeamMembership, uuid.UUID(membership_id))
        assert ended is not None
        assert ended.membership_status == "ended"


def test_set_reporting_line():
    client, _ = _manager()
    subordinate_id = _create_user(client, "wfsub")
    supervisor_id = _create_user(client, "wfsup")

    resp = client.post(
        f"/workforce/users/{subordinate_id}/reporting-line",
        data={"csrf_token": _csrf(client), "supervisor_user_id": supervisor_id},
    )
    assert resp.status_code == 303

    with SessionLocal() as db:
        line = db.scalar(
            select(ReportingAssignment).where(
                ReportingAssignment.subordinate_user_id == uuid.UUID(subordinate_id),
                ReportingAssignment.status == "active",
            )
        )
        assert line is not None
        assert str(line.supervisor_user_id) == supervisor_id

    detail = client.get(f"/workforce/users/{subordinate_id}", follow_redirects=True)
    assert "Workforce Test User" in detail.text
