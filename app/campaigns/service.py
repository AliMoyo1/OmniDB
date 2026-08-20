"""Campaign lifecycle and disposition-definition service.

Status transitions are validated here, not left to the client (plan 10.2). Only the
approved protected semantic code may cause global DNC suppression (invariant 9, ADR-009):
a custom disposition label cannot silently acquire that behavior.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.models.base import utcnow
from app.models.campaign import Campaign, CampaignDispositionDefinition
from app.models.contact import CampaignContact

# Only this stable semantic code may set causes_dnc=True (ADR-009).
PROTECTED_DNC_SEMANTIC_CODES = {"explicit_dnc"}


class CampaignStateError(Exception):
    pass


class DispositionPolicyError(Exception):
    pass


def create_campaign(
    db: Session,
    *,
    created_by: uuid.UUID,
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
    campaign = Campaign(
        owning_scope_type=owning_scope_type,
        owning_scope_id=owning_scope_id,
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
