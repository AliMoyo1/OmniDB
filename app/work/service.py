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
from app.models.campaign import Campaign, CampaignDispositionDefinition, CampaignUserAssignment
from app.models.contact import CampaignContact, Contact, SuppressionEntry
from app.models.work import CallAttempt, WorkItem
from app.security.encryption import decrypt, encrypt

_TERMINAL_STATES = ("completed", "suppressed", "cancelled")


class WorkItemError(Exception):
    pass


class LeaseConflict(WorkItemError):
    pass


class DispositionMismatch(WorkItemError):
    pass


class MissingRequiredField(WorkItemError):
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


def _load_leased_item(
    db: Session, work_item_id: uuid.UUID, agent_id: uuid.UUID, lease_id: uuid.UUID
) -> WorkItem:
    work_item = db.execute(
        select(WorkItem).where(WorkItem.id == work_item_id).with_for_update()
    ).scalar_one_or_none()
    if (
        work_item is None
        or work_item.state != "leased"
        or work_item.lease_owner_id != agent_id
        or work_item.lease_id != lease_id
    ):
        raise LeaseConflict("lease is not active or not owned by this agent")
    if work_item.lease_expires_at is not None and work_item.lease_expires_at <= utcnow():
        raise LeaseConflict("lease has expired")
    return work_item


def _existing_attempt(
    db: Session, agent_id: uuid.UUID, idempotency_key: str
) -> CallAttempt | None:
    return db.scalar(
        select(CallAttempt).where(
            CallAttempt.agent_id == agent_id, CallAttempt.idempotency_key == idempotency_key
        )
    )


def _suppress_contact_everywhere(
    db: Session, contact: Contact, *, source: str, created_by: uuid.UUID
) -> None:
    """Create or reuse an active suppression and close every pending work item for
    this contact across all campaigns, including the one currently being completed
    (invariant 7 applied globally, not only to the campaign the call happened in)."""
    existing = db.scalar(
        select(SuppressionEntry).where(
            SuppressionEntry.phone_fingerprint == contact.phone_fingerprint,
            SuppressionEntry.status == "active",
        )
    )
    if existing is None:
        db.add(
            SuppressionEntry(
                phone_fingerprint=contact.phone_fingerprint,
                protected_phone_value=contact.phone_ciphertext,
                source=source,
                effective_at=utcnow(),
                status="active",
                created_by=created_by,
            )
        )

    pending_items = db.scalars(
        select(WorkItem)
        .join(CampaignContact, WorkItem.campaign_contact_id == CampaignContact.id)
        .where(
            CampaignContact.contact_id == contact.id,
            WorkItem.state.notin_(_TERMINAL_STATES),
        )
        .with_for_update(of=WorkItem)
    )
    now = utcnow()
    for item in pending_items:
        item.state = "suppressed"
        item.lease_owner_id = None
        item.lease_id = None
        item.lease_expires_at = None
        item.completed_at = now
        item.version += 1
        campaign_contact = db.get(CampaignContact, item.campaign_contact_id)
        campaign_contact.status = "suppressed"


@dataclass(frozen=True)
class CompletionResult:
    attempt_id: uuid.UUID
    work_item_state: str
    semantic_outcome: str
    callback_at: datetime | None


