"""Agent work-item leasing and completion (plan 11.9-11.13).

Leasing uses SELECT ... FOR UPDATE SKIP LOCKED so concurrent agents never receive the
same work item (invariant 5). Only one contact is ever returned per lease, and the
response never includes queue size or other contacts (invariant 3). Completion is
idempotent per (agent, idempotency_key) (invariant 6). An explicit DNC outcome
suppresses the contact across every campaign that currently has pending work for it
(invariant 7), not only the campaign the call happened in.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.config import get_settings
from app.models.base import utcnow
from app.models.campaign import Campaign, CampaignUserAssignment
from app.models.contact import CampaignContact, Contact
from app.models.work import WorkItem
from app.security.encryption import decrypt

_TERMINAL_STATES = ("completed", "suppressed", "cancelled")


class WorkItemError(Exception):
    pass


class LeaseConflict(WorkItemError):
    pass


@dataclass(frozen=True)
class LeaseResult:
    work_item_id: uuid.UUID
    lease_id: uuid.UUID
    lease_expires_at: datetime
    campaign_id: uuid.UUID
    campaign_name: str
    phone_e164: str
    contact_name: str | None
    approved_metadata: dict | None
    is_callback: bool


def _active_primary_assignment(
    db: Session, agent_id: uuid.UUID
) -> CampaignUserAssignment | None:
    now = utcnow()
    return db.scalar(
        select(CampaignUserAssignment).where(
            CampaignUserAssignment.user_id == agent_id,
            CampaignUserAssignment.campaign_role == "agent",
            CampaignUserAssignment.assignment_type == "primary",
            CampaignUserAssignment.status == "active",
            CampaignUserAssignment.effective_from <= now,
            or_(
                CampaignUserAssignment.effective_to.is_(None),
                CampaignUserAssignment.effective_to > now,
            ),
        )
    )


def lease_next(db: Session, agent_id: uuid.UUID) -> LeaseResult | None:
    now = utcnow()

    # Due callbacks the agent already owns take priority, in any of their currently
    # active campaigns. Callback ownership survives a campaign-assignment change for
    # MVP; explicit transfer handling is Phase 4.
    callback_stmt = (
        select(WorkItem)
        .join(CampaignContact, WorkItem.campaign_contact_id == CampaignContact.id)
        .join(Campaign, CampaignContact.campaign_id == Campaign.id)
        .where(
            WorkItem.state == "callback_wait",
            WorkItem.assigned_agent_id == agent_id,
            WorkItem.due_at <= now,
            Campaign.status == "active",
        )
        .order_by(WorkItem.due_at.asc())
        .limit(1)
        .with_for_update(of=WorkItem, skip_locked=True)
    )
    work_item = db.scalar(callback_stmt)
    is_callback = work_item is not None

    if work_item is None:
        assignment = _active_primary_assignment(db, agent_id)
        if assignment is None:
            return None

        campaign = db.get(Campaign, assignment.campaign_id)
        if campaign is None or campaign.status != "active":
            return None

        queue_stmt = (
            select(WorkItem)
            .join(CampaignContact, WorkItem.campaign_contact_id == CampaignContact.id)
            .where(CampaignContact.campaign_id == campaign.id, WorkItem.state == "queued")
            .order_by(WorkItem.priority.desc(), WorkItem.created_at.asc())
            .limit(1)
            .with_for_update(of=WorkItem, skip_locked=True)
        )
        work_item = db.scalar(queue_stmt)
        if work_item is None:
            return None

    settings = get_settings()
    lease_id = uuid.uuid4()
    work_item.state = "leased"
    work_item.lease_owner_id = agent_id
    work_item.lease_id = lease_id
    work_item.lease_expires_at = now + timedelta(minutes=settings.lease_duration_minutes)
    work_item.version += 1
    db.flush()

    campaign_contact = db.get(CampaignContact, work_item.campaign_contact_id)
    contact = db.get(Contact, campaign_contact.contact_id)
    campaign = db.get(Campaign, campaign_contact.campaign_id)

    record_audit(
        db, action="work.view", result="success", actor_user_id=agent_id,
        target_type="work_item", target_id=work_item.id,
    )

    return LeaseResult(
        work_item_id=work_item.id,
        lease_id=lease_id,
        lease_expires_at=work_item.lease_expires_at,
        campaign_id=campaign.id,
        campaign_name=campaign.name,
        phone_e164=decrypt(contact.phone_ciphertext),
        contact_name=campaign_contact.campaign_name_value,
        approved_metadata=campaign_contact.approved_metadata,
        is_callback=is_callback,
    )


def renew_lease(
    db: Session, work_item_id: uuid.UUID, agent_id: uuid.UUID, lease_id: uuid.UUID
) -> WorkItem:
    work_item = db.execute(
        select(WorkItem).where(WorkItem.id == work_item_id).with_for_update()
    ).scalar_one_or_none()
    now = utcnow()
    if (
        work_item is None
        or work_item.state != "leased"
        or work_item.lease_owner_id != agent_id
        or work_item.lease_id != lease_id
        or work_item.lease_expires_at is None
        or work_item.lease_expires_at <= now
    ):
        raise LeaseConflict("lease is not active or not owned by this agent")

    settings = get_settings()
    work_item.lease_expires_at = now + timedelta(minutes=settings.lease_duration_minutes)
    work_item.version += 1
    return work_item


def reclaim_expired_leases(db: Session) -> int:
    """Return leased items past their expiry to their pre-lease state.

    A callback (assigned_agent_id set) returns to callback_wait, still owned by the
    same agent and still due. A shared-pool item returns to queued.
    """
    now = utcnow()
    expired = db.scalars(
        select(WorkItem).where(WorkItem.state == "leased", WorkItem.lease_expires_at < now)
    )
    count = 0
    for item in expired:
        item.state = "callback_wait" if item.assigned_agent_id is not None else "queued"
        item.lease_owner_id = None
        item.lease_id = None
        item.lease_expires_at = None
        item.version += 1
        count += 1
    return count
