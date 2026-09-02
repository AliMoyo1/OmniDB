"""Campaign lifecycle and disposition-definition service.

Status transitions are validated here, not left to the client (plan 10.2). Only the
approved protected semantic code may cause global DNC suppression (invariant 9, ADR-009):
a custom disposition label cannot silently acquire that behavior.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.flags import service as flags
from app.models.base import utcnow
from app.models.campaign import (
    Campaign,
    CampaignDispositionDefinition,
    CampaignTeamAssignment,
    CampaignUserAssignment,
)
from app.models.contact import CampaignContact
from app.work.service import release_campaign_work_for_agent

# Only this stable semantic code may set causes_dnc=True (ADR-009).
PROTECTED_DNC_SEMANTIC_CODES = {"explicit_dnc"}


class CampaignStateError(Exception):
    pass


class DispositionPolicyError(Exception):
    pass


class CampaignAssignmentError(Exception):
    pass


class DuplicateCampaignCode(Exception):
    pass


class InvalidCampaignCode(Exception):
    pass


def normalize_campaign_code(external_code: str) -> str:
    """The one place this is enforced regardless of caller - CampaignCreateRequest's
    own field validator only covers the JSON API; the web form and the dashboard's
    separate, older create-campaign form both call this function directly with a
    raw Form(...) string, not through that schema."""
    cleaned = external_code.strip()
    if not cleaned:
        raise InvalidCampaignCode("external_code must not be empty")
    if len(cleaned) > 50:
        raise InvalidCampaignCode("external_code must be at most 50 characters")
    if any(character.isspace() for character in cleaned):
        raise InvalidCampaignCode("external_code must not contain whitespace")
    return cleaned


def _check_staffing_capacity(
    db: Session, campaign_id: uuid.UUID, team_id: uuid.UUID | None
) -> None:
    """Plan 6, Phase 4A step 6: a transfer (or a plain assignment) must respect the
    destination's staffing capacity where one is set. No team, or a team with no
    capacity configured, means unlimited - staffing_capacity is optional (plan 4C's
    fuller staffing model is out of scope; this is the minimal real check D-18's
    "destination capacity" preflight needs).

    The team_assignment row is locked (FOR UPDATE) for the caller's transaction:
    without it, two concurrent assignment requests for the same (campaign, team)
    can both read the same under-capacity count before either commits and both
    succeed, landing the team over capacity - the same TOCTOU shape the leasing
    path already closes with SELECT ... FOR UPDATE SKIP LOCKED. Locking this row
    serializes concurrent callers so the second one re-reads a count that already
    reflects the first one's insert."""
    if team_id is None:
        return
    now = utcnow()
    team_assignment = db.scalar(
        select(CampaignTeamAssignment)
        .where(
            CampaignTeamAssignment.campaign_id == campaign_id,
            CampaignTeamAssignment.team_id == team_id,
            CampaignTeamAssignment.status == "active",
            CampaignTeamAssignment.effective_from <= now,
            or_(
                CampaignTeamAssignment.effective_to.is_(None),
                CampaignTeamAssignment.effective_to > now,
            ),
        )
        .with_for_update()
    )
    if team_assignment is None or team_assignment.staffing_capacity is None:
        return
    current_count = (
        db.scalar(
            select(func.count(CampaignUserAssignment.id)).where(
                CampaignUserAssignment.campaign_id == campaign_id,
                CampaignUserAssignment.team_id == team_id,
                CampaignUserAssignment.status == "active",
                CampaignUserAssignment.effective_to.is_(None),
            )
        )
        or 0
    )
    if current_count >= team_assignment.staffing_capacity:
        raise CampaignAssignmentError(
            f"destination campaign is at staffing capacity for this team "
            f"({current_count}/{team_assignment.staffing_capacity})"
        )


def assign_team_to_campaign(
    db: Session,
    campaign: Campaign,
    *,
    team_id: uuid.UUID,
    staffing_capacity: int | None,
    actor_id: uuid.UUID,
) -> CampaignTeamAssignment:
    assignment = CampaignTeamAssignment(
        campaign_id=campaign.id,
        team_id=team_id,
        effective_from=utcnow(),
        staffing_capacity=staffing_capacity,
        assigned_by=actor_id,
    )
    db.add(assignment)
    db.flush()
    record_audit(
        db, action="campaign.team_assignment.create", result="success", actor_user_id=actor_id,
        target_type="campaign", target_id=campaign.id, event_metadata={"team_id": str(team_id)},
    )
    return assignment


def end_team_assignment(
    db: Session, assignment: CampaignTeamAssignment, *, actor_id: uuid.UUID
) -> CampaignTeamAssignment:
    assignment.status = "ended"
    assignment.effective_to = utcnow()
    record_audit(
        db, action="campaign.team_assignment.end", result="success", actor_user_id=actor_id,
        target_type="campaign", target_id=assignment.campaign_id,
        event_metadata={"team_id": str(assignment.team_id)},
    )
    return assignment


