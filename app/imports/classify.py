"""Per-row classification: validity, duplicates, and suppression matching.

Classification is advisory at parse time. The commit step in service.py revalidates
duplicates and suppression against current state before inserting anything, so a DNC
entry created after preview is still honored (plan invariant 8).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.imports.parser import ParsedRow, sanitize_text
from app.models.contact import CampaignContact, Contact, SuppressionEntry
from app.security.phone import PhoneParseError, protect


@dataclass
class RowClassification:
    row_number: int
    raw_phone_protected: str | None
    phone_fingerprint: str | None
    canonical_values: dict | None
    validation_result: str  # "valid" | "invalid"
    validation_detail: str | None
    duplicate_category: str | None  # "in_file" | "in_campaign" | None
    suppression_match: bool


def _existing_contact_id(db: Session, fingerprint: str) -> uuid.UUID | None:
    return db.scalar(select(Contact.id).where(Contact.phone_fingerprint == fingerprint))


def already_in_campaign(db: Session, campaign_id: uuid.UUID, contact_id: uuid.UUID) -> bool:
    return (
        db.scalar(
            select(CampaignContact.id).where(
                CampaignContact.campaign_id == campaign_id,
                CampaignContact.contact_id == contact_id,
            )
        )
        is not None
    )


def is_suppressed(db: Session, fingerprint: str) -> bool:
    return (
        db.scalar(
            select(SuppressionEntry.id).where(
                SuppressionEntry.phone_fingerprint == fingerprint,
                SuppressionEntry.status == "active",
            )
        )
        is not None
    )


def classify_row(
    row: ParsedRow,
    *,
    db: Session,
    campaign_id: uuid.UUID,
    phone_column: str,
    default_region: str,
    name_column: str | None,
    metadata_columns: list[str],
    seen_fingerprints: set[str],
) -> RowClassification:
    raw_phone = (row.values.get(phone_column) or "").strip()
    if not raw_phone:
        return RowClassification(
            row_number=row.row_number,
            raw_phone_protected=None,
            phone_fingerprint=None,
            canonical_values=None,
            validation_result="invalid",
            validation_detail="missing phone value",
            duplicate_category=None,
            suppression_match=False,
        )

    try:
        protected = protect(raw_phone, default_region)
    except PhoneParseError as exc:
        return RowClassification(
            row_number=row.row_number,
            raw_phone_protected=None,
            phone_fingerprint=None,
            canonical_values=None,
            validation_result="invalid",
            validation_detail=str(exc)[:200],
            duplicate_category=None,
            suppression_match=False,
        )

    duplicate_category: str | None = None
    if protected.fingerprint in seen_fingerprints:
        duplicate_category = "in_file"
    else:
        existing_contact_id = _existing_contact_id(db, protected.fingerprint)
        if existing_contact_id and already_in_campaign(db, campaign_id, existing_contact_id):
            duplicate_category = "in_campaign"
    seen_fingerprints.add(protected.fingerprint)

    canonical: dict[str, str] = {}
    if name_column and row.values.get(name_column):
        canonical["name"] = sanitize_text(row.values[name_column].strip())
    for col in metadata_columns:
        if row.values.get(col):
            canonical[col] = sanitize_text(row.values[col].strip())

    return RowClassification(
        row_number=row.row_number,
        raw_phone_protected=protected.ciphertext,
        phone_fingerprint=protected.fingerprint,
        canonical_values=canonical or None,
        validation_result="valid",
        validation_detail=None,
        duplicate_category=duplicate_category,
        suppression_match=is_suppressed(db, protected.fingerprint),
    )
