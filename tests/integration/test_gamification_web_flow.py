"""Integration tests for phase 4D Phase E: the agent preferences page and the
workbench's private-progress panel - exercised through the browser routes,
with the achievement-refresh Celery task running eagerly (tests/conftest.py)
so a completion's award is visible on the very next page load.
"""

from __future__ import annotations

import re
import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.flags import service as flags
from tests.integration.conftest import make_user_with_role, zw_numbers
from tests.integration.test_standard_dispositions_web_flow import (
    _assign_via_web,
    _commit_contacts_via_web,
    _create_campaign_via_web,
    _csrf,
    _hidden,
    _launch_via_web,
)
from tests.integration.test_work_flow import _get_agent_id

pytestmark = pytest.mark.integration


def _set_flag(enabled: bool) -> None:
    with SessionLocal() as db:
        actor_id = make_user_with_role(
            f"gam-web-flag-{uuid.uuid4().hex[:8]}@example.com", "manager"
        )
        flags.set_flag(db, "agent_gamification_enabled", enabled, actor_id=actor_id)
        db.commit()


@pytest.fixture(autouse=True)
def _reset_gamification_flag_after_each_test():
    yield
    _set_flag(False)


def _campaign_with_agent(manager_client: TestClient, agent_client: TestClient) -> str:
    campaign_id = _create_campaign_via_web(manager_client)
    manager_client.post(
        f"/campaigns/{campaign_id}/dispositions",
        data={
            "csrf_token": _csrf(manager_client),
            "label": "Connected", "stable_semantic_code": f"conn_{uuid.uuid4().hex[:6]}",
            "next_action": "complete", "counts_as_connected": "true",
        },
        follow_redirects=False,
    )
    _commit_contacts_via_web(manager_client, campaign_id, zw_numbers(1), ["Contact"])
    launch = _launch_via_web(manager_client, campaign_id)
    assert launch.status_code == 303, launch.text
    _assign_via_web(manager_client, campaign_id, _get_agent_id(agent_client))
    return campaign_id


def _opt_in(agent_client: TestClient) -> None:
    resp = agent_client.post(
        "/agent/work/preferences",
        data={"csrf_token": _csrf(agent_client), "enabled": "true", "celebrations_enabled": "true"},
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text


def _lease_and_complete_connected(manager_client: TestClient, agent_client: TestClient) -> None:
    _campaign_with_agent(manager_client, agent_client)
    lease = agent_client.post(
        "/agent/work/next", data={"csrf_token": _csrf(agent_client)}, follow_redirects=False
    )
    assert lease.status_code == 303
    page = agent_client.get(lease.headers["location"])

    work_item_match = re.search(r'action="/agent/work/([0-9a-f-]+)/complete"', page.text)
    assert work_item_match, page.text
    disposition_match = re.search(r'<option value="([0-9a-f-]+)"', page.text)
    assert disposition_match, page.text
    complete = agent_client.post(
        f"/agent/work/{work_item_match.group(1)}/complete",
        data={
            "csrf_token": _csrf(agent_client),
            "lease_id": _hidden(page.text, "lease_id"),
            "disposition_id": disposition_match.group(1),
            "idempotency_key": str(uuid.uuid4()),
        },
        follow_redirects=False,
    )
    assert complete.status_code == 303, complete.text


def test_preferences_page_reports_unavailable_when_flag_is_off(agent_client):
    page = agent_client.get("/agent/work/preferences")
    assert page.status_code == 200
    assert "Not available yet" in page.text


def test_preferences_page_updates_and_persists(agent_client):
    _set_flag(True)
    resp = agent_client.post(
        "/agent/work/preferences",
        data={
            "csrf_token": _csrf(agent_client), "enabled": "true",
            "celebrations_enabled": "true", "daily_goal": "30",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    confirm = agent_client.get(resp.headers["location"])
    assert "Preferences updated." in confirm.text

    page = agent_client.get("/agent/work/preferences")
    assert 'value="30"' in page.text


def test_preferences_page_rejects_daily_goal_out_of_bounds(agent_client):
    _set_flag(True)
    resp = agent_client.post(
        "/agent/work/preferences",
        data={"csrf_token": _csrf(agent_client), "daily_goal": "999"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    result = agent_client.get(resp.headers["location"])
    assert "must be between" in result.text


def test_workbench_shows_only_factual_stats_when_flag_is_off(manager_client, agent_client):
    page = agent_client.get("/agent/work")
    assert "Attempts today" in page.text
    assert "gamification-panel" not in page.text


def test_workbench_shows_only_factual_stats_when_agent_has_not_opted_in(
    manager_client, agent_client
):
    _set_flag(True)
    page = agent_client.get("/agent/work")
    assert "Attempts today" in page.text
    assert "gamification-panel" not in page.text


def test_workbench_shows_progress_panel_once_flag_and_preference_are_both_on(
    manager_client, agent_client
):
    _set_flag(True)
    _opt_in(agent_client)
    page = agent_client.get("/agent/work")
    assert "gamification-panel" in page.text
    assert "Unique contacts handled" in page.text
    assert "Badges earned" in page.text
    assert "No badges yet" in page.text


def test_completing_a_disposition_awards_a_badge_visible_on_the_next_load(
    manager_client, agent_client
):
    _set_flag(True)
    _opt_in(agent_client)
    _lease_and_complete_connected(manager_client, agent_client)

    page = agent_client.get("/agent/work")
    assert "First Step" in page.text
    assert "New badge: First Step" in page.text  # the celebration banner


def test_celebration_banner_hidden_when_celebrations_disabled(manager_client, agent_client):
    _set_flag(True)
    agent_client.post(
        "/agent/work/preferences",
        data={
            "csrf_token": _csrf(agent_client), "enabled": "true", "celebrations_enabled": "",
        },
        follow_redirects=False,
    )
    _lease_and_complete_connected(manager_client, agent_client)

    page = agent_client.get("/agent/work")
    assert "First Step" in page.text  # badge still listed
    assert "New badge:" not in page.text  # but no celebration banner


def test_each_agent_sees_only_their_own_progress(manager_client, agent_client):
    from app.authz.capabilities import ROLE_AGENT
    from app.main import app
    from tests.integration.conftest import login

    _set_flag(True)
    _opt_in(agent_client)
    _lease_and_complete_connected(manager_client, agent_client)
    first_agent_page = agent_client.get("/agent/work")
    assert "First Step" in first_agent_page.text

    second_email = f"gam-agent2-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(second_email, ROLE_AGENT)
    with TestClient(app) as second_agent_client:
        login(second_agent_client, second_email)
        second_agent_client.post(
            "/agent/work/preferences",
            data={
                "csrf_token": _csrf(second_agent_client), "enabled": "true",
                "celebrations_enabled": "true",
            },
            follow_redirects=False,
        )
        second_page = second_agent_client.get("/agent/work")
        assert "First Step" not in second_page.text
        assert "No badges yet" in second_page.text
