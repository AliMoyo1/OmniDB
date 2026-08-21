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

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.config import get_settings
from app.db_locks import (
    lock_idempotency_key,
    lock_phone_fingerprint,
    try_lock_phone_fingerprint,
)
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


class IdempotencyConflict(WorkItemError):
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
        .order_by(CampaignUserAssignment.effective_from.desc())
        .with_for_update()
    )


def _active_assignment_for_campaign(
    db: Session, agent_id: uuid.UUID, campaign_id: uuid.UUID
) -> CampaignUserAssignment | None:
    now = utcnow()
    return db.scalar(
        select(CampaignUserAssignment)
        .where(
            CampaignUserAssignment.user_id == agent_id,
            CampaignUserAssignment.campaign_id == campaign_id,
            CampaignUserAssignment.campaign_role == "agent",
            CampaignUserAssignment.status == "active",
            CampaignUserAssignment.effective_from <= now,
            or_(
                CampaignUserAssignment.effective_to.is_(None),
                CampaignUserAssignment.effective_to > now,
            ),
        )
        .order_by(CampaignUserAssignment.effective_from.desc())
        .with_for_update()
    )


@dataclass(frozen=True)
class _LeaseCandidate:
    work_item_id: uuid.UUID
    phone_fingerprint: str
    campaign_id: uuid.UUID


def _active_suppression_exists(db: Session, phone_fingerprint: str) -> bool:
    return db.scalar(
        select(SuppressionEntry.id).where(
            SuppressionEntry.phone_fingerprint == phone_fingerprint,
            SuppressionEntry.status == "active",
        )
    ) is not None


def _next_callback_candidate(
    db: Session,
    agent_id: uuid.UUID,
    now: datetime,
    excluded_work_item_ids: set[uuid.UUID],
) -> _LeaseCandidate | None:
    active_assignment = exists().where(
        CampaignUserAssignment.user_id == agent_id,
        CampaignUserAssignment.campaign_id == CampaignContact.campaign_id,
        CampaignUserAssignment.campaign_role == "agent",
        CampaignUserAssignment.status == "active",
        CampaignUserAssignment.effective_from <= now,
        or_(
            CampaignUserAssignment.effective_to.is_(None),
            CampaignUserAssignment.effective_to > now,
        ),
    )
    not_suppressed = ~exists().where(
        SuppressionEntry.phone_fingerprint == Contact.phone_fingerprint,
        SuppressionEntry.status == "active",
    )
    stmt = (
        select(
            WorkItem.id,
            Contact.phone_fingerprint,
            CampaignContact.campaign_id,
        )
        .join(CampaignContact, WorkItem.campaign_contact_id == CampaignContact.id)
        .join(Contact, CampaignContact.contact_id == Contact.id)
        .join(Campaign, CampaignContact.campaign_id == Campaign.id)
        .where(
            WorkItem.state == "callback_wait",
            WorkItem.assigned_agent_id == agent_id,
            WorkItem.due_at <= now,
            Campaign.status == "active",
            active_assignment,
            not_suppressed,
        )
        .order_by(WorkItem.due_at.asc())
        .limit(1)
    )
    if excluded_work_item_ids:
        stmt = stmt.where(WorkItem.id.notin_(excluded_work_item_ids))
    row = db.execute(stmt).one_or_none()
    return _LeaseCandidate(*row) if row is not None else None


def _next_queue_candidate(
    db: Session,
    campaign_id: uuid.UUID,
    excluded_work_item_ids: set[uuid.UUID],
) -> _LeaseCandidate | None:
    not_suppressed = ~exists().where(
        SuppressionEntry.phone_fingerprint == Contact.phone_fingerprint,
        SuppressionEntry.status == "active",
    )
    stmt = (
        select(
            WorkItem.id,
            Contact.phone_fingerprint,
            CampaignContact.campaign_id,
        )
        .join(CampaignContact, WorkItem.campaign_contact_id == CampaignContact.id)
        .join(Contact, CampaignContact.contact_id == Contact.id)
        .where(
            CampaignContact.campaign_id == campaign_id,
            WorkItem.state == "queued",
            not_suppressed,
        )
        .order_by(WorkItem.priority.desc(), WorkItem.created_at.asc())
        .limit(1)
    )
    if excluded_work_item_ids:
        stmt = stmt.where(WorkItem.id.notin_(excluded_work_item_ids))
    row = db.execute(stmt).one_or_none()
    return _LeaseCandidate(*row) if row is not None else None


