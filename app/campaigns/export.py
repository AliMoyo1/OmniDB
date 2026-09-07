"""Completed-campaign Excel export (ADR-020).

The single authorized raw-PII export in this system: a completed campaign's
contact database - raw numbers, final dispositions, and calling-agent names - to
an .xlsx a Team Captain saves before the retention countdown deletes it. Every
export is audited (who, when, how many rows). Only a *completed* campaign can be
exported; an active one still holds live data that no raw export path may touch.
"""

from __future__ import annotations

import io
import uuid

from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.models.campaign import Campaign, CampaignDispositionDefinition
from app.models.contact import CampaignContact, Contact
from app.models.identity import User
from app.security.encryption import decrypt

_COLUMNS = ["Phone", "Final disposition", "Agent", "Completed at", "Imported at"]


class CampaignNotExportable(Exception):
    """The campaign is not in a state whose data may be exported."""


def _rows(db: Session, campaign_id: uuid.UUID) -> list[tuple[str, str, str, str, str]]:
    disposition_labels = {
        code: label
        for code, label in db.execute(
            select(
                CampaignDispositionDefinition.stable_semantic_code,
                CampaignDispositionDefinition.label,
            ).where(CampaignDispositionDefinition.campaign_id == campaign_id)
        )
    }
    records = db.execute(
        select(CampaignContact, Contact, User)
        .join(Contact, CampaignContact.contact_id == Contact.id)
        .outerjoin(User, CampaignContact.completed_by_agent_id == User.id)
        .where(CampaignContact.campaign_id == campaign_id)
        .order_by(CampaignContact.completed_at)
    )
    out: list[tuple[str, str, str, str, str]] = []
    for campaign_contact, contact, agent in records:
        code = campaign_contact.final_disposition_code or ""
        out.append(
            (
                decrypt(contact.phone_ciphertext),
                disposition_labels.get(code, code),
                agent.display_name if agent is not None else "",
                campaign_contact.completed_at.isoformat() if campaign_contact.completed_at else "",
                campaign_contact.imported_at.isoformat() if campaign_contact.imported_at else "",
            )
        )
    return out


def build_export_workbook(db: Session, campaign: Campaign) -> bytes:
    """The completed campaign's contacts as .xlsx bytes. Raises
    CampaignNotExportable unless the campaign is completed."""
    if campaign.status != "completed":
        raise CampaignNotExportable("only a completed campaign's data may be exported")
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Contacts"
    sheet.append(_COLUMNS)
    for row in _rows(db, campaign.id):
        sheet.append(list(row))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def export_completed_campaign(db: Session, campaign: Campaign, *, actor_id: uuid.UUID) -> bytes:
    """Build the export and audit it. The audit is the record ADR-020 requires of
    who took raw contact data out of the system. Caller commits."""
    data = build_export_workbook(db, campaign)
    total = db.scalar(
        select(func.count(CampaignContact.id)).where(CampaignContact.campaign_id == campaign.id)
    ) or 0
    record_audit(
        db, action="campaign.export", result="success", actor_user_id=actor_id,
        target_type="campaign", target_id=campaign.id,
        event_metadata={"rows": total, "format": "xlsx"},
    )
    return data
