"""Integration tests for campaign aggregate reports, the Viewer role, and scoped
audit search (Phase 4A-3)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models.audit import AuditEvent
from app.models.identity import Team, TeamMembership
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
        "external_code": f"c-{uuid.uuid4().hex[:8]}",
        "name": f"Reporting test {uuid.uuid4().hex[:6]}",
        "owning_scope_type": "organization",
        "default_region": "ZW",
        "timezone": "Africa/Harare",
        **_DEFAULT_PROVENANCE,
        **overrides,
    }
    resp = client.post("/api/v1/campaigns", json=payload, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


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


def _commit_contacts(
    client: TestClient, headers: dict, campaign_id: str, numbers: list[str], names: list[str]
) -> None:
    rows = "\n".join(f"{n},{name}" for n, name in zip(numbers, names, strict=True))
    csv_text = f"phone,name\n{rows}\n"
    files = {"file": ("c.csv", csv_text.encode(), "text/csv")}
    data = {"phone_column": "phone", "name_column": "name", "metadata_columns": ""}
    upload = client.post(
        f"/api/v1/campaigns/{campaign_id}/imports", files=files, data=data, headers=headers
    )
    assert upload.status_code == 200, upload.text
    job_id = upload.json()["id"]
    decide = client.patch(
        f"/api/v1/imports/{job_id}/decisions", json={"decision": "approve"}, headers=headers
    )
    commit = client.post(
        f"/api/v1/imports/{job_id}/commit",
        json={
            "decision_version": decide.json()["decision_version"],
            "idempotency_key": str(uuid.uuid4()),
        },
        headers=headers,
    )
    assert commit.status_code == 200, commit.text


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


def _scoped_client(role_code: str, team_id: uuid.UUID) -> TestClient:
    from app.main import app
    from tests.integration.conftest import login

    email = f"{role_code}-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(email, role_code, scope_type="team", scope_id=team_id)
    client = TestClient(app)
    login(client, email)
    return client


def test_campaign_stats_reconciles_against_attempts(manager_client, agent_client):
    headers = csrf_headers(manager_client)
    campaign_id = _create_campaign(manager_client, headers)
    numbers = zw_numbers(3)
    _commit_contacts(
        manager_client, headers, campaign_id, numbers, ["Alice", "Bob", "Carol"]
    )
    manager_client.post(f"/api/v1/campaigns/{campaign_id}/launch", headers=headers)

    complete_id = _create_disposition(
        manager_client, headers, campaign_id,
        stable_semantic_code="connected", next_action="complete",
        counts_as_connected=True, counts_as_conversion=True,
    )
    dnc_id = _create_disposition(
        manager_client, headers, campaign_id,
        stable_semantic_code="explicit_dnc", causes_dnc=True,
    )
    agent_id = agent_client.get("/api/v1/auth/me").json()["id"]
    manager_client.post(
        f"/api/v1/campaigns/{campaign_id}/assignments", json={"agent_id": agent_id}, headers=headers
    )

    lease1 = agent_client.post("/api/v1/work/next", headers=csrf_headers(agent_client)).json()
    agent_client.post(
        f"/api/v1/work/{lease1['work_item_id']}/complete",
        json={
            "lease_id": lease1["lease_id"], "disposition_id": complete_id,
            "idempotency_key": str(uuid.uuid4()),
        },
        headers=csrf_headers(agent_client),
    )
    lease2 = agent_client.post("/api/v1/work/next", headers=csrf_headers(agent_client)).json()
    agent_client.post(
        f"/api/v1/work/{lease2['work_item_id']}/complete",
        json={
            "lease_id": lease2["lease_id"], "disposition_id": dnc_id,
            "idempotency_key": str(uuid.uuid4()),
        },
        headers=csrf_headers(agent_client),
    )

    stats = manager_client.get(f"/api/v1/campaigns/{campaign_id}/stats")
    assert stats.status_code == 200, stats.text
    body = stats.json()
    assert body["total_contacts"] == 3
    assert body["assigned_agents"] == 1
    assert body["total_attempts"] == 2
    assert body["connected"] == 1
    assert body["conversions"] == 1
    assert body["dnc_requests"] == 1


def test_viewer_can_view_campaign_and_stats_but_nothing_else(manager_client):
    headers = csrf_headers(manager_client)
    campaign_id = _create_campaign(manager_client, headers)

    from app.main import app
    from tests.integration.conftest import login

    email = f"viewer-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(email, "viewer")
    viewer = TestClient(app)
    login(viewer, email)

    view = viewer.get(f"/api/v1/campaigns/{campaign_id}")
    assert view.status_code == 200, view.text
    stats = viewer.get(f"/api/v1/campaigns/{campaign_id}/stats")
    assert stats.status_code == 200, stats.text

    denied_create = viewer.post(
        "/api/v1/campaigns",
        json={
            "external_code": f"c-{uuid.uuid4().hex[:8]}", "name": "x",
            "owning_scope_type": "organization", "default_region": "ZW",
            "timezone": "Africa/Harare", **_DEFAULT_PROVENANCE,
        },
        headers=csrf_headers(viewer),
    )
    assert denied_create.status_code == 403
    denied_manage = viewer.patch(
        f"/api/v1/campaigns/{campaign_id}", json={"name": "renamed"}, headers=csrf_headers(viewer)
    )
    assert denied_manage.status_code == 403
    denied_assign = viewer.post(
        f"/api/v1/campaigns/{campaign_id}/assignments",
        json={"agent_id": str(uuid.uuid4())},
        headers=csrf_headers(viewer),
    )
    assert denied_assign.status_code == 403
    denied_audit = viewer.get("/api/v1/admin/audit-events")
    assert denied_audit.status_code == 403


def test_viewer_scoped_to_assigned_team_only(manager_client):
    headers = csrf_headers(manager_client)
    team_a = _create_team()
    team_b = _create_team()
    campaign_a = _create_campaign(
        manager_client, headers, owning_scope_type="team", owning_scope_id=str(team_a)
    )
    campaign_b = _create_campaign(
        manager_client, headers, owning_scope_type="team", owning_scope_id=str(team_b)
    )

    viewer_a = _scoped_client("viewer", team_a)
    visible = viewer_a.get(f"/api/v1/campaigns/{campaign_a}").status_code
    hidden = viewer_a.get(f"/api/v1/campaigns/{campaign_b}").status_code
    assert visible == 200
    assert hidden == 403


def test_agent_cannot_view_campaign_stats(manager_client, agent_client):
    headers = csrf_headers(manager_client)
    campaign_id = _create_campaign(manager_client, headers)
    resp = agent_client.get(f"/api/v1/campaigns/{campaign_id}/stats")
    assert resp.status_code == 403


def test_audit_search_manager_sees_events_org_wide(manager_client):
    with SessionLocal() as db:
        db.add(AuditEvent(action="test.somewhere", result="success", actor_user_id=None))
        db.commit()
    resp = manager_client.get("/api/v1/admin/audit-events")
    assert resp.status_code == 200
    actions = {e["action"] for e in resp.json()}
    assert "test.somewhere" in actions


def test_audit_search_team_scoped_sees_only_own_team_and_self(manager_client):
    team_id = _create_team()
    team_member = make_user_with_role(f"member-{uuid.uuid4().hex[:8]}@example.com", "agent")
    outsider = make_user_with_role(f"outsider-{uuid.uuid4().hex[:8]}@example.com", "agent")
    with SessionLocal() as db:
        db.add(
            TeamMembership(team_id=team_id, user_id=team_member, effective_from=datetime.now(UTC))
        )
        db.add(AuditEvent(action="test.member_action", result="success", actor_user_id=team_member))
        db.add(AuditEvent(action="test.outsider_action", result="success", actor_user_id=outsider))
        db.commit()

    leader = _scoped_client("team_leader", team_id)
    events = leader.get("/api/v1/admin/audit-events").json()
    actions = {e["action"] for e in events}
    assert "test.member_action" in actions
    assert "test.outsider_action" not in actions


def test_agent_cannot_search_audit_events(agent_client):
    resp = agent_client.get("/api/v1/admin/audit-events")
    assert resp.status_code == 403


def test_audit_search_action_filter_narrows_within_scope(manager_client):
    unique = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        db.add(
            AuditEvent(action=f"filtertest.match.{unique}", result="success", actor_user_id=None)
        )
        db.add(
            AuditEvent(action=f"filtertest.other.{unique}", result="success", actor_user_id=None)
        )
        db.commit()

    resp = manager_client.get(f"/api/v1/admin/audit-events?action=match.{unique}")
    assert resp.status_code == 200
    actions = {e["action"] for e in resp.json()}
    assert f"filtertest.match.{unique}" in actions
    assert f"filtertest.other.{unique}" not in actions


def test_audit_search_result_filter(manager_client):
    unique = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        db.add(
            AuditEvent(action=f"filtertest.result.{unique}", result="denied", actor_user_id=None)
        )
        db.commit()

    matching = manager_client.get(
        f"/api/v1/admin/audit-events?action=filtertest.result.{unique}&result=denied"
    ).json()
    assert len(matching) == 1
    assert matching[0]["result"] == "denied"

    non_matching = manager_client.get(
        f"/api/v1/admin/audit-events?action=filtertest.result.{unique}&result=success"
    ).json()
    assert non_matching == []


def test_audit_search_date_range_filter(manager_client):
    unique = uuid.uuid4().hex[:8]
    with SessionLocal() as db:
        db.add(
            AuditEvent(
                action=f"filtertest.old.{unique}",
                result="success",
                actor_user_id=None,
                occurred_at=datetime(2020, 1, 1, tzinfo=UTC),
            )
        )
        db.commit()

    excluded = manager_client.get(
        f"/api/v1/admin/audit-events"
        f"?action=filtertest.old.{unique}&since=2026-01-01T00:00:00Z"
    ).json()
    assert excluded == []

    included = manager_client.get(
        f"/api/v1/admin/audit-events?action=filtertest.old.{unique}"
    ).json()
    actions = {e["action"] for e in included}
    assert f"filtertest.old.{unique}" in actions