def _lock_lease_candidate(
    db: Session,
    candidate: _LeaseCandidate,
    *,
    agent_id: uuid.UUID,
    now: datetime,
    is_callback: bool,
) -> tuple[WorkItem, CampaignContact, Contact] | None:
    # The phone lock comes before the work-row lock on every leasing path. Use the
    # nonblocking form so a busy phone does not stall unrelated queue records.
    if not try_lock_phone_fingerprint(db, candidate.phone_fingerprint):
        return None

    conditions = [WorkItem.id == candidate.work_item_id]
    if is_callback:
        conditions.extend(
            [
                WorkItem.state == "callback_wait",
                WorkItem.assigned_agent_id == agent_id,
                WorkItem.due_at <= now,
            ]
        )
    else:
        conditions.append(WorkItem.state == "queued")

    work_item = db.scalar(
        select(WorkItem).where(*conditions).with_for_update(skip_locked=True)
    )
    if work_item is None:
        return None

    campaign_contact = db.get(CampaignContact, work_item.campaign_contact_id)
    if campaign_contact is None:
        raise WorkItemError("work item references a missing campaign contact")
    contact = db.get(Contact, campaign_contact.contact_id)
    if contact is None:
        raise WorkItemError("campaign contact references a missing contact")
    campaign = db.get(Campaign, campaign_contact.campaign_id)
    if campaign is None or campaign.status != "active":
        return None

    if _active_suppression_exists(db, contact.phone_fingerprint):
        work_item.state = "suppressed"
        work_item.completed_at = now
        work_item.version += 1
        campaign_contact.status = "suppressed"
        campaign_contact.completed_at = now
        campaign_contact.final_disposition_code = "explicit_dnc"
        db.flush()
        return None

    return work_item, campaign_contact, contact


def lease_next(db: Session, agent_id: uuid.UUID) -> LeaseResult | None:
    now = utcnow()
    work_item: WorkItem | None = None
    campaign_contact: CampaignContact | None = None
    contact: Contact | None = None
    assignment: CampaignUserAssignment | None = None
    is_callback = False
    skipped_callback_ids: set[uuid.UUID] = set()

    # Due callbacks take priority, but raw contact data is returned only while an
    # effective campaign assignment still authorizes the agent.
    while True:
        candidate = _next_callback_candidate(
            db, agent_id, now, skipped_callback_ids
        )
        if candidate is None:
            break
        assignment = _active_assignment_for_campaign(db, agent_id, candidate.campaign_id)
        if assignment is None:
            skipped_callback_ids.add(candidate.work_item_id)
            continue
        locked = _lock_lease_candidate(
            db, candidate, agent_id=agent_id, now=now, is_callback=True
        )
        if locked is None:
            skipped_callback_ids.add(candidate.work_item_id)
            continue
        work_item, campaign_contact, contact = locked
        is_callback = True
        break

    if work_item is None:
        assignment = _active_primary_assignment(db, agent_id)
        if assignment is None:
            return None

        campaign = db.get(Campaign, assignment.campaign_id)
        if campaign is None or campaign.status != "active":
            return None

        skipped_queue_ids: set[uuid.UUID] = set()
        while True:
            candidate = _next_queue_candidate(db, campaign.id, skipped_queue_ids)
            if candidate is None:
                return None
            locked = _lock_lease_candidate(
                db, candidate, agent_id=agent_id, now=now, is_callback=False
            )
            if locked is None:
                skipped_queue_ids.add(candidate.work_item_id)
                continue
            work_item, campaign_contact, contact = locked
            break

    if campaign_contact is None or contact is None or assignment is None:
        raise WorkItemError("lease candidate is missing required assignment data")

    settings = get_settings()
    lease_id = uuid.uuid4()
    work_item.state = "leased"
    work_item.lease_owner_id = agent_id
    work_item.lease_id = lease_id
    work_item.lease_expires_at = now + timedelta(minutes=settings.lease_duration_minutes)
    work_item.campaign_user_assignment_id = assignment.id
    work_item.version += 1
    db.flush()

    campaign = db.get(Campaign, campaign_contact.campaign_id)
    if campaign is None:
        raise WorkItemError("campaign contact references a missing campaign")

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
        select(WorkItem)
        .where(WorkItem.state == "leased", WorkItem.lease_expires_at < now)
        .with_for_update(skip_locked=True)
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