def complete_work_item(
    db: Session,
    *,
    work_item_id: uuid.UUID,
    agent_id: uuid.UUID,
    lease_id: uuid.UUID,
    disposition_id: uuid.UUID,
    notes: str | None,
    callback_at: datetime | None,
    self_reported_duration_seconds: int | None,
    idempotency_key: str,
) -> CompletionResult:
    existing = _existing_attempt(db, agent_id, idempotency_key)
    if existing is not None:
        prior_item = db.get(WorkItem, existing.work_item_id)
        return CompletionResult(
            attempt_id=existing.id,
            work_item_state=prior_item.state,
            semantic_outcome=existing.semantic_outcome,
            callback_at=existing.callback_at,
        )

    work_item = _load_leased_item(db, work_item_id, agent_id, lease_id)
    campaign_contact = db.get(CampaignContact, work_item.campaign_contact_id)

    disposition = db.get(CampaignDispositionDefinition, disposition_id)
    if disposition is None or disposition.campaign_id != campaign_contact.campaign_id:
        raise DispositionMismatch("disposition does not belong to this work item's campaign")
    if not disposition.active:
        raise DispositionMismatch("disposition is not active")
    if disposition.requires_notes and not notes:
        raise MissingRequiredField("notes are required for this disposition")
    if disposition.requires_callback_time and callback_at is None:
        raise MissingRequiredField("a callback time is required for this disposition")

    contact = db.get(Contact, campaign_contact.contact_id)
    notes_ciphertext = encrypt(notes) if notes else None

    attempt = CallAttempt(
        work_item_id=work_item.id,
        campaign_contact_id=campaign_contact.id,
        agent_id=agent_id,
        disposition_definition_id=disposition.id,
        semantic_outcome=disposition.stable_semantic_code,
        notes_ciphertext=notes_ciphertext,
        self_reported_duration_seconds=self_reported_duration_seconds,
        explicit_dnc_requested=disposition.causes_dnc,
        callback_at=callback_at if disposition.requires_callback_time else None,
        idempotency_key=idempotency_key,
    )
    db.add(attempt)
    db.flush()

    work_item.attempt_count += 1

    if disposition.causes_dnc:
        _suppress_contact_everywhere(
            db, contact, source="explicit_contact_request", created_by=agent_id
        )
        record_audit(
            db, action="work.dnc", result="success", actor_user_id=agent_id,
            target_type="contact", target_id=contact.id,
        )
    elif disposition.requires_callback_time:
        work_item.state = "callback_wait"
        work_item.due_at = callback_at
        work_item.assigned_agent_id = agent_id
        work_item.lease_owner_id = None
        work_item.lease_id = None
        work_item.lease_expires_at = None
        work_item.version += 1
    else:
        next_action = disposition.next_action or "complete"
        work_item.lease_owner_id = None
        work_item.lease_id = None
        work_item.lease_expires_at = None
        work_item.version += 1
        if next_action == "review":
            work_item.state = "review"
        elif next_action == "requeue":
            work_item.state = (
                "review" if work_item.attempt_count >= work_item.max_attempts else "queued"
            )
        else:  # "complete" or unrecognized: a safe default is done, not stuck in limbo
            work_item.state = "completed"
            work_item.completed_at = utcnow()
            campaign_contact.status = "completed"
            campaign_contact.completed_at = utcnow()
            campaign_contact.completed_by_agent_id = agent_id
            campaign_contact.final_disposition_code = disposition.stable_semantic_code

    record_audit(
        db, action="work.complete", result="success", actor_user_id=agent_id,
        target_type="work_item", target_id=work_item.id, reason_code=disposition.stable_semantic_code,
    )

    return CompletionResult(
        attempt_id=attempt.id,
        work_item_state=work_item.state,
        semantic_outcome=attempt.semantic_outcome,
        callback_at=attempt.callback_at,
    )


def skip_work_item(
    db: Session, *, work_item_id: uuid.UUID, agent_id: uuid.UUID, lease_id: uuid.UUID, reason: str
) -> WorkItem:
    if not reason or not reason.strip():
        raise MissingRequiredField("a reason is required to skip")

    # No separate idempotency key: the lease_id is single-use by construction (skip
    # clears it), so a naive retry finds the lease already gone and fails as a clean
    # conflict rather than double-counting the skip.
    work_item = _load_leased_item(db, work_item_id, agent_id, lease_id)
    settings = get_settings()
    work_item.skip_count += 1
    work_item.state = (
        "review" if work_item.skip_count >= settings.max_skips_before_review else "queued"
    )
    work_item.lease_owner_id = None
    work_item.lease_id = None
    work_item.lease_expires_at = None
    work_item.version += 1

    record_audit(
        db, action="work.skip", result="success", actor_user_id=agent_id,
        target_type="work_item", target_id=work_item.id, reason_code=reason[:50],
    )
    return work_item
