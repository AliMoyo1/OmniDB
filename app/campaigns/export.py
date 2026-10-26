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
from dataclasses import dataclass
from datetime import datetime

from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.imports.parser import sanitize_text
from app.models.base import utcnow
from app.models.campaign import Campaign, CampaignDispositionDefinition
from app.models.contact import CampaignContact, Contact
from app.models.identity import User
from app.security.encryption import decrypt

_COLUMNS = ["Phone", "Final disposition", "Agent", "Completed at", "Imported at"]

# Length caps on the operator-entered provenance fields, so a stray paste cannot
# bloat the audit row or the Export Info sheet.
_JUSTIFICATION_MAX = 500
_SHORT_FIELD_MAX = 200


class CampaignNotExportable(Exception):
    """The campaign is not in a state whose data may be exported."""


@dataclass(frozen=True)
class ExportContext:
    """Provenance for one completed-campaign export: who took the data out, when,
    for whom, with whose approval and why. Written into both the Export Info sheet
    and the audit event so this one raw-PII export is never anonymous."""

    exporter_name: str
    justification: str
    approver: str
    recipient: str
    exported_at: datetime


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
        # Neutralize spreadsheet-formula injection on the operator-controlled free
        # text - a disposition label or an agent display name can reach here
        # unsanitized, and openpyxl stores a value like "=1+1" as a live FORMULA
        # that would run when the Team Captain opens this raw-PII export. The phone
        # (validated E.164: "+" then digits, never a formula) and the ISO
        # timestamps are left exactly as-is, so the numbers stay clean and reusable.
        out.append(
            (
                decrypt(contact.phone_ciphertext),
                sanitize_text(disposition_labels.get(code, code)),
                sanitize_text(agent.display_name) if agent is not None else "",
                campaign_contact.completed_at.isoformat() if campaign_contact.completed_at else "",
                campaign_contact.imported_at.isoformat() if campaign_contact.imported_at else "",
            )
        )
    return out


def _append_export_info(workbook: Workbook, campaign: Campaign, context: ExportContext) -> None:
    """A second sheet carrying the export's provenance. Added after Contacts so it
    never disturbs the Contacts sheet or its position. The operator-entered fields
    are run through the same formula-injection guard as the contact data."""
    sheet = workbook.create_sheet("Export Info")
    for label, value in (
        ("Classification", "RESTRICTED - do not share outside the approved recipient"),
        ("Campaign", sanitize_text(campaign.name)),
        ("Campaign code", sanitize_text(campaign.external_code)),
        ("Exported by", sanitize_text(context.exporter_name)),
        ("Exported at", context.exported_at.isoformat()),
        ("Recipient", sanitize_text(context.recipient)),
        ("Approved by", sanitize_text(context.approver)),
        ("Business justification", sanitize_text(context.justification)),
    ):
        sheet.append([label, value])


def build_export_workbook(
    db: Session, campaign: Campaign, *, context: ExportContext | None = None
) -> bytes:
    """The completed campaign's contacts as .xlsx bytes. Raises CampaignNotExportable
    unless the campaign is completed. When `context` is given (a real, attributed
    export) a provenance sheet is added; a context-less call is an internal build."""
    if campaign.status != "completed":
        raise CampaignNotExportable("only a completed campaign's data may be exported")
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Contacts"
    # A visible restricted label on any printout of the raw contact data.
    sheet.oddFooter.center.text = "RESTRICTED - CipherContact export - do not share"
    sheet.append(_COLUMNS)
    for row in _rows(db, campaign.id):
        sheet.append(list(row))
    if context is not None:
        _append_export_info(workbook, campaign, context)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def export_completed_campaign(
    db: Session,
    campaign: Campaign,
    *,
    actor_id: uuid.UUID,
    exporter_name: str,
    justification: str,
    approver: str,
    recipient: str,
) -> bytes:
    """Build the export and audit it. The audit is the record ADR-020 requires of who
    took raw contact data out of the system, now also carrying why, who approved it
    and who receives it. Caller commits."""
    justification = justification.strip()[:_JUSTIFICATION_MAX]
    approver = approver.strip()[:_SHORT_FIELD_MAX]
    recipient = recipient.strip()[:_SHORT_FIELD_MAX]
    context = ExportContext(
        exporter_name=exporter_name[:_SHORT_FIELD_MAX],
        justification=justification,
        approver=approver,
        recipient=recipient,
        exported_at=utcnow(),
    )
    data = build_export_workbook(db, campaign, context=context)
    total = db.scalar(
        select(func.count(CampaignContact.id)).where(CampaignContact.campaign_id == campaign.id)
    ) or 0
    record_audit(
        db, action="campaign.export", result="success", actor_user_id=actor_id,
        target_type="campaign", target_id=campaign.id,
        event_metadata={
            "rows": total, "format": "xlsx",
            "justification": justification, "approver": approver, "recipient": recipient,
        },
    )
    return data