def reclaim_leases_for_user(db: Session, agent_id: uuid.UUID) -> int:
    """Return every item currently leased by agent_id to its pre-lease state.

    Used when a user is disabled (plan 6.4: disabling a user immediately revokes
    active sessions and leases). Same pre-lease-state rule as reclaim_expired_leases:
    a callback returns to callback_wait still owned by the same agent, a shared-pool
    item returns to queued.
    """
    leased = db.scalars(
        select(WorkItem)
        .where(WorkItem.state == "leased", WorkItem.lease_owner_id == agent_id)
        .with_for_update(skip_locked=True)
    )
    count = 0
    for item in leased:
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


def _phone_fingerprint_for_work_item(
    db: Session, work_item_id: uuid.UUID
) -> str | None:
    return db.scalar(
        select(Contact.phone_fingerprint)
        .join(CampaignContact, CampaignContact.contact_id == Contact.id)
        .join(WorkItem, WorkItem.campaign_contact_id == CampaignContact.id)
        .where(WorkItem.id == work_item_id)
    )


def _result_from_existing_attempt(
    existing: CallAttempt, work_item_id: uuid.UUID
) -> CompletionResult:
    if existing.work_item_id != work_item_id:
        raise IdempotencyConflict("idempotency key was already used for another work item")
    return CompletionResult(
        attempt_id=existing.id,
        work_item_state=existing.resulting_work_item_state,
        semantic_outcome=existing.semantic_outcome,
        callback_at=existing.callback_at,
    )


