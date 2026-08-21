"""Integration tests for agent leasing, completion, callbacks, and skip (real Postgres)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models.contact import CampaignContact, SuppressionEntry
from app.models.work import WorkItem
from app.security.phone import protect
from app.work import service as work_service
from tests.integration.conftest import assign_agent_to_campaign, csrf_headers, zw_numbers

pytestmark = pytest.mark.integration

_DEFAULT_PROVENANCE = {
    "purpose": "Customer outreach",
    "data_source": "CRM export",
    "data_obtained_at": "2026-01-01",
    "lawful_basis_or_consent_reference": "consent-ref-123",
}


def _create_campaign(client: TestClient, headers: dict, **overrides) -> str:
    payload = {
        "name": f"Work test campaign {uuid.uuid4().hex[:6]}",
        "owning_scope_type": "organization",
        "default_region": "ZW",
        "timezone": "Africa/Harare",
        **_DEFAULT_PROVENANCE,
        **overrides,
    }
    resp = client.post("/api/v1/campaigns", json=payload, headers=headers)
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
    decision_version = decide.json()["decision_version"]
    commit = client.post(
        f"/api/v1/imports/{job_id}/commit",
        json={"decision_version": decision_version, "idempotency_key": str(uuid.uuid4())},
        headers=headers,
    )
    assert commit.status_code == 200, commit.text


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


def _setup_campaign_with_agent(
    manager_client: TestClient,
    agent_client: TestClient,
    agent_id: uuid.UUID,
    contact_count: int = 1,
) -> tuple[str, list[str]]:
    headers = csrf_headers(manager_client)
    campaign_id = _create_campaign(manager_client, headers)
    numbers = zw_numbers(contact_count)
    names = [f"Contact{i}" for i in range(contact_count)]
    _commit_contacts(manager_client, headers, campaign_id, numbers, names)
    launch = manager_client.post(f"/api/v1/campaigns/{campaign_id}/launch", headers=headers)
    assert launch.status_code == 200, launch.text
    assign_agent_to_campaign(agent_id, uuid.UUID(campaign_id))
    return campaign_id, numbers


def _get_agent_id(client: TestClient) -> uuid.UUID:
    me = client.get("/api/v1/auth/me")
    return uuid.UUID(me.json()["id"])


def test_lease_next_with_no_assignment_returns_204(agent_client):
    resp = agent_client.post("/api/v1/work/next", headers=csrf_headers(agent_client))
    assert resp.status_code == 204


def test_lease_next_returns_one_contact_without_leaking_queue_size(
    manager_client, agent_client
):
    agent_id = _get_agent_id(agent_client)
    _setup_campaign_with_agent(manager_client, agent_client, agent_id, contact_count=2)

    resp = agent_client.post("/api/v1/work/next", headers=csrf_headers(agent_client))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body.keys()) == {
        "work_item_id", "lease_id", "lease_expires_at", "campaign_id", "campaign_name",
        "phone_e164", "contact_name", "approved_metadata", "is_callback",
    }
    assert body["phone_e164"].startswith("+263")
    assert body["is_callback"] is False


def test_complete_work_item_is_idempotent(manager_client, agent_client):
    agent_id = _get_agent_id(agent_client)
    campaign_id, _ = _setup_campaign_with_agent(manager_client, agent_client, agent_id)
    disposition_id = _create_disposition(
        manager_client, csrf_headers(manager_client), campaign_id,
        stable_semantic_code="connected", next_action="complete", counts_as_connected=True,
    )

    lease = agent_client.post("/api/v1/work/next", headers=csrf_headers(agent_client)).json()
    idem_key = str(uuid.uuid4())
    payload = {
        "lease_id": lease["lease_id"], "disposition_id": disposition_id,
        "idempotency_key": idem_key,
    }
    first = agent_client.post(
        f"/api/v1/work/{lease['work_item_id']}/complete", json=payload,
        headers=csrf_headers(agent_client),
    )
    assert first.status_code == 200, first.text
    second = agent_client.post(
        f"/api/v1/work/{lease['work_item_id']}/complete", json=payload,
        headers=csrf_headers(agent_client),
    )
    assert second.status_code == 200
    assert second.json() == first.json()


def test_idempotency_key_cannot_be_reused_for_another_work_item(
    manager_client, agent_client
):
    agent_id = _get_agent_id(agent_client)
    campaign_id, _ = _setup_campaign_with_agent(
        manager_client, agent_client, agent_id, contact_count=2
    )
    disposition_id = _create_disposition(
        manager_client, csrf_headers(manager_client), campaign_id
    )
    idem_key = str(uuid.uuid4())

    first_lease = agent_client.post(
        "/api/v1/work/next", headers=csrf_headers(agent_client)
    ).json()
    first = agent_client.post(
        f"/api/v1/work/{first_lease['work_item_id']}/complete",
        json={
            "lease_id": first_lease["lease_id"],
            "disposition_id": disposition_id,
            "idempotency_key": idem_key,
        },
        headers=csrf_headers(agent_client),
    )
    assert first.status_code == 200, first.text

    second_lease = agent_client.post(
        "/api/v1/work/next", headers=csrf_headers(agent_client)
    ).json()
    conflict = agent_client.post(
        f"/api/v1/work/{second_lease['work_item_id']}/complete",
        json={
            "lease_id": second_lease["lease_id"],
            "disposition_id": disposition_id,
            "idempotency_key": idem_key,
        },
        headers=csrf_headers(agent_client),
    )

    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "idempotency_conflict"


def test_complete_with_wrong_lease_id_is_rejected(manager_client, agent_client):
    agent_id = _get_agent_id(agent_client)
    campaign_id, _ = _setup_campaign_with_agent(manager_client, agent_client, agent_id)
    disposition_id = _create_disposition(manager_client, csrf_headers(manager_client), campaign_id)

    lease = agent_client.post("/api/v1/work/next", headers=csrf_headers(agent_client)).json()
    payload = {
        "lease_id": str(uuid.uuid4()), "disposition_id": disposition_id,
        "idempotency_key": str(uuid.uuid4()),
    }
    resp = agent_client.post(
        f"/api/v1/work/{lease['work_item_id']}/complete", json=payload,
        headers=csrf_headers(agent_client),
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "lease_conflict"


def test_explicit_dnc_suppresses_the_contact_across_campaigns(manager_client, agent_client):
    agent_id = _get_agent_id(agent_client)
    headers = csrf_headers(manager_client)
    numbers = zw_numbers(1)

    campaign_a = _create_campaign(manager_client, headers)
    _commit_contacts(manager_client, headers, campaign_a, numbers, ["Shared"])
    manager_client.post(f"/api/v1/campaigns/{campaign_a}/launch", headers=headers)
    assign_agent_to_campaign(agent_id, uuid.UUID(campaign_a))

    campaign_b = _create_campaign(manager_client, headers)
    _commit_contacts(manager_client, headers, campaign_b, numbers, ["Shared"])
    manager_client.post(f"/api/v1/campaigns/{campaign_b}/launch", headers=headers)

    dnc_disposition = _create_disposition(
        manager_client, headers, campaign_a,
        stable_semantic_code="explicit_dnc", causes_dnc=True,
    )

    lease = agent_client.post("/api/v1/work/next", headers=csrf_headers(agent_client)).json()
    complete = agent_client.post(
        f"/api/v1/work/{lease['work_item_id']}/complete",
        json={
            "lease_id": lease["lease_id"], "disposition_id": dnc_disposition,
            "idempotency_key": str(uuid.uuid4()),
        },
        headers=csrf_headers(agent_client),
    )
    assert complete.status_code == 200, complete.text
    assert complete.json()["work_item_state"] == "suppressed"

    with SessionLocal() as db:
        fingerprint = protect(numbers[0], "ZW").fingerprint
        suppression = db.scalar(
            select(SuppressionEntry).where(
                SuppressionEntry.phone_fingerprint == fingerprint,
                SuppressionEntry.status == "active",
            )
        )
        assert suppression is not None

        campaign_b_contacts = db.scalars(
            select(CampaignContact).where(CampaignContact.campaign_id == uuid.UUID(campaign_b))
        ).all()
        assert len(campaign_b_contacts) == 1
        assert campaign_b_contacts[0].status == "suppressed"

        work_items_b = db.scalars(
            select(WorkItem).where(
                WorkItem.campaign_contact_id == campaign_b_contacts[0].id
            )
        ).all()
        assert all(w.state == "suppressed" for w in work_items_b)


def test_requeue_disposition_returns_to_queue_then_review_at_attempt_limit(
    manager_client, agent_client
):
    agent_id = _get_agent_id(agent_client)
    campaign_id, _ = _setup_campaign_with_agent(manager_client, agent_client, agent_id)
    headers = csrf_headers(manager_client)
    disposition_id = _create_disposition(
        manager_client, headers, campaign_id,
        stable_semantic_code="no_answer", next_action="requeue",
    )

    lease1 = agent_client.post("/api/v1/work/next", headers=csrf_headers(agent_client)).json()
    with SessionLocal() as db:
        item = db.get(WorkItem, uuid.UUID(lease1["work_item_id"]))
        item.max_attempts = 2
        db.commit()

    complete1 = agent_client.post(
        f"/api/v1/work/{lease1['work_item_id']}/complete",
        json={
            "lease_id": lease1["lease_id"], "disposition_id": disposition_id,
            "idempotency_key": str(uuid.uuid4()),
        },
        headers=csrf_headers(agent_client),
    ).json()
    assert complete1["work_item_state"] == "queued"

    lease2 = agent_client.post("/api/v1/work/next", headers=csrf_headers(agent_client)).json()
    assert lease2["work_item_id"] == lease1["work_item_id"]
    complete2 = agent_client.post(
        f"/api/v1/work/{lease2['work_item_id']}/complete",
        json={
            "lease_id": lease2["lease_id"], "disposition_id": disposition_id,
            "idempotency_key": str(uuid.uuid4()),
        },
        headers=csrf_headers(agent_client),
    ).json()
    assert complete2["work_item_state"] == "review"


def test_callback_disposition_schedules_and_masks_reference(manager_client, agent_client):
    agent_id = _get_agent_id(agent_client)
    campaign_id, _ = _setup_campaign_with_agent(manager_client, agent_client, agent_id)
    headers = csrf_headers(manager_client)
    disposition_id = _create_disposition(
        manager_client, headers, campaign_id,
        stable_semantic_code="callback_requested", requires_callback_time=True,
    )

    lease = agent_client.post("/api/v1/work/next", headers=csrf_headers(agent_client)).json()
    callback_at = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
    complete = agent_client.post(
        f"/api/v1/work/{lease['work_item_id']}/complete",
        json={
            "lease_id": lease["lease_id"], "disposition_id": disposition_id,
            "callback_at": callback_at, "idempotency_key": str(uuid.uuid4()),
        },
        headers=csrf_headers(agent_client),
    )
    assert complete.status_code == 200, complete.text
    assert complete.json()["work_item_state"] == "callback_wait"

    callbacks = agent_client.get("/api/v1/agent/callbacks").json()
    assert len(callbacks) == 1
    assert callbacks[0]["work_item_id"] == lease["work_item_id"]
    assert callbacks[0]["reference"] == "Contact0"
    assert "+263" not in callbacks[0]["reference"]

    # Scheduling requires a future time, so backdate it directly to make it due,
    # then confirm it is immediately leasable again as the due callback.
    with SessionLocal() as db:
        item = db.get(WorkItem, uuid.UUID(lease["work_item_id"]))
        item.due_at = datetime.now(UTC) - timedelta(minutes=1)
        db.commit()

    release = agent_client.post("/api/v1/work/next", headers=csrf_headers(agent_client)).json()
    assert release["work_item_id"] == lease["work_item_id"]
    assert release["is_callback"] is True


def test_skip_requires_a_reason(manager_client, agent_client):
    agent_id = _get_agent_id(agent_client)
    _setup_campaign_with_agent(manager_client, agent_client, agent_id)
    lease = agent_client.post("/api/v1/work/next", headers=csrf_headers(agent_client)).json()

    resp = agent_client.post(
        f"/api/v1/work/{lease['work_item_id']}/skip",
        json={"lease_id": lease["lease_id"], "reason": "  "},
        headers=csrf_headers(agent_client),
    )
    assert resp.status_code == 400


def test_skip_past_threshold_routes_to_review(manager_client, agent_client):
    agent_id = _get_agent_id(agent_client)
    _setup_campaign_with_agent(manager_client, agent_client, agent_id)

    state = None
    for _ in range(3):  # settings.max_skips_before_review default is 3
        lease = agent_client.post("/api/v1/work/next", headers=csrf_headers(agent_client)).json()
        resp = agent_client.post(
            f"/api/v1/work/{lease['work_item_id']}/skip",
            json={"lease_id": lease["lease_id"], "reason": "not reachable"},
            headers=csrf_headers(agent_client),
        )
        assert resp.status_code == 200, resp.text
        state = resp.json()["state"]
    assert state == "review"


def test_lease_expiry_reclaim_returns_item_to_queue(manager_client, agent_client):
    agent_id = _get_agent_id(agent_client)
    _setup_campaign_with_agent(manager_client, agent_client, agent_id)
    lease = agent_client.post("/api/v1/work/next", headers=csrf_headers(agent_client)).json()

    with SessionLocal() as db:
        item = db.get(WorkItem, uuid.UUID(lease["work_item_id"]))
        item.lease_expires_at = datetime.now(UTC) - timedelta(minutes=1)
        db.commit()

        reclaimed = work_service.reclaim_expired_leases(db)
        db.commit()
        assert reclaimed == 1

        db.refresh(item)
        assert item.state == "queued"
        assert item.lease_owner_id is None


def test_renew_lease_extends_expiry_only_for_the_owner(manager_client, agent_client):
    agent_id = _get_agent_id(agent_client)
    _setup_campaign_with_agent(manager_client, agent_client, agent_id)
    lease = agent_client.post("/api/v1/work/next", headers=csrf_headers(agent_client)).json()

    resp = agent_client.post(
        f"/api/v1/work/{lease['work_item_id']}/lease/renew",
        json={"lease_id": lease["lease_id"]},
        headers=csrf_headers(agent_client),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["lease_id"] == lease["lease_id"]

    bad = agent_client.post(
        f"/api/v1/work/{lease['work_item_id']}/lease/renew",
        json={"lease_id": str(uuid.uuid4())},
        headers=csrf_headers(agent_client),
    )
    assert bad.status_code == 409
