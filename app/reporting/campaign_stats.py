"""Aggregate campaign performance, computed live from immutable call attempts.

Totals only - never a raw contact, note, import row, or DNC entry (plan 6.1). This
is the one kind of data Viewer is meant to hold: plan 6.3's "View analytics" row.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.campaign import CampaignDispositionDefinition, CampaignUserAssignment
from app.models.contact import CampaignContact
from app.models.work import CallAttempt


def _count(db: Session, campaign_id: uuid.UUID, *, only_flag: str | None = None) -> int:
    stmt = (
        select(func.count(CallAttempt.id))
        .join(CampaignContact, CallAttempt.campaign_contact_id == CampaignContact.id)
        .where(CampaignContact.campaign_id == campaign_id)
    )
    if only_flag == "connected":
        stmt = stmt.join(
            CampaignDispositionDefinition,
            CallAttempt.disposition_definition_id == CampaignDispositionDefinition.id,
        ).where(CampaignDispositionDefinition.counts_as_connected.is_(True))
    elif only_flag == "conversion":
        stmt = stmt.join(
            CampaignDispositionDefinition,
            CallAttempt.disposition_definition_id == CampaignDispositionDefinition.id,
        ).where(CampaignDispositionDefinition.counts_as_conversion.is_(True))
    elif only_flag == "dnc":
        stmt = stmt.where(CallAttempt.explicit_dnc_requested.is_(True))
    return db.scalar(stmt) or 0


def get_campaign_stats(db: Session, campaign_id: uuid.UUID) -> dict:
    total_contacts = (
        db.scalar(
            select(func.count(CampaignContact.id)).where(
                CampaignContact.campaign_id == campaign_id
            )
        )
        or 0
    )
    assigned_agents = (
        db.scalar(
            select(func.count(CampaignUserAssignment.id)).where(
                CampaignUserAssignment.campaign_id == campaign_id,
                CampaignUserAssignment.status == "active",
                CampaignUserAssignment.effective_to.is_(None),
            )
        )
        or 0
    )
    return {
        "total_contacts": total_contacts,
        "assigned_agents": assigned_agents,
        "total_attempts": _count(db, campaign_id),
        "connected": _count(db, campaign_id, only_flag="connected"),
        "conversions": _count(db, campaign_id, only_flag="conversion"),
        "dnc_requests": _count(db, campaign_id, only_flag="dnc"),
    }
