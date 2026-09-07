"""Integration tests for campaign-completion detection + retention countdown
(ADR-020, increment A). Real Postgres.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.campaigns import retention
from app.db import SessionLocal
from app.models.campaign import Campaign
from app.models.contact import CampaignContact, Contact
from app.models.identity import User
from app.notifications import service as notifications_service
from app.security.passwords import hash_password

pytestmark = pytest.mark.integration


def _make_campaign(
    *, completed: int, outstanding: int, status: str = "active"
) -> tuple[uuid.UUID, uuid.UUID]:
    """A campaign (org-scoped, so any org-wide manager can view it) with the
    given number of completed and still-outstanding contacts. Returns
    (campaign_id, creator_id)."""
    now = datetime.now(UTC)
    with SessionLocal() as db:
        creator = User(
            workforce_id=f"ret-creator-{uuid.uuid4().hex[:8]}",
            email=f"ret-creator-{uuid.uuid4().hex[:8]}@example.com",
            display_name="Retention Creator",
            password_hash=hash_password("not-used"),
        )
        db.add(creator)
        db.flush()
        campaign = Campaign(
            owning_scope_type="organization", owning_scope_id=None,
            external_code=f"ret-{uuid.uuid4().hex[:10]}", name=f"Retention {uuid.uuid4().hex[:6]}",
            default_region="ZW", timezone="Africa/Harare", status=status,
            created_by=creator.id, launched_at=now,
        )
        db.add(campaign)
        db.flush()
        for i in range(completed + outstanding):
            contact = Contact(phone_ciphertext="x", phone_fingerprint=uuid.uuid4().hex)
            db.add(contact)
            db.flush()
            db.add(
                CampaignContact(
                    campaign_id=campaign.id, contact_id=contact.id, status="queued",
                    imported_at=now, completed_at=(now if i < completed else None),
                )
            )
        db.commit()
        return campaign.id, creator.id


def test_fully_worked_campaign_is_detected_complete_with_countdown():
    campaign_id, creator_id = _make_campaign(completed=3, outstanding=0)

    with SessionLocal() as db:
        marked = retention.detect_completed_campaigns(db)
        db.commit()
    assert marked >= 1

    with SessionLocal() as db:
        campaign = db.get(Campaign, campaign_id)
        assert campaign is not None
        assert campaign.status == "completed"
        assert campaign.completed_at is not None
        assert campaign.retention_delete_after is not None
        delta = campaign.retention_delete_after - campaign.completed_at
        assert abs(delta - timedelta(days=retention.RETENTION_DAYS)) < timedelta(seconds=5)

        # The owner was notified to export before deletion.
        notes = [
            n for n in notifications_service.list_for_user(db, creator_id)
            if n.category == "campaign.retention" and n.related_entity_id == campaign_id
        ]
        assert len(notes) == 1


def test_outstanding_contact_blocks_completion():
    campaign_id, _ = _make_campaign(completed=2, outstanding=1)
    with SessionLocal() as db:
        assert retention.campaign_is_complete(db, db.get(Campaign, campaign_id)) is False
        retention.detect_completed_campaigns(db)
        db.commit()
    with SessionLocal() as db:
        assert db.get(Campaign, campaign_id).status == "active"


def test_empty_campaign_is_never_complete():
    campaign_id, _ = _make_campaign(completed=0, outstanding=0)
    with SessionLocal() as db:
        assert retention.campaign_is_complete(db, db.get(Campaign, campaign_id)) is False


def test_detection_is_idempotent():
    campaign_id, creator_id = _make_campaign(completed=2, outstanding=0)
    with SessionLocal() as db:
        assert retention.detect_completed_campaigns(db) >= 1
        db.commit()
    # A second pass sees the campaign already completed (only active ones are
    # scanned), so it neither re-marks nor re-notifies.
    with SessionLocal() as db:
        before = db.get(Campaign, campaign_id).completed_at
        second = retention.detect_completed_campaigns(db)
        db.commit()
    with SessionLocal() as db:
        assert db.get(Campaign, campaign_id).completed_at == before
        notes = [
            n for n in notifications_service.list_for_user(db, creator_id)
            if n.category == "campaign.retention" and n.related_entity_id == campaign_id
        ]
        assert len(notes) == 1
    assert isinstance(second, int)


def test_detail_page_shows_retention_countdown(manager_client):
    campaign_id, _ = _make_campaign(completed=1, outstanding=0)
    with SessionLocal() as db:
        retention.mark_campaign_completed(db, db.get(Campaign, campaign_id))
        db.commit()

    page = manager_client.get(f"/campaigns/{campaign_id}")
    assert page.status_code == 200
    assert "retention countdown" in page.text.lower()
    assert "auto-deleted" in page.text.lower()
