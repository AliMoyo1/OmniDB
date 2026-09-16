"""Integration tests for phase 4D Phase C: the manager "Call outcome policy"
panel, the attempt-limit review screen, and the agent workbench's standard
seven-outcome control - all exercised through the browser routes only, per
the phase's own exit criterion ("an agent can execute every outcome without
using the JSON API").
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models.campaign import CampaignDispositionDefinition
from app.models.work import WorkItem
from tests.integration.conftest import zw_numbers
from tests.integration.test_work_flow import _get_agent_id

pytestmark = pytest.mark.integration

_DEFAULT_PROVENANCE = {
    "purpose": "Customer outreach",
    "data_source": "CRM export",
    "data_obtained_at": "2026-01-01",
    "lawful_basis_or_consent_reference": "consent-ref-123",
}


def _csrf(client: TestClient) -> str:
    token = client.cookies.get("cc_csrf")
    assert token
    return token


def _hidden(html: str, name: str) -> str:
    match = re.search(rf'name="{re.escape(name)}" value="([^"]+)"', html)
    assert match, f"hidden input {name} not found"
    return match.group(1)


def _create_campaign_via_web(client: TestClient, **overrides) -> str:
    data = {
        "external_code": f"c-{uuid.uuid4().hex[:8]}",
        "name": f"Web standard test {uuid.uuid4().hex[:6]}",
        **_DEFAULT_PROVENANCE,
        **overrides,
    }
    resp = client.post(
        "/campaigns", data={"csrf_token": _csrf(client), **data}, follow_redirects=False
    )
    assert resp.status_code == 303, resp.text
    match = re.fullmatch(r"/campaigns/([0-9a-f-]+)(?:\?.*)?", resp.headers["location"])
    assert match
    return match.group(1)


def _install_standard_policy_via_web(client: TestClient, campaign_id: str, **overrides):
    resp = client.post(
        f"/campaigns/{campaign_id}/disposition-policy/install",
        data={"csrf_token": _csrf(client), **overrides},
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text
    return client.get(resp.headers["location"])


def _commit_contacts_via_web(
    client: TestClient, campaign_id: str, numbers: list[str], names: list[str]
) -> None:
    rows = "\n".join(f"{n},{name}" for n, name in zip(numbers, names, strict=True))
    upload = client.post(
        f"/campaigns/{campaign_id}/imports",
        data={"csrf_token": _csrf(client), "phone_column": "phone", "name_column": "name"},
        files={"file": ("c.csv", f"phone,name\n{rows}\n".encode(), "text/csv")},
        follow_redirects=False,
    )
    assert upload.status_code == 303, upload.text
    detail = client.get(upload.headers["location"])
    import_id_match = re.search(r"/imports/([0-9a-f-]+)/decision", detail.text)
    assert import_id_match
    import_id = import_id_match.group(1)

    decision = client.post(
        f"/campaigns/{campaign_id}/imports/{import_id}/decision",
        data={"csrf_token": _csrf(client), "decision": "approve", "note": ""},
        follow_redirects=False,
    )
    assert decision.status_code == 303
    reviewed = client.get(decision.headers["location"])

    commit = client.post(
        f"/campaigns/{campaign_id}/imports/{import_id}/commit",
        data={
            "csrf_token": _csrf(client),
            "decision_version": _hidden(reviewed.text, "decision_version"),
            "idempotency_key": _hidden(reviewed.text, "idempotency_key"),
        },
        follow_redirects=False,
    )
    assert commit.status_code == 303, commit.text


def _launch_via_web(client: TestClient, campaign_id: str):
    return client.post(
        f"/campaigns/{campaign_id}/lifecycle",
        data={"csrf_token": _csrf(client), "action": "launch"},
        follow_redirects=False,
    )


def _assign_via_web(client: TestClient, campaign_id: str, agent_id) -> None:
    resp = client.post(
        f"/campaigns/{campaign_id}/assignments",
        data={
            "csrf_token": _csrf(client), "agent_id": str(agent_id), "assignment_type": "primary",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text


def _standard_campaign_ready_for_agent(
    manager_client: TestClient, agent_client: TestClient, *, contact_count: int = 1
) -> str:
    """A launched, standard-policy campaign with one agent assigned - the
    common setup shared by the agent-workbench tests below."""
    campaign_id = _create_campaign_via_web(manager_client)
    _install_standard_policy_via_web(manager_client, campaign_id)
    numbers = zw_numbers(contact_count)
    names = [f"Contact{i}" for i in range(contact_count)]
    _commit_contacts_via_web(manager_client, campaign_id, numbers, names)
    launch = _launch_via_web(manager_client, campaign_id)
    assert launch.status_code == 303, launch.text
    _assign_via_web(manager_client, campaign_id, _get_agent_id(agent_client))
    return campaign_id


def _lease_and_select(agent_client: TestClient, campaign_id: str, code: str) -> tuple[str, str]:
    """POST /agent/work/next, then return (work_item_id, disposition_id) for
    the standard disposition matching `code`, so a test can complete it."""
    resp = agent_client.post(
        "/agent/work/next", data={"csrf_token": _csrf(agent_client)}, follow_redirects=False
    )
    assert resp.status_code == 303, resp.text
    page = agent_client.get(resp.headers["location"])
    work_item_id_match = re.search(r'action="/agent/work/([0-9a-f-]+)/complete"', page.text)
    assert work_item_id_match, page.text
    with SessionLocal() as db:
        disposition = db.scalar(
            select(CampaignDispositionDefinition).where(
                CampaignDispositionDefinition.campaign_id == uuid.UUID(campaign_id),
                CampaignDispositionDefinition.stable_semantic_code == code,
            )
        )
        assert disposition is not None
        return work_item_id_match.group(1), str(disposition.id)


def _complete_via_web(agent_client: TestClient, work_item_id: str, disposition_id: str, **extra):
    lease_id = extra.pop("lease_id", None)
    if lease_id is None:
        page = agent_client.get("/agent/work")
        lease_id = _hidden(page.text, "lease_id")
    data = {
        "csrf_token": _csrf(agent_client), "lease_id": lease_id, "disposition_id": disposition_id,
        "idempotency_key": str(uuid.uuid4()), **extra,
    }
    resp = agent_client.post(
        f"/agent/work/{work_item_id}/complete", data=data, follow_redirects=False
    )
    assert resp.status_code == 303, resp.text
    return agent_client.get(resp.headers["location"])


# --- manager campaign control room --------------------------------------------


def test_new_draft_campaign_offers_install_standard_policy(manager_client):
    campaign_id = _create_campaign_via_web(manager_client)
    detail = manager_client.get(f"/campaigns/{campaign_id}")
    assert "Use the standard call outcomes instead?" in detail.text
    assert f'action="/campaigns/{campaign_id}/disposition-policy/install"' in detail.text


def test_manager_can_install_standard_policy_and_sees_locked_panel(manager_client):
    campaign_id = _create_campaign_via_web(manager_client)
    detail = _install_standard_policy_via_web(
        manager_client, campaign_id,
        no_answer_retry_minutes="90", unavailable_retry_minutes="20",
    )
    assert "Standard call outcomes installed." in detail.text
    assert "Call outcome policy" in detail.text
    assert "Policy version 1" in detail.text
    for label in (
        "Connected", "No Answer", "Call Back Later", "Hung Up",
        "Number Disconnected", "Do Not Call", "Unavailable",
    ):
        assert label in detail.text
    assert "valid — all seven standard outcomes" in detail.text.lower()
    assert 'value="90"' in detail.text
    assert 'value="20"' in detail.text
    # Legacy free-form controls are gone once a policy is installed.
    assert f'action="/campaigns/{campaign_id}/dispositions"' not in detail.text


def test_manager_can_update_a_retry_delay_before_launch(manager_client):
    campaign_id = _create_campaign_via_web(manager_client)
    _install_standard_policy_via_web(manager_client, campaign_id)
    resp = manager_client.post(
        f"/campaigns/{campaign_id}/disposition-policy/retries",
        data={
            "csrf_token": _csrf(manager_client),
            "stable_semantic_code": "no_answer", "retry_delay_minutes": "45",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    detail = manager_client.get(resp.headers["location"])
    assert "Retry delay updated." in detail.text
    assert 'value="45"' in detail.text


def test_launch_is_blocked_when_standard_policy_is_invalid(manager_client, agent_client):
    campaign_id = _create_campaign_via_web(manager_client)
    _install_standard_policy_via_web(manager_client, campaign_id)
    _commit_contacts_via_web(manager_client, campaign_id, zw_numbers(1), ["Contact"])

    with SessionLocal() as db:
        row = db.scalar(
            select(CampaignDispositionDefinition).where(
                CampaignDispositionDefinition.campaign_id == uuid.UUID(campaign_id),
                CampaignDispositionDefinition.stable_semantic_code == "explicit_dnc",
            )
        )
        assert row is not None
        row.active = False
        db.commit()

    launch = _launch_via_web(manager_client, campaign_id)
    assert launch.status_code == 303
    result = manager_client.get(launch.headers["location"])
    assert "standard disposition policy is invalid" in result.text
    assert "status-draft" in result.text


def test_launch_succeeds_for_a_valid_standard_policy(manager_client):
    campaign_id = _create_campaign_via_web(manager_client)
    _install_standard_policy_via_web(manager_client, campaign_id)
    _commit_contacts_via_web(manager_client, campaign_id, zw_numbers(1), ["Contact"])
    launch = _launch_via_web(manager_client, campaign_id)
    assert launch.status_code == 303
    result = manager_client.get(launch.headers["location"])
    assert "status-active" in result.text


def test_legacy_campaign_keeps_the_free_form_disposition_builder(manager_client):
    campaign_id = _create_campaign_via_web(manager_client)
    manager_client.post(
        f"/campaigns/{campaign_id}/dispositions",
        data={
            "csrf_token": _csrf(manager_client),
            "label": "Reached", "stable_semantic_code": f"reached_{uuid.uuid4().hex[:6]}",
            "next_action": "complete",
        },
        follow_redirects=False,
    )
    detail = manager_client.get(f"/campaigns/{campaign_id}")
    assert "Disposition set" in detail.text
    assert "Call outcome policy" not in detail.text
    # No dispositions left, but the campaign now has one - the install
    # shortcut only offers itself to an empty draft campaign.
    assert "Use the standard call outcomes instead?" not in detail.text


# --- agent workbench: exact manifest order and help text -----------------------


def test_agent_workbench_lists_standard_outcomes_in_manifest_order_with_help(
    manager_client, agent_client
):
    _standard_campaign_ready_for_agent(manager_client, agent_client)
    agent_client.post("/agent/work/next", data={"csrf_token": _csrf(agent_client)})
    page = agent_client.get("/agent/work")

    labels_in_order = re.findall(r'<option value="[0-9a-f-]+"[^>]*>([^<]+)</option>', page.text)
    assert labels_in_order == [
        "Connected", "No Answer", "Call Back Later", "Hung Up",
        "Number Disconnected", "Do Not Call", "Unavailable",
    ]
    assert "This number will return to the pool after 60 minutes." in page.text
    assert "kept ready for immediate redial" in page.text
    assert "standard set, in order" in page.text


# --- agent workbench: each outcome's feedback -----------------------------------


def test_agent_connected_shows_completed_message(manager_client, agent_client):
    campaign_id = _standard_campaign_ready_for_agent(manager_client, agent_client)
    work_item_id, disposition_id = _lease_and_select(agent_client, campaign_id, "connected")
    result = _complete_via_web(agent_client, work_item_id, disposition_id)
    assert "Number completed." in result.text


def test_agent_number_disconnected_shows_completed_message(manager_client, agent_client):
    campaign_id = _standard_campaign_ready_for_agent(manager_client, agent_client)
    work_item_id, disposition_id = _lease_and_select(
        agent_client, campaign_id, "number_disconnected"
    )
    result = _complete_via_web(agent_client, work_item_id, disposition_id)
    assert "Number completed." in result.text


def test_agent_explicit_dnc_shows_suppression_message(manager_client, agent_client):
    campaign_id = _standard_campaign_ready_for_agent(manager_client, agent_client)
    work_item_id, disposition_id = _lease_and_select(agent_client, campaign_id, "explicit_dnc")
    result = _complete_via_web(agent_client, work_item_id, disposition_id)
    assert "Do-not-call recorded." in result.text


def test_agent_callback_later_shows_scheduled_time(manager_client, agent_client):
    campaign_id = _standard_campaign_ready_for_agent(manager_client, agent_client)
    work_item_id, disposition_id = _lease_and_select(agent_client, campaign_id, "callback_later")
    local_future = datetime.now(ZoneInfo("Africa/Harare")) + timedelta(hours=2)
    callback_at = local_future.strftime("%Y-%m-%dT%H:%M")
    result = _complete_via_web(
        agent_client, work_item_id, disposition_id, callback_at=callback_at
    )
    assert "Callback scheduled for" in result.text


def test_agent_no_answer_shows_pool_return_time(manager_client, agent_client):
    campaign_id = _standard_campaign_ready_for_agent(manager_client, agent_client)
    work_item_id, disposition_id = _lease_and_select(agent_client, campaign_id, "no_answer")
    result = _complete_via_web(agent_client, work_item_id, disposition_id)
    assert "This number will return to the pool at" in result.text


def test_agent_unavailable_shows_pool_return_time(manager_client, agent_client):
    campaign_id = _standard_campaign_ready_for_agent(manager_client, agent_client)
    work_item_id, disposition_id = _lease_and_select(agent_client, campaign_id, "unavailable")
    result = _complete_via_web(agent_client, work_item_id, disposition_id)
    assert "This number will return to the pool at" in result.text


def test_agent_hung_up_shows_redial_ready_and_resumes_same_contact(manager_client, agent_client):
    campaign_id = _standard_campaign_ready_for_agent(manager_client, agent_client)
    work_item_id, disposition_id = _lease_and_select(agent_client, campaign_id, "hung_up")
    result = _complete_via_web(agent_client, work_item_id, disposition_id)
    assert "ready for immediate redial - press Redial now" in result.text

    resumed = agent_client.get("/agent/work")
    assert f'action="/agent/work/{work_item_id}/complete"' in resumed.text
    assert "Redial now" in resumed.text
    assert "Redial hold" in resumed.text
    assert "redial-ready" in resumed.text


def test_agent_hung_up_at_attempt_ceiling_reports_review(manager_client, agent_client):
    campaign_id = _standard_campaign_ready_for_agent(manager_client, agent_client)
    work_item_id, disposition_id = _lease_and_select(agent_client, campaign_id, "hung_up")
    with SessionLocal() as db:
        item = db.get(WorkItem, uuid.UUID(work_item_id))
        item.max_attempts = 1
        db.commit()
    result = _complete_via_web(agent_client, work_item_id, disposition_id)
    assert "gone to manager review" in result.text


# --- attempt-limit review screen -------------------------------------------------


def _push_to_review_via_web(manager_client, agent_client, campaign_id: str) -> str:
    work_item_id, disposition_id = _lease_and_select(agent_client, campaign_id, "no_answer")
    with SessionLocal() as db:
        item = db.get(WorkItem, uuid.UUID(work_item_id))
        item.max_attempts = 1
        db.commit()
    _complete_via_web(agent_client, work_item_id, disposition_id)
    return work_item_id


def test_review_screen_lists_masked_reference_and_last_outcome(manager_client, agent_client):
    campaign_id = _standard_campaign_ready_for_agent(manager_client, agent_client)
    _push_to_review_via_web(manager_client, agent_client, campaign_id)
    screen = manager_client.get(f"/campaigns/{campaign_id}/review")
    assert "1 awaiting review" in screen.text
    assert "no_answer" in screen.text
    assert "1 / 1 attempts" in screen.text
    assert "+263" not in screen.text  # never a raw E.164 phone number


def test_review_screen_try_again_later_returns_item_to_retry_wait(manager_client, agent_client):
    campaign_id = _standard_campaign_ready_for_agent(manager_client, agent_client)
    work_item_id = _push_to_review_via_web(manager_client, agent_client, campaign_id)
    retry_at = (datetime.now(ZoneInfo("Africa/Harare")) + timedelta(hours=3)).strftime(
        "%Y-%m-%dT%H:%M"
    )
    resp = manager_client.post(
        f"/campaigns/{campaign_id}/review/{work_item_id}/retry",
        data={
            "csrf_token": _csrf(manager_client), "retry_at": retry_at,
            "reason": "Known good contact, worth one more try",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    screen = manager_client.get(resp.headers["location"])
    assert "Retry scheduled." in screen.text
    with SessionLocal() as db:
        item = db.get(WorkItem, uuid.UUID(work_item_id))
        assert item.state == "retry_wait"


def test_review_screen_allow_more_attempts_returns_to_shared_pool(manager_client, agent_client):
    campaign_id = _standard_campaign_ready_for_agent(manager_client, agent_client)
    work_item_id = _push_to_review_via_web(manager_client, agent_client, campaign_id)
    resp = manager_client.post(
        f"/campaigns/{campaign_id}/review/{work_item_id}/allow-more",
        data={
            "csrf_token": _csrf(manager_client), "new_max_attempts": "3",
            "reason": "Raise the ceiling for this batch",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    screen = manager_client.get(resp.headers["location"])
    assert "back in the shared pool now" in screen.text
    with SessionLocal() as db:
        item = db.get(WorkItem, uuid.UUID(work_item_id))
        assert item.state == "retry_wait"
        assert item.max_attempts == 3


def test_review_screen_close_records_last_outcome_and_agent(manager_client, agent_client):
    campaign_id = _standard_campaign_ready_for_agent(manager_client, agent_client)
    agent_id = _get_agent_id(agent_client)
    work_item_id = _push_to_review_via_web(manager_client, agent_client, campaign_id)
    resp = manager_client.post(
        f"/campaigns/{campaign_id}/review/{work_item_id}/close",
        data={"csrf_token": _csrf(manager_client), "reason": "Exhausted retries per policy"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    screen = manager_client.get(resp.headers["location"])
    assert "Contact closed after repeated attempts." in screen.text
    with SessionLocal() as db:
        item = db.get(WorkItem, uuid.UUID(work_item_id))
        assert item.state == "completed"
        from app.models.contact import CampaignContact

        campaign_contact = db.get(CampaignContact, item.campaign_contact_id)
        assert campaign_contact.final_disposition_code == "no_answer"
        assert campaign_contact.completed_by_agent_id == agent_id


def test_review_action_rejects_a_work_item_from_a_different_campaign(
    manager_client, agent_client
):
    campaign_a = _standard_campaign_ready_for_agent(manager_client, agent_client, contact_count=1)
    work_item_id = _push_to_review_via_web(manager_client, agent_client, campaign_a)

    campaign_b = _create_campaign_via_web(manager_client)
    _install_standard_policy_via_web(manager_client, campaign_b)

    resp = manager_client.post(
        f"/campaigns/{campaign_b}/review/{work_item_id}/close",
        data={"csrf_token": _csrf(manager_client), "reason": "should not apply"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    screen = manager_client.get(resp.headers["location"])
    assert "Review item not found." in screen.text
    with SessionLocal() as db:
        item = db.get(WorkItem, uuid.UUID(work_item_id))
        assert item.state == "review"  # unaffected