def assign_agent_to_campaign(
    db: Session,
    campaign: Campaign,
    *,
    agent_id: uuid.UUID,
    team_id: uuid.UUID | None,
    actor_id: uuid.UUID,
    assignment_type: str = "primary",
    priority: int | None = None,
    allocation_percentage: int | None = None,
    shift_reference: str | None = None,
) -> CampaignUserAssignment:
    """A fresh assignment for an agent who does not currently have an active primary
    one anywhere. Moving an agent who already has one is transfer_agent's job - this
    function deliberately does not supersede an existing primary assignment, so every
    move goes through the one path that reclaims in-flight work and checks
    destination capacity, rather than two independent ways to reach the same state."""
    if assignment_type == "primary":
        existing_primary = db.scalar(
            select(CampaignUserAssignment).where(
                CampaignUserAssignment.user_id == agent_id,
                CampaignUserAssignment.assignment_type == "primary",
                CampaignUserAssignment.status == "active",
                CampaignUserAssignment.effective_to.is_(None),
            )
        )
        if existing_primary is not None:
            raise CampaignAssignmentError(
                "agent already has an active primary assignment; use transfer instead"
            )
    _check_staffing_capacity(db, campaign.id, team_id)
    assignment = CampaignUserAssignment(
        campaign_id=campaign.id,
        user_id=agent_id,
        team_id=team_id,
        campaign_role="agent",
        assignment_type=assignment_type,
        effective_from=utcnow(),
        priority=priority,
        allocation_percentage=allocation_percentage,
        shift_reference=shift_reference,
        assigned_by=actor_id,
    )
    db.add(assignment)
    db.flush()
    record_audit(
        db, action="campaign.assignment.create", result="success", actor_user_id=actor_id,
        target_type="campaign", target_id=campaign.id,
        event_metadata={"agent_id": str(agent_id), "assignment_type": assignment_type},
    )
    return assignment


def end_user_assignment(
    db: Session,
    assignment: CampaignUserAssignment,
    *,
    actor_id: uuid.UUID,
    reason_code: str | None = None,
) -> CampaignUserAssignment:
    assignment.status = "ended"
    assignment.effective_to = utcnow()
    released = release_campaign_work_for_agent(db, assignment.user_id, assignment.campaign_id)
    record_audit(
        db, action="campaign.assignment.end", result="success", actor_user_id=actor_id,
        target_type="campaign", target_id=assignment.campaign_id, reason_code=reason_code,
        event_metadata={
            "agent_id": str(assignment.user_id), "work_items_released": released,
        },
    )
    return assignment


def transfer_agent(
    db: Session,
    *,
    agent_id: uuid.UUID,
    from_campaign: Campaign,
    to_campaign: Campaign,
    team_id: uuid.UUID | None,
    actor_id: uuid.UUID,
    reason_code: str | None = None,
) -> CampaignUserAssignment:
    """D-18: transfer preflight. Lease and callback treatment is fixed at "return to
    the source queue" - not configurable per call, since ops's own resolution was
    "pick the defaults" rather than name a treatment, and this is the same treatment
    already used for reclaim_leases_for_user (disabling a user). Target proration is
    correctly absent: D-19/D-20 defer the whole target subsystem from this pilot."""
    if from_campaign.id == to_campaign.id:
        raise CampaignAssignmentError("source and destination campaign must differ")
    source = db.scalar(
        select(CampaignUserAssignment).where(
            CampaignUserAssignment.user_id == agent_id,
            CampaignUserAssignment.campaign_id == from_campaign.id,
            CampaignUserAssignment.assignment_type == "primary",
            CampaignUserAssignment.status == "active",
            CampaignUserAssignment.effective_to.is_(None),
        )
    )
    if source is None:
        raise CampaignAssignmentError(
            "agent has no active primary assignment on the source campaign"
        )
    if to_campaign.status != "active":
        raise CampaignAssignmentError("destination campaign is not active")
    _check_staffing_capacity(db, to_campaign.id, team_id)

    now = utcnow()
    source.status = "ended"
    source.effective_to = now
    db.flush()
    released = release_campaign_work_for_agent(db, agent_id, from_campaign.id)

    destination = CampaignUserAssignment(
        campaign_id=to_campaign.id,
        user_id=agent_id,
        team_id=team_id,
        campaign_role="agent",
        assignment_type="primary",
        effective_from=now,
        assigned_by=actor_id,
    )
    db.add(destination)
    db.flush()
    record_audit(
        db, action="campaign.assignment.transfer", result="success", actor_user_id=actor_id,
        target_type="campaign", target_id=to_campaign.id, reason_code=reason_code,
        event_metadata={
            "agent_id": str(agent_id),
            "from_campaign_id": str(from_campaign.id),
            "to_campaign_id": str(to_campaign.id),
            "work_items_released": released,
        },
    )
    return destination


