"""Campaign-completion retention (ADR-020).

A campaign's contact database is complete once every number has reached a final
disposition with the calling agent recorded - which in this schema is exactly
`CampaignContact.completed_at IS NOT NULL`, set together with the disposition and
agent on every terminal path (normal completion, per-lease DNC suppression, and
the cross-campaign suppression sweep). On completion a 60-day retention countdown
begins; a Team Captain may export then delete the data before it ends, and the
system auto-deletes at the backstop (increments B and C).

This module owns detection and marking only; export and deletion live alongside.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import delete, exists, func, select, update
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.models.base import utcnow
from app.models.campaign import Campaign
from app.models.contact import CampaignContact, Contact
from app.models.work import CallAttempt, WorkItem
from app.notifications import service as notifications_service

# ADR-020 fixes the countdown at 60 days.
RETENTION_DAYS = 60


class CampaignNotDeletable(Exception):
    """The campaign is not in a state whose contact data may be deleted."""


def campaign_is_complete(db: Session, campaign: Campaign) -> bool:
    """True once the campaign has at least one contact and none is still
    outstanding (every CampaignContact has completed_at set). An empty campaign
    is never "complete" - there was no database to retain or delete."""
    if campaign.status != "active":
        return False
    total = db.scalar(
        select(func.count(CampaignContact.id)).where(
            CampaignContact.campaign_id == campaign.id
        )
    ) or 0
    if total == 0:
        return False
    outstanding = db.scalar(
        select(func.count(CampaignContact.id)).where(
            CampaignContact.campaign_id == campaign.id,
            CampaignContact.completed_at.is_(None),
        )
    ) or 0
    return outstanding == 0


def mark_campaign_completed(
    db: Session, campaign: Campaign, *, now: datetime | None = None
) -> None:
    """Move an active, fully-worked campaign into the completed/retention state:
    stamp completed_at, start the 60-day countdown, audit it, and tell the owner
    so they can export before the data is deleted. Callers commit."""
    now = now or utcnow()
    campaign.status = "completed"
    campaign.completed_at = now
    campaign.retention_delete_after = now + timedelta(days=RETENTION_DAYS)
    record_audit(
        db, action="campaign.completed", result="success", actor_user_id=None,
        target_type="campaign", target_id=campaign.id,
        event_metadata={
            "retention_delete_after": campaign.retention_delete_after.isoformat(),
            "retention_days": RETENTION_DAYS,
        },
    )
    if campaign.created_by is not None:
        notifications_service.notify(
            db,
            recipient_id=campaign.created_by,
            category="campaign.retention",
            title=f"Campaign “{campaign.name}” is complete",
            body=(
                f"Every number has a final disposition. A {RETENTION_DAYS}-day "
                "retention countdown has started - export the data before it is "
                "deleted."
            ),
            related_entity_type="campaign",
            related_entity_id=campaign.id,
        )


def detect_completed_campaigns(db: Session, *, now: datetime | None = None) -> int:
    """Mark every active campaign that has become fully worked as completed.
    Idempotent: only active campaigns are considered, so a campaign already moved
    to completed is never re-marked or re-notified. Returns how many were marked
    this pass. Callers commit."""
    now = now or utcnow()
    active_ids = list(
        db.scalars(select(Campaign.id).where(Campaign.status == "active"))
    )
    marked = 0
    for campaign_id in active_ids:
        campaign = db.get(Campaign, campaign_id)
        if campaign is None:
            continue
        if campaign_is_complete(db, campaign):
            mark_campaign_completed(db, campaign, now=now)
            marked += 1
    return marked


def delete_completed_campaign_data(
    db: Session, campaign: Campaign, *, actor_id: uuid.UUID | None, reason: str | None = None
) -> dict:
    """Remove a completed campaign's contact database - the call attempts, work
    items, campaign-contact rows, and any Contact left with no other campaign
    referencing it. Never touches audit events or DNC-suppression entries: those
    are the retained evidence ADR-020 keeps separately. Only a completed campaign
    can be deleted (an active one still holds live, workable data). Idempotent -
    re-running on an already-purged campaign deletes nothing. Callers commit."""
    if campaign.status != "completed":
        raise CampaignNotDeletable("only a completed campaign's data may be deleted")

    contact_ids = set(
        db.scalars(
            select(CampaignContact.contact_id).where(
                CampaignContact.campaign_id == campaign.id
            )
        )
    )
    campaign_contact_ids = select(CampaignContact.id).where(
        CampaignContact.campaign_id == campaign.id
    )

    # Break call attempts' self-references (a correction points at the attempt it
    # corrects) before deleting them, so no row blocks another's removal.
    db.execute(
        update(CallAttempt)
        .where(CallAttempt.campaign_contact_id.in_(campaign_contact_ids))
        .values(correction_of_attempt_id=None)
    )
    db.execute(
        delete(CallAttempt).where(CallAttempt.campaign_contact_id.in_(campaign_contact_ids))
    )
    db.execute(delete(WorkItem).where(WorkItem.campaign_contact_id.in_(campaign_contact_ids)))
    db.execute(delete(CampaignContact).where(CampaignContact.campaign_id == campaign.id))

    deleted_contacts = 0
    if contact_ids:
        # Only numbers now referenced by no campaign at all - a contact still in
        # another (e.g. active) campaign is left untouched. Resolved to an
        # explicit id set (rather than a bulk DELETE ... rowcount) so the count
        # is exact and typed.
        orphaned = list(
            db.scalars(
                select(Contact.id).where(
                    Contact.id.in_(contact_ids),
                    ~exists(
                        select(CampaignContact.id).where(
                            CampaignContact.contact_id == Contact.id
                        )
                    ),
                )
            )
        )
        if orphaned:
            db.execute(delete(Contact).where(Contact.id.in_(orphaned)))
        deleted_contacts = len(orphaned)

    # The data is gone, so there is nothing left to count down to; clearing this
    # also takes the campaign out of the auto-purge task's query.
    campaign.retention_delete_after = None
    record_audit(
        db, action="campaign.delete_data", result="success", actor_user_id=actor_id,
        target_type="campaign", target_id=campaign.id, reason_code=reason,
        event_metadata={
            "campaign_contacts": len(contact_ids), "contacts_deleted": deleted_contacts,
        },
    )
    return {"campaign_contacts": len(contact_ids), "contacts_deleted": deleted_contacts}


def purge_expired_campaign_data(db: Session, *, now: datetime | None = None) -> int:
    """Auto-delete the contact data of every completed campaign whose 60-day
    countdown has elapsed - the hard backstop for campaigns a Team Captain never
    manually cleared. Returns how many campaigns were purged. Callers commit."""
    now = now or utcnow()
    due_ids = list(
        db.scalars(
            select(Campaign.id).where(
                Campaign.status == "completed",
                Campaign.retention_delete_after.is_not(None),
                Campaign.retention_delete_after <= now,
            )
        )
    )
    purged = 0
    for campaign_id in due_ids:
        campaign = db.get(Campaign, campaign_id)
        if campaign is None:
            continue
        delete_completed_campaign_data(db, campaign, actor_id=None, reason="retention_auto_delete")
        purged += 1
    return purged


def retention_days_remaining(campaign: Campaign, *, now: datetime | None = None) -> int | None:
    """Whole days left before this campaign's data is auto-deleted, or None if it
    is not on the retention countdown. Never negative (a past-due campaign shows
    0 until the purge task removes it)."""
    if campaign.retention_delete_after is None:
        return None
    now = now or utcnow()
    remaining = campaign.retention_delete_after - now
    return max(0, remaining.days + (1 if remaining.seconds or remaining.microseconds else 0))
