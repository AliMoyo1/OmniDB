"""Integration coverage for the server-rendered Campaign Control Room."""

from __future__ import annotations

import re
import uuid

import pytest
from fastapi.testclient import TestClient

from app.authz.capabilities import ROLE_VIEWER
from tests.integration.conftest import (
    TEST_PASSWORD,
    login,
    make_user_with_role,
    zw_numbers,
)
from tests.integration.test_work_flow import _get_agent_id

pytestmark = pytest.mark.integration


def _csrf(client: TestClient) -> str:
    token = client.cookies.get("cc_csrf")
    assert token
    return token


def _hidden(html: str, name: str) -> str:
    match = re.search(rf'name="{re.escape(name)}" value="([^"]+)"', html)
    assert match, f"hidden input {name} not found"
    return match.group(1)


def _create_via_form(client: TestClient) -> str:
    response = client.post(
        "/campaigns",
        data={
            "csrf_token": _csrf(client),
            "name": f"Browser campaign {uuid.uuid4().hex[:8]}",
            "purpose": "Customer outreach",
            "data_source": "Approved CRM export",
            "data_obtained_at": "2026-01-01",
            "lawful_basis_or_consent_reference": "consent-ref-123",
            "description": "Browser workflow test",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    match = re.fullmatch(r"/campaigns/([0-9a-f-]+)(?:\?.*)?", response.headers["location"])
    assert match
    return match.group(1)


def test_unauthenticated_campaign_operations_redirect_to_login():
    from app.main import app

    client = TestClient(app, follow_redirects=False)
    response = client.get("/campaigns")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_campaign_list_renders_control_room_and_creation_form(manager_client):
    response = manager_client.get("/campaigns")
    assert response.status_code == 200
    assert "Campaign control room" in response.text
    assert 'action="/campaigns"' in response.text
    assert "Raw contact data never appears here." not in response.text


def test_manager_can_stage_review_commit_launch_and_assign_from_browser(
    manager_client, agent_client
):
    campaign_id = _create_via_form(manager_client)
    phone = zw_numbers(1)[0]
    upload = manager_client.post(
        f"/campaigns/{campaign_id}/imports",
        data={
            "csrf_token": _csrf(manager_client),
            "phone_column": "phone",
            "name_column": "name",
            "metadata_columns": "region",
        },
        files={"file": ("approved.csv", f"phone,name,region\n{phone},Ada,Harare\n", "text/csv")},
        follow_redirects=False,
    )
    assert upload.status_code == 303
    assert "import=" in upload.headers["location"]

    detail = manager_client.get(upload.headers["location"])
    assert detail.status_code == 200
    assert "approved.csv" in detail.text
    assert phone not in detail.text
    import_id_match = re.search(r"/imports/([0-9a-f-]+)/decision", detail.text)
    assert import_id_match
    import_id = import_id_match.group(1)

    decision = manager_client.post(
        f"/campaigns/{campaign_id}/imports/{import_id}/decision",
        data={"csrf_token": _csrf(manager_client), "decision": "approve", "note": "Validated"},
        follow_redirects=False,
    )
    assert decision.status_code == 303
    reviewed = manager_client.get(decision.headers["location"])
    assert "Latest decision:" in reviewed.text
    assert "approve" in reviewed.text

    commit = manager_client.post(
        f"/campaigns/{campaign_id}/imports/{import_id}/commit",
        data={
            "csrf_token": _csrf(manager_client),
            "decision_version": _hidden(reviewed.text, "decision_version"),
            "idempotency_key": _hidden(reviewed.text, "idempotency_key"),
        },
        follow_redirects=False,
    )
    assert commit.status_code == 303
    committed = manager_client.get(commit.headers["location"])
    assert "Import committed: 1 contacts queued, 0 suppressed." in committed.text

    disposition = manager_client.post(
        f"/campaigns/{campaign_id}/dispositions",
        data={
            "csrf_token": _csrf(manager_client),
            "label": "Reached customer",
            "stable_semantic_code": f"reached_{uuid.uuid4().hex[:8]}",
            "next_action": "complete",
            "counts_as_connected": "true",
        },
        follow_redirects=False,
    )
    assert disposition.status_code == 303

    assign = manager_client.post(
        f"/campaigns/{campaign_id}/assignments",
        data={
            "csrf_token": _csrf(manager_client),
            "agent_id": str(_get_agent_id(agent_client)),
            "assignment_type": "primary",
        },
        follow_redirects=False,
    )
    assert assign.status_code == 303

    launch = manager_client.post(
        f"/campaigns/{campaign_id}/lifecycle",
        data={"csrf_token": _csrf(manager_client), "action": "launch"},
        follow_redirects=False,
    )
    assert launch.status_code == 303
    launched = manager_client.get(launch.headers["location"])
    assert "status-active" in launched.text
    assert "Reached customer" in launched.text
    assert "Agent assigned to campaign." not in launched.text


def test_viewer_can_read_campaign_but_cannot_see_management_forms(manager_client):
    from app.main import app

    campaign_id = _create_via_form(manager_client)
    email = f"viewer-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(email, ROLE_VIEWER)
    viewer = TestClient(app, follow_redirects=False)
    login(viewer, email, TEST_PASSWORD)

    response = viewer.get(f"/campaigns/{campaign_id}")
    assert response.status_code == 200
    assert "Import and validate" in response.text
    assert f'action="/campaigns/{campaign_id}/imports"' not in response.text
    assert f'action="/campaigns/{campaign_id}/dispositions"' not in response.text
    assert f'action="/campaigns/{campaign_id}/assignments"' not in response.text


def test_campaign_form_csrf_returns_to_campaign_area(manager_client):
    response = manager_client.post(
        "/campaigns",
        data={
            "csrf_token": "invalid",
            "name": "Should not exist",
            "purpose": "Test",
            "data_source": "Test",
            "data_obtained_at": "2026-01-01",
            "lawful_basis_or_consent_reference": "test",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/campaigns?flash_error=")