def create_campaign(
    db: Session,
    *,
    created_by: uuid.UUID,
    external_code: str,
    name: str,
    description: str | None,
    owning_scope_type: str,
    owning_scope_id: uuid.UUID | None,
    default_region: str,
    timezone: str,
    purpose: str | None,
    data_source: str | None,
    data_obtained_at,
    lawful_basis_or_consent_reference: str | None,
) -> Campaign:
    external_code = normalize_campaign_code(external_code)
    if db.scalar(select(Campaign.id).where(Campaign.external_code == external_code)) is not None:
        raise DuplicateCampaignCode(f"a campaign with code '{external_code}' already exists")
    campaign = Campaign(
        owning_scope_type=owning_scope_type,
        owning_scope_id=owning_scope_id,
        external_code=external_code,
        name=name,
        description=description,
        default_region=default_region,
        timezone=timezone,
        purpose=purpose,
        data_source=data_source,
        data_obtained_at=data_obtained_at,
        lawful_basis_or_consent_reference=lawful_basis_or_consent_reference,
        status="draft",
        created_by=created_by,
    )
    db.add(campaign)
    db.flush()
    record_audit(
        db, action="campaign.create", result="success", actor_user_id=created_by,
        target_type="campaign", target_id=campaign.id,
    )
    return campaign


def update_campaign(db: Session, campaign: Campaign, *, actor_id: uuid.UUID, **fields) -> Campaign:
    if campaign.status != "draft":
        raise CampaignStateError("only a draft campaign may be edited")
    for key, value in fields.items():
        if value is not None:
            setattr(campaign, key, value)
    record_audit(
        db, action="campaign.update", result="success", actor_user_id=actor_id,
        target_type="campaign", target_id=campaign.id,
    )
    return campaign


def launch_campaign(db: Session, campaign: Campaign, *, actor_id: uuid.UUID) -> Campaign:
    flags.require_enabled(db, "campaign_launch_enabled")
    if campaign.status != "draft":
        raise CampaignStateError(f"cannot launch a campaign in state {campaign.status}")
    missing = [
        name
        for name, value in {
            "data_source": campaign.data_source,
            "purpose": campaign.purpose,
            "data_obtained_at": campaign.data_obtained_at,
            "lawful_basis_or_consent_reference": campaign.lawful_basis_or_consent_reference,
        }.items()
        if not value
    ]
    if missing:
        raise CampaignStateError(f"missing required provenance fields: {', '.join(missing)}")
    contact_count = db.scalar(
        select(func.count(CampaignContact.id)).where(CampaignContact.campaign_id == campaign.id)
    )
    if not contact_count:
        raise CampaignStateError("cannot launch a campaign with no committed imported contacts")

    campaign.status = "active"
    campaign.launched_at = utcnow()
    record_audit(
        db, action="campaign.launch", result="success", actor_user_id=actor_id,
        target_type="campaign", target_id=campaign.id, event_metadata={"contacts": contact_count},
    )
    return campaign


def pause_campaign(db: Session, campaign: Campaign, *, actor_id: uuid.UUID) -> Campaign:
    if campaign.status != "active":
        raise CampaignStateError(f"cannot pause a campaign in state {campaign.status}")
    campaign.status = "paused"
    record_audit(
        db, action="campaign.pause", result="success", actor_user_id=actor_id,
        target_type="campaign", target_id=campaign.id,
    )
    return campaign


def archive_campaign(db: Session, campaign: Campaign, *, actor_id: uuid.UUID) -> Campaign:
    if campaign.status not in ("active", "paused", "draft"):
        raise CampaignStateError(f"cannot archive a campaign in state {campaign.status}")
    campaign.status = "archived"
    campaign.archived_at = utcnow()
    record_audit(
        db, action="campaign.archive", result="success", actor_user_id=actor_id,
        target_type="campaign", target_id=campaign.id,
    )
    return campaign


def create_disposition(
    db: Session,
    campaign: Campaign,
    *,
    actor_id: uuid.UUID,
    label: str,
    stable_semantic_code: str,
    next_action: str | None,
    requires_notes: bool,
    requires_callback_time: bool,
    counts_as_connected: bool,
    counts_as_conversion: bool,
    causes_dnc: bool,
) -> CampaignDispositionDefinition:
    if causes_dnc and stable_semantic_code not in PROTECTED_DNC_SEMANTIC_CODES:
        raise DispositionPolicyError(
            "only an approved protected semantic code may cause global DNC suppression"
        )
    if stable_semantic_code in PROTECTED_DNC_SEMANTIC_CODES and not causes_dnc:
        raise DispositionPolicyError(
            "the protected explicit-DNC semantic code must cause global DNC suppression"
        )
    if next_action not in (None, "complete", "review", "requeue"):
        raise DispositionPolicyError("next action must be complete, review, or requeue")
    disposition = CampaignDispositionDefinition(
        campaign_id=campaign.id,
        label=label,
        stable_semantic_code=stable_semantic_code,
        next_action=next_action,
        requires_notes=requires_notes,
        requires_callback_time=requires_callback_time,
        counts_as_connected=counts_as_connected,
        counts_as_conversion=counts_as_conversion,
        causes_dnc=causes_dnc,
    )
    db.add(disposition)
    db.flush()
    record_audit(
        db, action="campaign.disposition.create", result="success", actor_user_id=actor_id,
        target_type="campaign", target_id=campaign.id,
        event_metadata={"stable_semantic_code": stable_semantic_code, "causes_dnc": causes_dnc},
    )
    return disposition
