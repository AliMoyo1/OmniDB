"""Integration tests for phase 4D Phase B: retry_wait eligibility and queue
precedence, the atomic same-agent immediate-redial transaction (Hung Up), and
the review service for items that reach the attempt ceiling.

Standard dispositions with next_action "retry_wait"/"immediate_redial" cannot
be created through the legacy free-form API (its DispositionCreateRequest
only allows complete/review/requeue - intentionally, plan 6.8) so every test
here installs the real version-1 manifest via
app.campaigns.service.install_standard_dispositions directly, the same way
Phase A's own tests do.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.campaigns import service as campaigns_service
from app.db import SessionLocal
from app.models.audit import AuditEvent
from app.models.campaign import Campaign, CampaignDispositionDefinition
from app.models.contact import CampaignContact, Contact, SuppressionEntry
from app.models.work import CallAttempt, WorkItem
from app.security.phone import protect
from app.work import service as work_service
from tests.integration.conftest import (
    assign_agent_to_campaign,
    csrf_headers,
    login,
    make_user_with_role,
    zw_numbers,
)

pytestmark = pytest.mark.integration

_DEFAULT_PROVENANCE = {
    "purpose": "Customer outreach",
    "data_source": "CRM export",
    "data_obtained_at": "2026-01-01",
    "lawful_basis_or_consent_reference": "consent-ref-123",
}


def _create_campaign(client: TestClient, headers: dict, **overrides) -> str:
    payload = {
        "external_code": f"rw-{uuid.uuid4().hex[:8]}",
        "name": f"Retry wait test {uuid.uuid4().hex[:6]}",
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


def _get_user_id(client: TestClient) -> uuid.UUID:
    me = client.get("/api/v1/auth/me")
    return uuid.UUID(me.json()["id"])


def _create_standard_campaign_with_agent(
    manager_client: TestClient,
    agent_client: TestClient,
    *,
    contact_count: int = 1,
    no_answer_retry_minutes: int | None = None,
    unavailable_retry_minutes: int | None = None,
) -> tuple[str, list[str]]:
    manager_id = _get_user_id(manager_client)
    agent_id = _get_user_id(agent_client)
    headers = csrf_headers(manager_client)
    campaign_id = _create_campaign(manager_client, headers)

    with SessionLocal() as db:
        campaign = db.get(Campaign, uuid.UUID(campaign_id))
        assert campaign is not None
        campaigns_service.install_standard_dispositions(
            db, campaign, actor_id=manager_id,
            no_answer_retry_minutes=no_answer_retry_minutes,
            unavailable_retry_minutes=unavailable_retry_minutes,
        )
        db.commit()

    numbers = zw_numbers(contact_count)
    names = [f"Contact{i}" for i in range(contact_count)]
    _commit_contacts(manager_client, headers, campaign_id, numbers, names)
    launch = manager_client.post(f"/api/v1/campaigns/{campaign_id}/launch", headers=headers)
    assert launch.status_code == 200, launch.text
    assign_agent_to_campaign(agent_id, uuid.UUID(campaign_id))
    return campaign_id, numbers


def _disposition_id(campaign_id: str, code: str) -> str:
    with SessionLocal() as db:
        row = db.scalar(
            select(CampaignDispositionDefinition).where(
                CampaignDispositionDefinition.campaign_id == uuid.UUID(campaign_id),
                CampaignDispositionDefinition.stable_semantic_code == code,
            )
        )
        assert row is not None, f"standard disposition '{code}' was not installed"
        return str(row.id)


def _work_items_for_campaign(campaign_id: str) -> list[WorkItem]:
    with SessionLocal() as db:
        rows = db.scalars(
            select(WorkItem)
            .join(CampaignContact, WorkItem.campaign_contact_id == CampaignContact.id)
            .where(CampaignContact.campaign_id == uuid.UUID(campaign_id))
            .order_by(WorkItem.created_at.asc())
        ).all()
        db.expunge_all()
        return list(rows)


def _lease(agent_client: TestClient) -> dict:
    resp = agent_client.post("/api/v1/work/next", headers=csrf_headers(agent_client))
    assert resp.status_code == 200, resp.text
    return resp.json()


def _complete(
    agent_client: TestClient, work_item_id: str, lease_id: str, disposition_id: str, **extra
):
    payload = {
        "lease_id": lease_id, "disposition_id": disposition_id,
        "idempotency_key": str(uuid.uuid4()), **extra,
    }
    return agent_client.post(
        f"/api/v1/work/{work_item_id}/complete", json=payload, headers=csrf_headers(agent_client)
    )


# --- retry_wait eligibility and queue precedence -----------------------------


def test_no_answer_enters_retry_wait_and_is_not_immediately_leaseable(
    manager_client, agent_client
):
    campaign_id, _ = _create_standard_campaign_with_agent(manager_client, agent_client)
    no_answer_id = _disposition_id(campaign_id, "no_answer")

    lease = _lease(agent_client)
    complete = _complete(agent_client, lease["work_item_id"], lease["lease_id"], no_answer_id)
    assert complete.status_code == 200, complete.text
    body = complete.json()
    assert body["work_item_state"] == "retry_wait"
    assert body["next_step"] == "retry_scheduled"
    assert body["retry_at"] is not None
    retry_at = datetime.fromisoformat(body["retry_at"])
    assert timedelta(minutes=55) < (retry_at - datetime.now(UTC)) < timedelta(minutes=65)

    again = agent_client.post("/api/v1/work/next", headers=csrf_headers(agent_client))
    assert again.status_code == 204


def test_no_answer_becomes_leaseable_once_due_time_passes(manager_client, agent_client):
    campaign_id, _ = _create_standard_campaign_with_agent(manager_client, agent_client)
    no_answer_id = _disposition_id(campaign_id, "no_answer")

    lease = _lease(agent_client)
    complete = _complete(agent_client, lease["work_item_id"], lease["lease_id"], no_answer_id)
    assert complete.status_code == 200, complete.text

    with SessionLocal() as db:
        item = db.get(WorkItem, uuid.UUID(lease["work_item_id"]))
        assert item is not None
        item.due_at = datetime.now(UTC) - timedelta(minutes=1)
        db.commit()

    release = _lease(agent_client)
    assert release["work_item_id"] == lease["work_item_id"]
    assert release["lease_reason"] == "delayed_retry"
    assert release["lease_id"] != lease["lease_id"]


def test_unavailable_uses_its_own_configured_retry_delay(manager_client, agent_client):
    campaign_id, _ = _create_standard_campaign_with_agent(
        manager_client, agent_client, unavailable_retry_minutes=10
    )
    unavailable_id = _disposition_id(campaign_id, "unavailable")

    lease = _lease(agent_client)
    complete = _complete(agent_client, lease["work_item_id"], lease["lease_id"], unavailable_id)
    assert complete.status_code == 200, complete.text
    retry_at = datetime.fromisoformat(complete.json()["retry_at"])
    assert timedelta(minutes=8) < (retry_at - datetime.now(UTC)) < timedelta(minutes=12)


def test_due_callback_outranks_due_retry(manager_client, agent_client):
    campaign_id, _ = _create_standard_campaign_with_agent(
        manager_client, agent_client, contact_count=2
    )
    agent_id = _get_user_id(agent_client)
    items = _work_items_for_campaign(campaign_id)
    assert len(items) == 2
    now = datetime.now(UTC)
    with SessionLocal() as db:
        callback_item = db.get(WorkItem, items[0].id)
        callback_item.state = "callback_wait"
        callback_item.assigned_agent_id = agent_id
        callback_item.due_at = now - timedelta(minutes=1)
        retry_item = db.get(WorkItem, items[1].id)
        retry_item.state = "retry_wait"
        retry_item.due_at = now - timedelta(minutes=1)
        db.commit()

    lease = _lease(agent_client)
    assert lease["work_item_id"] == str(items[0].id)
    assert lease["is_callback"] is True
    assert lease["lease_reason"] == "scheduled_callback"


def test_due_retry_outranks_fresh_queue_item(manager_client, agent_client):
    campaign_id, _ = _create_standard_campaign_with_agent(
        manager_client, agent_client, contact_count=2
    )
    items = _work_items_for_campaign(campaign_id)
    assert len(items) == 2
    with SessionLocal() as db:
        retry_item = db.get(WorkItem, items[0].id)
        retry_item.state = "retry_wait"
        retry_item.due_at = datetime.now(UTC) - timedelta(minutes=1)
        # items[1] stays "queued" - fresh, never leased.
        db.commit()

    lease = _lease(agent_client)
    assert lease["work_item_id"] == str(items[0].id)
    assert lease["lease_reason"] == "delayed_retry"


def test_due_retries_are_ordered_oldest_due_at_first(manager_client, agent_client):
    campaign_id, _ = _create_standard_campaign_with_agent(
        manager_client, agent_client, contact_count=2
    )
    items = _work_items_for_campaign(campaign_id)
    now = datetime.now(UTC)
    with SessionLocal() as db:
        older = db.get(WorkItem, items[0].id)
        older.state = "retry_wait"
        older.due_at = now - timedelta(minutes=10)
        newer = db.get(WorkItem, items[1].id)
        newer.state = "retry_wait"
        newer.due_at = now - timedelta(minutes=1)
        db.commit()

    lease = _lease(agent_client)
    assert lease["work_item_id"] == str(items[0].id)


def test_no_answer_reaches_review_exactly_at_max_attempts(manager_client, agent_client):
    campaign_id, _ = _create_standard_campaign_with_agent(manager_client, agent_client)
    no_answer_id = _disposition_id(campaign_id, "no_answer")

    lease1 = _lease(agent_client)
    with SessionLocal() as db:
        item = db.get(WorkItem, uuid.UUID(lease1["work_item_id"]))
        item.max_attempts = 2
        db.commit()

    first = _complete(agent_client, lease1["work_item_id"], lease1["lease_id"], no_answer_id)
    assert first.json()["work_item_state"] == "retry_wait"

    with SessionLocal() as db:
        item = db.get(WorkItem, uuid.UUID(lease1["work_item_id"]))
        item.due_at = datetime.now(UTC) - timedelta(minutes=1)
        db.commit()

    lease2 = _lease(agent_client)
    assert lease2["work_item_id"] == lease1["work_item_id"]
    second = _complete(agent_client, lease2["work_item_id"], lease2["lease_id"], no_answer_id)
    body = second.json()
    assert body["work_item_state"] == "review"
    assert body["next_step"] == "review"
    assert body["retry_at"] is None

    with SessionLocal() as db:
        attempts = db.scalars(
            select(CallAttempt).where(
                CallAttempt.work_item_id == uuid.UUID(lease1["work_item_id"])
            )
        ).all()
        assert len(attempts) == 2


# --- immediate same-agent redial (Hung Up) ------------------------------------


def test_hung_up_writes_one_attempt_and_renews_the_lease_for_the_same_agent(
    manager_client, agent_client
):
    campaign_id, _ = _create_standard_campaign_with_agent(manager_client, agent_client)
    hung_up_id = _disposition_id(campaign_id, "hung_up")

    lease = _lease(agent_client)
    complete = _complete(agent_client, lease["work_item_id"], lease["lease_id"], hung_up_id)
    assert complete.status_code == 200, complete.text
    body = complete.json()
    assert body["work_item_state"] == "leased"
    assert body["next_step"] == "redial_ready"
    assert body["redial_lease_id"] is not None
    assert body["redial_lease_id"] != lease["lease_id"]
    assert body["redial_lease_expires_at"] is not None

    with SessionLocal() as db:
        item = db.get(WorkItem, uuid.UUID(lease["work_item_id"]))
        assert item is not None
        assert item.state == "leased"
        assert item.lease_owner_id == _get_user_id(agent_client)
        assert str(item.lease_id) == body["redial_lease_id"]
        assert item.attempt_count == 1
        attempts = db.scalars(
            select(CallAttempt).where(CallAttempt.work_item_id == item.id)
        ).all()
        assert len(attempts) == 1


def test_old_lease_id_is_rejected_after_hung_up_renewal(manager_client, agent_client):
    campaign_id, _ = _create_standard_campaign_with_agent(manager_client, agent_client)
    hung_up_id = _disposition_id(campaign_id, "hung_up")
    connected_id = _disposition_id(campaign_id, "connected")

    lease = _lease(agent_client)
    _complete(agent_client, lease["work_item_id"], lease["lease_id"], hung_up_id)

    replay_with_old_lease = _complete(
        agent_client, lease["work_item_id"], lease["lease_id"], connected_id
    )
    assert replay_with_old_lease.status_code == 409
    assert replay_with_old_lease.json()["detail"]["code"] == "lease_conflict"


def test_second_agent_cannot_acquire_contact_during_renewed_redial_hold(
    manager_client, agent_client
):
    from app.authz.capabilities import ROLE_AGENT
    from app.main import app

    campaign_id, _ = _create_standard_campaign_with_agent(manager_client, agent_client)
    hung_up_id = _disposition_id(campaign_id, "hung_up")

    lease = _lease(agent_client)
    _complete(agent_client, lease["work_item_id"], lease["lease_id"], hung_up_id)

    second_email = f"agent2-{uuid.uuid4().hex[:8]}@example.com"
    second_agent_id = make_user_with_role(second_email, ROLE_AGENT)
    assign_agent_to_campaign(second_agent_id, uuid.UUID(campaign_id))
    with TestClient(app) as second_agent_client:
        login(second_agent_client, second_email)
        blocked = second_agent_client.post(
            "/api/v1/work/next", headers=csrf_headers(second_agent_client)
        )
        assert blocked.status_code == 204


def test_hung_up_idempotent_replay_returns_original_attempt_and_renewed_lease(
    manager_client, agent_client
):
    campaign_id, _ = _create_standard_campaign_with_agent(manager_client, agent_client)
    hung_up_id = _disposition_id(campaign_id, "hung_up")
    idem_key = str(uuid.uuid4())

    lease = _lease(agent_client)
    first = _complete(
        agent_client, lease["work_item_id"], lease["lease_id"], hung_up_id,
        idempotency_key=idem_key,
    )
    assert first.status_code == 200, first.text

    # Replays with the ORIGINAL (now-stale) lease_id - idempotency is checked
    # before lease validation, so this must still succeed and return the same
    # renewed-lease result rather than erroring or minting a second lease.
    replay = _complete(
        agent_client, lease["work_item_id"], lease["lease_id"], hung_up_id,
        idempotency_key=idem_key,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json() == first.json()

    with SessionLocal() as db:
        attempts = db.scalars(
            select(CallAttempt).where(
                CallAttempt.work_item_id == uuid.UUID(lease["work_item_id"])
            )
        ).all()
        assert len(attempts) == 1


def test_different_idempotency_key_after_redial_records_a_second_real_attempt(
    manager_client, agent_client
):
    campaign_id, _ = _create_standard_campaign_with_agent(manager_client, agent_client)
    hung_up_id = _disposition_id(campaign_id, "hung_up")

    lease = _lease(agent_client)
    first = _complete(agent_client, lease["work_item_id"], lease["lease_id"], hung_up_id)
    renewed_lease_id = first.json()["redial_lease_id"]

    second = _complete(agent_client, lease["work_item_id"], renewed_lease_id, hung_up_id)
    assert second.status_code == 200, second.text
    assert second.json()["redial_lease_id"] != renewed_lease_id

    with SessionLocal() as db:
        attempts = db.scalars(
            select(CallAttempt).where(
                CallAttempt.work_item_id == uuid.UUID(lease["work_item_id"])
            )
        ).all()
        assert len(attempts) == 2


def test_hung_up_at_attempt_ceiling_issues_no_new_lease_and_enters_review(
    manager_client, agent_client
):
    campaign_id, _ = _create_standard_campaign_with_agent(manager_client, agent_client)
    hung_up_id = _disposition_id(campaign_id, "hung_up")

    lease = _lease(agent_client)
    with SessionLocal() as db:
        item = db.get(WorkItem, uuid.UUID(lease["work_item_id"]))
        item.max_attempts = 1
        db.commit()

    complete = _complete(agent_client, lease["work_item_id"], lease["lease_id"], hung_up_id)
    body = complete.json()
    assert body["work_item_state"] == "review"
    assert body["next_step"] == "review"
    assert body["redial_lease_id"] is None

    with SessionLocal() as db:
        item = db.get(WorkItem, uuid.UUID(lease["work_item_id"]))
        assert item.state == "review"
        assert item.lease_owner_id is None
        assert item.lease_id is None


def test_renewed_redial_lease_is_reclaimed_like_any_other_expired_lease(
    manager_client, agent_client
):
    campaign_id, _ = _create_standard_campaign_with_agent(manager_client, agent_client)
    hung_up_id = _disposition_id(campaign_id, "hung_up")

    lease = _lease(agent_client)
    _complete(agent_client, lease["work_item_id"], lease["lease_id"], hung_up_id)

    with SessionLocal() as db:
        item = db.get(WorkItem, uuid.UUID(lease["work_item_id"]))
        item.lease_expires_at = datetime.now(UTC) - timedelta(minutes=1)
        db.commit()

        reclaimed = work_service.reclaim_expired_leases(db)
        db.commit()
        assert reclaimed == 1

        item = db.get(WorkItem, uuid.UUID(lease["work_item_id"]))
        assert item.state == "queued"
        assert item.lease_owner_id is None


def test_renewed_redial_lease_is_released_when_the_agent_is_disabled(
    manager_client, agent_client
):
    campaign_id, _ = _create_standard_campaign_with_agent(manager_client, agent_client)
    hung_up_id = _disposition_id(campaign_id, "hung_up")
    agent_id = _get_user_id(agent_client)

    lease = _lease(agent_client)
    _complete(agent_client, lease["work_item_id"], lease["lease_id"], hung_up_id)

    with SessionLocal() as db:
        released = work_service.reclaim_leases_for_user(db, agent_id)
        db.commit()
        assert released == 1

        item = db.get(WorkItem, uuid.UUID(lease["work_item_id"]))
        assert item.state == "queued"
        assert item.lease_owner_id is None


def test_dnc_elsewhere_suppresses_a_contact_currently_held_under_a_renewed_redial_lease(
    manager_client, agent_client
):
    """The generic cross-campaign suppression sweep (invariant 7) already
    catches every non-terminal state, "leased" included - this proves a
    renewed immediate-redial hold is not a gap in it, without needing a real
    second completion to trigger the sweep."""
    campaign_id, numbers = _create_standard_campaign_with_agent(manager_client, agent_client)
    hung_up_id = _disposition_id(campaign_id, "hung_up")
    manager_id = _get_user_id(manager_client)

    lease = _lease(agent_client)
    complete = _complete(agent_client, lease["work_item_id"], lease["lease_id"], hung_up_id)
    assert complete.status_code == 200, complete.text
    assert complete.json()["work_item_state"] == "leased"

    with SessionLocal() as db:
        item = db.get(WorkItem, uuid.UUID(lease["work_item_id"]))
        assert item is not None
        campaign_contact = db.get(CampaignContact, item.campaign_contact_id)
        contact = db.get(Contact, campaign_contact.contact_id)
        work_service._suppress_contact_everywhere(  # noqa: SLF001 - exercising the real sweep
            db, contact, source="explicit_contact_request", created_by=manager_id
        )
        db.commit()

    with SessionLocal() as db:
        item = db.get(WorkItem, uuid.UUID(lease["work_item_id"]))
        assert item is not None
        assert item.state == "suppressed"
        assert item.lease_owner_id is None
        suppression = db.scalar(
            select(SuppressionEntry).where(
                SuppressionEntry.phone_fingerprint == protect(numbers[0], "ZW").fingerprint,
                SuppressionEntry.status == "active",
            )
        )
        assert suppression is not None


def test_source_lease_reason_snapshot_matches_how_the_item_was_actually_leased(
    manager_client, agent_client
):
    campaign_id, _ = _create_standard_campaign_with_agent(manager_client, agent_client)
    no_answer_id = _disposition_id(campaign_id, "no_answer")
    hung_up_id = _disposition_id(campaign_id, "hung_up")

    lease1 = _lease(agent_client)
    assert lease1["lease_reason"] == "normal"
    first = _complete(agent_client, lease1["work_item_id"], lease1["lease_id"], no_answer_id)
    assert first.status_code == 200, first.text

    with SessionLocal() as db:
        item = db.get(WorkItem, uuid.UUID(lease1["work_item_id"]))
        item.due_at = datetime.now(UTC) - timedelta(minutes=1)
        db.commit()

    lease2 = _lease(agent_client)
    assert lease2["lease_reason"] == "delayed_retry"
    second = _complete(agent_client, lease2["work_item_id"], lease2["lease_id"], hung_up_id)
    assert second.status_code == 200, second.text

    with SessionLocal() as db:
        attempts = db.scalars(
            select(CallAttempt)
            .where(CallAttempt.work_item_id == uuid.UUID(lease1["work_item_id"]))
            .order_by(CallAttempt.created_at.asc())
        ).all()
        assert [a.source_lease_reason for a in attempts] == ["normal", "delayed_retry"]


# --- review service ------------------------------------------------------------


def _push_to_review(manager_client, agent_client, *, campaign_id: str, no_answer_id: str) -> str:
    lease = _lease(agent_client)
    with SessionLocal() as db:
        item = db.get(WorkItem, uuid.UUID(lease["work_item_id"]))
        item.max_attempts = 1
        db.commit()
    complete = _complete(agent_client, lease["work_item_id"], lease["lease_id"], no_answer_id)
    assert complete.json()["work_item_state"] == "review"
    return lease["work_item_id"]


def test_list_review_items_masks_reference_and_scopes_to_campaign(manager_client, agent_client):
    campaign_id, _ = _create_standard_campaign_with_agent(
        manager_client, agent_client, contact_count=1
    )
    no_answer_id = _disposition_id(campaign_id, "no_answer")
    work_item_id = _push_to_review(
        manager_client, agent_client, campaign_id=campaign_id, no_answer_id=no_answer_id
    )

    # A second campaign, deliberately not agent-assigned (an agent may hold
    # only one active primary assignment at a time) - only its scoping is
    # under test here, not its work queue.
    headers = csrf_headers(manager_client)
    other_campaign_id = _create_campaign(manager_client, headers)
    with SessionLocal() as db:
        other_campaign = db.get(Campaign, uuid.UUID(other_campaign_id))
        campaigns_service.install_standard_dispositions(
            db, other_campaign, actor_id=_get_user_id(manager_client)
        )
        db.commit()

    with SessionLocal() as db:
        items = work_service.list_review_items(db, uuid.UUID(campaign_id))
        assert [str(i.work_item_id) for i in items] == [work_item_id]
        assert items[0].last_standard_outcome == "no_answer"
        assert items[0].attempt_count == 1
        assert items[0].max_attempts == 1
        assert "+" not in items[0].reference  # never a raw phone number

        other_items = work_service.list_review_items(db, uuid.UUID(other_campaign_id))
        assert other_items == []


def test_reschedule_review_item_returns_to_retry_wait_and_clears_agent_ownership(
    manager_client, agent_client
):
    campaign_id, _ = _create_standard_campaign_with_agent(manager_client, agent_client)
    no_answer_id = _disposition_id(campaign_id, "no_answer")
    work_item_id = _push_to_review(
        manager_client, agent_client, campaign_id=campaign_id, no_answer_id=no_answer_id
    )
    manager_id = _get_user_id(manager_client)
    retry_at = datetime.now(UTC) + timedelta(hours=2)

    with SessionLocal() as db:
        item = work_service.reschedule_review_item(
            db, uuid.UUID(work_item_id), actor_id=manager_id, retry_at=retry_at,
            reason="Customer asked to be called back after hours",
        )
        db.commit()
        assert item.state == "retry_wait"
        assert item.assigned_agent_id is None

        event = db.scalar(
            select(AuditEvent).where(AuditEvent.action == "work.review.retry")
            .order_by(AuditEvent.occurred_at.desc())
        )
        assert event is not None
        assert event.actor_user_id == manager_id


def test_reschedule_review_item_rejects_missing_reason(manager_client, agent_client):
    campaign_id, _ = _create_standard_campaign_with_agent(manager_client, agent_client)
    no_answer_id = _disposition_id(campaign_id, "no_answer")
    work_item_id = _push_to_review(
        manager_client, agent_client, campaign_id=campaign_id, no_answer_id=no_answer_id
    )
    with SessionLocal() as db:
        with pytest.raises(work_service.MissingRequiredField):
            work_service.reschedule_review_item(
                db, uuid.UUID(work_item_id), actor_id=_get_user_id(manager_client),
                retry_at=datetime.now(UTC) + timedelta(hours=1), reason="   ",
            )


def test_reschedule_review_item_rejects_a_past_time(manager_client, agent_client):
    campaign_id, _ = _create_standard_campaign_with_agent(manager_client, agent_client)
    no_answer_id = _disposition_id(campaign_id, "no_answer")
    work_item_id = _push_to_review(
        manager_client, agent_client, campaign_id=campaign_id, no_answer_id=no_answer_id
    )
    with SessionLocal() as db:
        with pytest.raises(work_service.MissingRequiredField):
            work_service.reschedule_review_item(
                db, uuid.UUID(work_item_id), actor_id=_get_user_id(manager_client),
                retry_at=datetime.now(UTC) - timedelta(minutes=1), reason="typo fix",
            )


def test_increase_review_item_attempts_raises_ceiling_and_is_immediately_eligible(
    manager_client, agent_client
):
    campaign_id, _ = _create_standard_campaign_with_agent(manager_client, agent_client)
    no_answer_id = _disposition_id(campaign_id, "no_answer")
    work_item_id = _push_to_review(
        manager_client, agent_client, campaign_id=campaign_id, no_answer_id=no_answer_id
    )
    with SessionLocal() as db:
        item = work_service.increase_review_item_attempts(
            db, uuid.UUID(work_item_id), actor_id=_get_user_id(manager_client),
            new_max_attempts=3, reason="Known good number, worth another try",
        )
        db.commit()
        assert item.state == "retry_wait"
        assert item.max_attempts == 3
        assert item.due_at is not None and item.due_at <= datetime.now(UTC)

    release = _lease(agent_client)
    assert release["work_item_id"] == work_item_id
    assert release["lease_reason"] == "delayed_retry"


def test_increase_review_item_attempts_rejects_a_max_not_greater_than_attempt_count(
    manager_client, agent_client
):
    campaign_id, _ = _create_standard_campaign_with_agent(manager_client, agent_client)
    no_answer_id = _disposition_id(campaign_id, "no_answer")
    work_item_id = _push_to_review(
        manager_client, agent_client, campaign_id=campaign_id, no_answer_id=no_answer_id
    )
    with SessionLocal() as db:
        with pytest.raises(work_service.MissingRequiredField):
            work_service.increase_review_item_attempts(
                db, uuid.UUID(work_item_id), actor_id=_get_user_id(manager_client),
                new_max_attempts=1, reason="not enough of a bump",
            )


def test_close_review_item_uses_last_attempt_outcome_not_the_reviewer(
    manager_client, agent_client
):
    campaign_id, _ = _create_standard_campaign_with_agent(manager_client, agent_client)
    no_answer_id = _disposition_id(campaign_id, "no_answer")
    agent_id = _get_user_id(agent_client)
    work_item_id = _push_to_review(
        manager_client, agent_client, campaign_id=campaign_id, no_answer_id=no_answer_id
    )
    manager_id = _get_user_id(manager_client)

    with SessionLocal() as db:
        item = work_service.close_review_item(
            db, uuid.UUID(work_item_id), actor_id=manager_id,
            reason="Exhausted retries, closing per policy",
        )
        db.commit()
        assert item.state == "completed"

        campaign_contact = db.get(CampaignContact, item.campaign_contact_id)
        assert campaign_contact.status == "completed"
        assert campaign_contact.final_disposition_code == "no_answer"
        # The last attempting AGENT is recorded as the caller, never the
        # reviewing manager (plan 6.6).
        assert campaign_contact.completed_by_agent_id == agent_id

        event = db.scalar(
            select(AuditEvent).where(AuditEvent.action == "work.review.close")
            .order_by(AuditEvent.occurred_at.desc())
        )
        assert event is not None
        assert event.actor_user_id == manager_id


def test_close_review_item_rejects_missing_reason(manager_client, agent_client):
    campaign_id, _ = _create_standard_campaign_with_agent(manager_client, agent_client)
    no_answer_id = _disposition_id(campaign_id, "no_answer")
    work_item_id = _push_to_review(
        manager_client, agent_client, campaign_id=campaign_id, no_answer_id=no_answer_id
    )
    with SessionLocal() as db:
        with pytest.raises(work_service.MissingRequiredField):
            work_service.close_review_item(
                db, uuid.UUID(work_item_id), actor_id=_get_user_id(manager_client), reason=""
            )


def test_review_actions_reject_a_work_item_that_is_not_in_review(manager_client, agent_client):
    campaign_id, _ = _create_standard_campaign_with_agent(manager_client, agent_client)
    lease = _lease(agent_client)  # leaves the item "leased", not "review"
    with SessionLocal() as db:
        with pytest.raises(work_service.ReviewStateError):
            work_service.close_review_item(
                db, uuid.UUID(lease["work_item_id"]), actor_id=_get_user_id(manager_client),
                reason="should not apply",
            )
