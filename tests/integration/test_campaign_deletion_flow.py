"""Integration tests for completed-campaign data deletion (ADR-020, increment
C): manual delete + auto-purge, and the evidence that must survive. Real Postgres.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.campaigns import retention
from app.db import SessionLocal
from app.models.audit import AuditEvent
from app.models.campaign import Campaign, CampaignDispositionDefinition
from app.models.contact import CampaignContact, Contact, SuppressionEntry
from app.models.identity import User
from app.models.work import CallAttempt, WorkItem
from app.security.encryption import encrypt
from app.security.passwords import hash_password

pytestmark = pytest.mark.integration


def _make_agent(db) -> User:
    agent = User(
        workforce_id=f"del-agent-{uuid.uuid4().hex[:8]}",
        email=f"del-agent-{uuid.uuid4().hex[:8]}@example.com",
        display_name="Del Agent", password_hash=hash_password("x"),
    )
    db.add(agent)
    db.flush()
    return agent


def _completed_campaign_with_work(
    *, phone: str, retention_after: datetime | None = None, extra_campaign_for_contact: bool = False
) -> uuid.UUID:
    """A completed campaign with one contact, a work item, and a call attempt.
    Optionally the same contact is also placed in a second (active) campaign, to
    prove a shared number is not deleted."""
    now = datetime.now(UTC)
    with SessionLocal() as db:
        agent = _make_agent(db)
        campaign = Campaign(
            owning_scope_type="organization", owning_scope_id=None,
            external_code=f"del-{uuid.uuid4().hex[:10]}", name="Del Campaign",
            default_region="ZW", timezone="Africa/Harare", status="active",
            created_by=agent.id, launched_at=now,
        )
        db.add(campaign)
        db.flush()
        disposition = CampaignDispositionDefinition(
            campaign_id=campaign.id, label="Done", stable_semantic_code="done",
            display_order=0, active=True,
        )
        db.add(disposition)
        contact = Contact(phone_ciphertext=encrypt(phone), phone_fingerprint=uuid.uuid4().hex)
        db.add(contact)
        db.flush()
        campaign_contact = CampaignContact(
            campaign_id=campaign.id, contact_id=contact.id, status="completed",
            imported_at=now, completed_at=now, completed_by_agent_id=agent.id,
            final_disposition_code="done",
        )
        db.add(campaign_contact)
        db.flush()
        work_item = WorkItem(campaign_contact_id=campaign_contact.id, state="completed")
        db.add(work_item)
        db.flush()
        db.add(
            CallAttempt(
                work_item_id=work_item.id, campaign_contact_id=campaign_contact.id,
                agent_id=agent.id, disposition_definition_id=disposition.id,
                semantic_outcome="done", idempotency_key=uuid.uuid4().hex,
                resulting_work_item_state="completed",
            )
        )
        # A DNC-suppression entry is retained evidence - deletion must not touch it.
        db.add(
            SuppressionEntry(
                phone_fingerprint=contact.phone_fingerprint, source="explicit_dnc",
                effective_at=now, status="active",
            )
        )
        if extra_campaign_for_contact:
            other = Campaign(
                owning_scope_type="organization", owning_scope_id=None,
                external_code=f"del-other-{uuid.uuid4().hex[:8]}", name="Other",
                default_region="ZW", timezone="Africa/Harare", status="active",
                created_by=agent.id, launched_at=now,
            )
            db.add(other)
            db.flush()
            db.add(
                CampaignContact(
                    campaign_id=other.id, contact_id=contact.id, status="queued",
                    imported_at=now,
                )
            )
        retention.mark_campaign_completed(db, campaign)
        if retention_after is not None:
            campaign.retention_delete_after = retention_after
        db.commit()
        return campaign.id


def _counts(db, campaign_id: uuid.UUID) -> dict:
    cc_ids = select(CampaignContact.id).where(CampaignContact.campaign_id == campaign_id)
    return {
        "campaign_contacts": db.scalar(
            select(func.count()).select_from(CampaignContact).where(
                CampaignContact.campaign_id == campaign_id
            )
        ),
        "work_items": db.scalar(
            select(func.count()).select_from(WorkItem).where(
                WorkItem.campaign_contact_id.in_(cc_ids)
            )
        ),
        "call_attempts": db.scalar(
            select(func.count()).select_from(CallAttempt).where(
                CallAttempt.campaign_contact_id.in_(cc_ids)
            )
        ),
    }


def test_manual_delete_removes_contact_data_but_keeps_evidence():
    campaign_id = _completed_campaign_with_work(phone="+263771111111")

    with SessionLocal() as db:
        audit_before = db.scalar(select(func.count()).select_from(AuditEvent))
        suppression_before = db.scalar(select(func.count()).select_from(SuppressionEntry))
        contacts_total_before = db.scalar(select(func.count()).select_from(Contact))
        campaign = db.get(Campaign, campaign_id)
        result = retention.delete_completed_campaign_data(db, campaign, actor_id=None)
        db.commit()
    assert result["campaign_contacts"] == 1
    assert result["contacts_deleted"] == 1

    with SessionLocal() as db:
        counts = _counts(db, campaign_id)
        assert counts == {"campaign_contacts": 0, "work_items": 0, "call_attempts": 0}
        # The one contact was orphaned, so removed.
        assert db.scalar(select(func.count()).select_from(Contact)) == contacts_total_before - 1
        # Evidence retained: suppression entries untouched, audit only grew.
        assert db.scalar(select(func.count()).select_from(SuppressionEntry)) == suppression_before
        assert db.scalar(select(func.count()).select_from(AuditEvent)) > audit_before
        assert (
            db.scalar(
                select(func.count()).select_from(AuditEvent).where(
                    AuditEvent.action == "campaign.delete_data",
                    AuditEvent.target_id == campaign_id,
                )
            )
            == 1
        )
        # The countdown is cleared once the data is gone.
        assert db.get(Campaign, campaign_id).retention_delete_after is None


def test_delete_keeps_a_number_shared_with_another_campaign():
    campaign_id = _completed_campaign_with_work(
        phone="+263772222222", extra_campaign_for_contact=True
    )
    with SessionLocal() as db:
        contacts_before = db.scalar(select(func.count()).select_from(Contact))
        campaign = db.get(Campaign, campaign_id)
        result = retention.delete_completed_campaign_data(db, campaign, actor_id=None)
        db.commit()
    # The number is still in another campaign, so it is not deleted.
    assert result["contacts_deleted"] == 0
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Contact)) == contacts_before
        assert _counts(db, campaign_id)["campaign_contacts"] == 0


def test_auto_purge_deletes_only_past_countdown_completed_campaigns():
    now = datetime.now(UTC)
    past = _completed_campaign_with_work(
        phone="+263773333333", retention_after=now - timedelta(days=1)
    )
    future = _completed_campaign_with_work(
        phone="+263774444444", retention_after=now + timedelta(days=30)
    )

    with SessionLocal() as db:
        purged = retention.purge_expired_campaign_data(db)
        db.commit()
    assert purged >= 1

    with SessionLocal() as db:
        assert _counts(db, past)["campaign_contacts"] == 0
        assert db.get(Campaign, past).retention_delete_after is None
        # The not-yet-due campaign is untouched.
        assert _counts(db, future)["campaign_contacts"] == 1
        assert db.get(Campaign, future).retention_delete_after is not None


def test_delete_refuses_a_non_completed_campaign():
    now = datetime.now(UTC)
    with SessionLocal() as db:
        agent = _make_agent(db)
        campaign = Campaign(
            owning_scope_type="organization", owning_scope_id=None,
            external_code=f"del-active-{uuid.uuid4().hex[:8]}", name="Active",
            default_region="ZW", timezone="Africa/Harare", status="active",
            created_by=agent.id, launched_at=now,
        )
        db.add(campaign)
        db.commit()
        campaign_id = campaign.id

    with SessionLocal() as db:
        campaign = db.get(Campaign, campaign_id)
        with pytest.raises(retention.CampaignNotDeletable):
            retention.delete_completed_campaign_data(db, campaign, actor_id=None)


def test_manual_delete_endpoint_authz_and_effect(manager_client):
    """The web delete endpoint: a manager (holds the export/delete capability)
    can purge a completed campaign's data through it; an agent cannot."""
    from fastapi.testclient import TestClient

    from app.main import app
    from tests.integration.conftest import TEST_PASSWORD, login, make_user_with_role

    campaign_id = _completed_campaign_with_work(phone="+263775555555")

    # An agent (no export/delete capability) is refused and nothing is deleted.
    agent_email = f"del-agent-web-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(agent_email, "agent")
    agent = TestClient(app, follow_redirects=False)
    login(agent, agent_email, TEST_PASSWORD)
    denied = agent.post(
        f"/campaigns/{campaign_id}/delete-data",
        data={"csrf_token": agent.cookies.get("cc_csrf")},
    )
    assert denied.status_code == 303
    assert denied.headers["location"].startswith("/campaigns?flash_error=")
    with SessionLocal() as db:
        assert _counts(db, campaign_id)["campaign_contacts"] == 1

    # The manager can, and the data is gone afterward.
    ok = manager_client.post(
        f"/campaigns/{campaign_id}/delete-data",
        data={"csrf_token": manager_client.cookies.get("cc_csrf")},
        follow_redirects=False,
    )
    assert ok.status_code == 303
    assert f"/campaigns/{campaign_id}?flash_success=" in ok.headers["location"]
    with SessionLocal() as db:
        assert _counts(db, campaign_id) == {
            "campaign_contacts": 0, "work_items": 0, "call_attempts": 0
        }