def _suppress_contact_everywhere(
    db: Session, contact: Contact, *, source: str, created_by: uuid.UUID
) -> None:
    """Create or reuse an active suppression and close every pending work item for
    this contact across all campaigns, including the one currently being completed
    (invariant 7 applied globally, not only to the campaign the call happened in)."""
    lock_phone_fingerprint(db, contact.phone_fingerprint)
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
        if campaign_contact is None:
            raise WorkItemError("work item references a missing campaign contact")
        campaign_contact.status = "suppressed"
        campaign_contact.completed_at = now
        campaign_contact.completed_by_agent_id = created_by
        campaign_contact.final_disposition_code = "explicit_dnc"


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
    lock_idempotency_key(db, "work.complete", agent_id, idempotency_key)
    existing = _existing_attempt(db, agent_id, idempotency_key)
    if existing is not None:
        return _result_from_existing_attempt(existing, work_item_id)

    phone_fingerprint = _phone_fingerprint_for_work_item(db, work_item_id)
    if phone_fingerprint is None:
        raise LeaseConflict("lease is not active or not owned by this agent")
    # Suppression, import, leasing, and completion all reserve the phone before a
    # work row. This makes a DNC commit and a normal completion strictly ordered.
    lock_phone_fingerprint(db, phone_fingerprint)

    work_item = _load_leased_item(db, work_item_id, agent_id, lease_id)
    campaign_contact = db.get(CampaignContact, work_item.campaign_contact_id)
    if campaign_contact is None:
        raise WorkItemError("work item references a missing campaign contact")

    disposition = db.get(CampaignDispositionDefinition, disposition_id)
    if disposition is None or disposition.campaign_id != campaign_contact.campaign_id:
        raise DispositionMismatch("disposition does not belong to this work item's campaign")
    if not disposition.active:
        raise DispositionMismatch("disposition is not active")
    if disposition.requires_notes and not notes:
        raise MissingRequiredField("notes are required for this disposition")
    if disposition.requires_callback_time and callback_at is None:
        raise MissingRequiredField("a callback time is required for this disposition")
    if disposition.requires_callback_time and callback_at is not None and callback_at <= utcnow():
        raise MissingRequiredField("callback time must be in the future")
    if self_reported_duration_seconds is not None and self_reported_duration_seconds < 0:
        raise MissingRequiredField("duration cannot be negative")

    contact = db.get(Contact, campaign_contact.contact_id)
    if contact is None:
        raise WorkItemError("campaign contact references a missing contact")
    notes_ciphertext = encrypt(notes) if notes else None

    attempt = CallAttempt(
        work_item_id=work_item.id,
        campaign_contact_id=campaign_contact.id,
        agent_id=agent_id,
        campaign_user_assignment_id=work_item.campaign_user_assignment_id,
        disposition_definition_id=disposition.id,
        semantic_outcome=disposition.stable_semantic_code,
        notes_ciphertext=notes_ciphertext,
        self_reported_duration_seconds=self_reported_duration_seconds,
        explicit_dnc_requested=disposition.causes_dnc,
        callback_at=callback_at if disposition.requires_callback_time else None,
        idempotency_key=idempotency_key,
        # A query in DNC handling can autoflush this row before the final state is
        # known. Keep the column valid, then replace it with the committed result.
        resulting_work_item_state=work_item.state,
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
        elif next_action == "complete":
            work_item.state = "completed"
            work_item.completed_at = utcnow()
            campaign_contact.status = "completed"
            campaign_contact.completed_at = utcnow()
            campaign_contact.completed_by_agent_id = agent_id
            campaign_contact.final_disposition_code = disposition.stable_semantic_code
        else:
            raise DispositionMismatch("disposition has an unsupported next action")

    attempt.resulting_work_item_state = work_item.state

    record_audit(
        db, action="work.complete", result="success", actor_user_id=agent_id,
        target_type="work_item", target_id=work_item.id,
        reason_code=disposition.stable_semantic_code,
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


@dataclass(frozen=True)
class CallbackListItem:
    work_item_id: uuid.UUID
    campaign_id: uuid.UUID
    campaign_name: str
    reference: str
    due_at: datetime | None


def list_agent_callbacks(db: Session, agent_id: uuid.UUID) -> list[CallbackListItem]:
    """Masked callback references only (plan invariant 3): a name if one was imported,
    otherwise a short non-reversible fragment. Never the phone number - revealing it
    still requires leasing the item, same as any other work."""
    now = utcnow()
    active_assignment = exists().where(
        CampaignUserAssignment.user_id == agent_id,
        CampaignUserAssignment.campaign_id == CampaignContact.campaign_id,
        CampaignUserAssignment.campaign_role == "agent",
        CampaignUserAssignment.status == "active",
        CampaignUserAssignment.effective_from <= now,
        or_(
            CampaignUserAssignment.effective_to.is_(None),
            CampaignUserAssignment.effective_to > now,
        ),
    )
    rows = db.execute(
        select(WorkItem, CampaignContact, Campaign)
        .join(CampaignContact, WorkItem.campaign_contact_id == CampaignContact.id)
        .join(Campaign, CampaignContact.campaign_id == Campaign.id)
        .where(
            WorkItem.state == "callback_wait",
            WorkItem.assigned_agent_id == agent_id,
            Campaign.status == "active",
            active_assignment,
        )
        .order_by(WorkItem.due_at.asc())
    )
    results = []
    for work_item, campaign_contact, campaign in rows:
        reference = campaign_contact.campaign_name_value or f"Contact #{str(work_item.id)[:8]}"
        results.append(
            CallbackListItem(
                work_item_id=work_item.id,
                campaign_id=campaign.id,
                campaign_name=campaign.name,
                reference=reference,
                due_at=work_item.due_at,
            )
        )
    return results
