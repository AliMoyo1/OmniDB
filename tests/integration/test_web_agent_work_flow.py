"""Browser-form coverage for the agent workbench."""

from __future__ import annotations

import re
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models.work import WorkItem
from tests.integration.conftest import csrf_headers
from tests.integration.test_work_flow import (
    _create_disposition,
    _get_agent_id,
    _setup_campaign_with_agent,
)

pytestmark = pytest.mark.integration


def _csrf(client: TestClient) -> str:
    token = client.cookies.get("cc_csrf")
    assert token
    return token


def _hidden(html: str, name: str) -> str:
    match = re.search(rf'name="{re.escape(name)}" value="([^"]+)"', html)
    assert match, f"hidden input {name} not found"
    return match.group(1)


def test_unauthenticated_workbench_redirects_to_login():
    from app.main import app

    client = TestClient(app, follow_redirects=False)
    response = client.get("/agent/work")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_manager_cannot_open_agent_workbench(manager_client):
    response = manager_client.get("/agent/work", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/dashboard?flash_error=")


def test_agent_without_assignment_sees_ready_state(agent_client):
    response = agent_client.get("/agent/work")
    assert response.status_code == 200
    assert "Ready for your next contact?" in response.text
    assert 'action="/agent/work/next"' in response.text


def test_next_refresh_and_double_submit_resume_one_contact(manager_client, agent_client):
    agent_id = _get_agent_id(agent_client)
    _, numbers = _setup_campaign_with_agent(manager_client, agent_client, agent_id, contact_count=2)

    first = agent_client.post(
        "/agent/work/next", data={"csrf_token": _csrf(agent_client)}, follow_redirects=False
    )
    second = agent_client.post(
        "/agent/work/next", data={"csrf_token": _csrf(agent_client)}, follow_redirects=False
    )
    assert first.status_code == second.status_code == 303

    page = agent_client.get("/agent/work")
    assert page.status_code == 200
    visible_numbers = [number for number in numbers if number in page.text]
    assert len(visible_numbers) == 1
    lease_id = _hidden(page.text, "lease_id")
    refreshed = agent_client.get("/agent/work")
    assert _hidden(refreshed.text, "lease_id") == lease_id

    with SessionLocal() as db:
        active_count = db.scalar(
            select(func.count(WorkItem.id)).where(
                WorkItem.state == "leased", WorkItem.lease_owner_id == agent_id
            )
        )
        assert active_count == 1


def test_complete_contact_via_workbench_form(manager_client, agent_client):
    agent_id = _get_agent_id(agent_client)
    campaign_id, numbers = _setup_campaign_with_agent(manager_client, agent_client, agent_id)
    disposition_id = _create_disposition(
        manager_client,
        csrf_headers(manager_client),
        campaign_id,
        label="Reached customer",
        stable_semantic_code=f"reached_{uuid.uuid4().hex[:6]}",
    )
    agent_client.post("/agent/work/next", data={"csrf_token": _csrf(agent_client)})
    page = agent_client.get("/agent/work")
    work_item_id = re.search(r'action="/agent/work/([^/]+)/complete"', page.text)
    assert work_item_id

    response = agent_client.post(
        f"/agent/work/{work_item_id.group(1)}/complete",
        data={
            "csrf_token": _csrf(agent_client),
            "lease_id": _hidden(page.text, "lease_id"),
            "disposition_id": disposition_id,
            "idempotency_key": _hidden(page.text, "idempotency_key"),
            "notes": "",
            "callback_at": "",
            "duration_seconds": "4",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "flash_success" in response.headers["location"]
    after = agent_client.get("/agent/work")
    assert numbers[0] not in after.text
    assert "Ready for your next contact?" in after.text


def test_workbench_rejects_blank_skip_and_bad_csrf(manager_client, agent_client):
    agent_id = _get_agent_id(agent_client)
    _setup_campaign_with_agent(manager_client, agent_client, agent_id)
    agent_client.post("/agent/work/next", data={"csrf_token": _csrf(agent_client)})
    page = agent_client.get("/agent/work")
    work_item_id = re.search(r'action="/agent/work/([^/]+)/skip"', page.text)
    assert work_item_id
    url = f"/agent/work/{work_item_id.group(1)}/skip"

    blank = agent_client.post(
        url,
        data={
            "csrf_token": _csrf(agent_client),
            "lease_id": _hidden(page.text, "lease_id"),
            "reason": " ",
        },
        follow_redirects=False,
    )
    assert "flash_error" in blank.headers["location"]

    bad_csrf = agent_client.post(
        url,
        data={
            "csrf_token": "bad",
            "lease_id": _hidden(page.text, "lease_id"),
            "reason": "Duplicate",
        },
        follow_redirects=False,
    )
    assert bad_csrf.status_code == 303
    assert bad_csrf.headers["location"].startswith("/agent/work?flash_error=")
