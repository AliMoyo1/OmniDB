"""Import job orchestration: quarantine, parse, preview, decide, atomic commit, cleanup.

Follows plan 11.8 (Stage A-G). Commit revalidates duplicates and suppression against
current state so a DNC entry created after preview is still honored (invariant 8), and
is idempotent on (uploader, idempotency_key) (invariant 6).
"""

from __future__ import annotations

import itertools
import uuid
from collections.abc import Iterable
from datetime import timedelta
from pathlib import PurePosixPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.config import get_settings
from app.db_locks import lock_phone_fingerprint
from app.imports import classify, parser, storage, validators
from app.imports.parser import ParseLimitExceeded
from app.models.base import utcnow
from app.models.campaign import Campaign
from app.models.contact import CampaignContact, Contact
from app.models.imports import ImportDecision, ImportJob, ImportRow
from app.models.work import WorkItem

_PARSE_BATCH_SIZE = 500


class ImportError_(Exception):
    """Base class for import pipeline errors (avoids shadowing the builtin name)."""


class UploadRejected(ImportError_):
    pass


class ImportNotReady(ImportError_):
    pass


class StaleDecisionVersion(ImportError_):
    pass


class MissingProvenance(ImportError_):
    pass


def _truncate_filename_preserving_extension(display_filename: str, max_len: int = 255) -> str:
    """Truncate a display filename to max_len while keeping its extension intact,
    so extension checks stay consistent between upload time and parse time."""
    if len(display_filename) <= max_len:
        return display_filename
    parts = PurePosixPath(display_filename)
    suffix = parts.suffix
    keep = max(0, max_len - len(suffix))
    return parts.stem[:keep] + suffix


def create_import_job(
    db: Session,
    *,
    campaign: Campaign,
    uploader_id: uuid.UUID,
    display_filename: str,
    file_chunks: Iterable[bytes],
) -> ImportJob:
    settings = get_settings()
    ext = validators.check_extension(display_filename)  # cheap check before writing anything
    stored_filename = _truncate_filename_preserving_extension(display_filename)
    storage_key = storage.generate_storage_key()
    try:
        size, file_hash = storage.write_streamed(
            storage_key, file_chunks, settings.upload_max_bytes
        )
        validators.validate_upload(storage.path_for(storage_key), display_filename)
    except (storage.UploadTooLarge, validators.UploadValidationError) as exc:
        storage.delete(storage_key)
        raise UploadRejected(str(exc)) from exc
    except Exception:
        storage.delete(storage_key)
        raise

    job = ImportJob(
        campaign_id=campaign.id,
        uploader_id=uploader_id,
        source_filename_display=stored_filename,
        generated_storage_key=storage_key,
        file_hash=file_hash,
        state="quarantined",
        expires_at=utcnow() + timedelta(hours=settings.import_expiry_hours),
    )
    db.add(job)
    db.flush()
    record_audit(
        db,
        action="import.upload",
        result="success",
        actor_user_id=uploader_id,
        target_type="import_job",
        target_id=job.id,
        event_metadata={"file_hash": file_hash, "size": size, "extension": ext},
    )
    return job


def parse_job(
    db: Session,
    job_id: uuid.UUID,
    *,
    phone_column: str,
    name_column: str | None,
    metadata_columns: list[str],
) -> None:
    job = db.execute(
        select(ImportJob).where(ImportJob.id == job_id).with_for_update()
    ).scalar_one_or_none()
    if job is None or job.state != "quarantined":
        return

    job.state = "parsing"
    db.flush()

    campaign = db.get(Campaign, job.campaign_id)
    if campaign is None:
        job.state = "failed"
        job.error_summary = "campaign for import job no longer exists"
        record_audit(
            db,
            action="import.parse",
            result="failure",
            actor_user_id=job.uploader_id,
            target_type="import_job",
            target_id=job.id,
            reason_code="campaign_missing",
        )
        return
    ext = validators.check_extension(job.source_filename_display)
    path = storage.path_for(job.generated_storage_key)

    total = valid = invalid = duplicates = suppressed = 0
    seen_fingerprints: set[str] = set()
    batch: list[ImportRow] = []

    try:
        rows_iter = parser.parse_file(path, ext)
        first_row = next(rows_iter, None)
        if first_row is not None and phone_column not in first_row.values:
            raise ParseLimitExceeded(f"phone column '{phone_column}' not found in file header")
        all_rows = itertools.chain([first_row], rows_iter) if first_row is not None else ()

        for row in all_rows:
            total += 1
            result = classify.classify_row(
                row,
                db=db,
                campaign_id=campaign.id,
                phone_column=phone_column,
                default_region=campaign.default_region,
                name_column=name_column,
                metadata_columns=metadata_columns,
                seen_fingerprints=seen_fingerprints,
            )
            batch.append(
                ImportRow(
                    import_job_id=job.id,
                    row_number=result.row_number,
                    raw_phone_protected=result.raw_phone_protected,
                    phone_fingerprint=result.phone_fingerprint,
                    canonical_values=result.canonical_values,
                    validation_result=result.validation_result,
                    validation_detail=result.validation_detail,
                    duplicate_category=result.duplicate_category,
                    suppression_match=result.suppression_match,
                )
            )
            if result.validation_result == "invalid":
                invalid += 1
            elif result.duplicate_category:
                duplicates += 1
            else:
                valid += 1
                if result.suppression_match:
                    suppressed += 1

            if len(batch) >= _PARSE_BATCH_SIZE:
                db.add_all(batch)
                db.flush()
                batch.clear()

        if batch:
            db.add_all(batch)
            db.flush()

    except (ParseLimitExceeded, UnicodeDecodeError, ValueError) as exc:
        job.state = "failed"
        job.error_summary = str(exc)[:1000]
        record_audit(
            db, action="import.parse", result="failure", actor_user_id=job.uploader_id,
            target_type="import_job", target_id=job.id, reason_code=str(exc)[:100],
        )
        return

    job.total_rows = total
    job.valid_rows = valid
    job.invalid_rows = invalid
    job.duplicate_rows = duplicates
    job.suppression_hits = suppressed
    job.state = "parsed"
    record_audit(
        db, action="import.parse", result="success", actor_user_id=job.uploader_id,
        target_type="import_job", target_id=job.id,
        event_metadata={"total": total, "valid": valid, "invalid": invalid},
    )


def get_preview(db: Session, job: ImportJob) -> list[ImportRow]:
    return list(
        db.scalars(
            select(ImportRow)
            .where(ImportRow.import_job_id == job.id, ImportRow.validation_result == "invalid")
            .order_by(ImportRow.row_number)
            .limit(5)
        )
    )


def record_decision(
    db: Session, job: ImportJob, *, decided_by: uuid.UUID, decision: str, note: str | None
) -> ImportDecision:
    if decision not in ("approve", "reject", "cancel"):
        raise ValueError("decision must be approve, reject, or cancel")
    job.decision_version += 1
    row = ImportDecision(
        import_job_id=job.id,
        decision_version=job.decision_version,
        decision=decision,
        decided_by=decided_by,
        note=note,
    )
    db.add(row)
    db.flush()
    record_audit(
        db, action="import.decide", result="success", actor_user_id=decided_by,
        target_type="import_job", target_id=job.id, reason_code=decision,
    )
    return row


def _latest_decision(db: Session, job: ImportJob) -> ImportDecision | None:
    return db.scalar(
        select(ImportDecision)
        .where(
            ImportDecision.import_job_id == job.id,
            ImportDecision.decision_version == job.decision_version,
        )
        .order_by(ImportDecision.created_at.desc())
        .limit(1)
    )


def _assert_provenance(campaign: Campaign) -> None:
    required = {
        "data_source": campaign.data_source,
        "purpose": campaign.purpose,
        "data_obtained_at": campaign.data_obtained_at,
        "lawful_basis_or_consent_reference": campaign.lawful_basis_or_consent_reference,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise MissingProvenance(f"missing required provenance fields: {', '.join(missing)}")


def commit_job(
    db: Session,
    job_id: uuid.UUID,
    *,
    actor_id: uuid.UUID,
    decision_version: int,
    idempotency_key: str,
) -> dict:
    job = db.execute(
        select(ImportJob).where(ImportJob.id == job_id).with_for_update()
    ).scalar_one_or_none()
    if job is None:
        raise ImportNotReady("import job not found")

    if job.state == "committed":
        if job.idempotency_key == idempotency_key:
            return job.committed_result or {"job_id": str(job.id), "inserted": 0, "suppressed": 0}
        raise ImportNotReady("import already committed under a different idempotency key")

    if job.state != "parsed":
        raise ImportNotReady(f"import job is not ready to commit (state={job.state})")

    if job.decision_version != decision_version:
        raise StaleDecisionVersion("decision version is stale; re-review the preview")

    latest = _latest_decision(db, job)
    if latest is None or latest.decision != "approve":
        raise ImportNotReady("import has not been approved for commit")

    campaign = db.execute(
        select(Campaign).where(Campaign.id == job.campaign_id).with_for_update()
    ).scalar_one()
    _assert_provenance(campaign)

    settings = get_settings()
    rows = db.scalars(
        select(ImportRow).where(
            ImportRow.import_job_id == job.id,
            ImportRow.validation_result == "valid",
            ImportRow.duplicate_category.is_(None),
        ).order_by(ImportRow.phone_fingerprint.asc(), ImportRow.row_number.asc())
    )

    now = utcnow()
    inserted = 0
    suppressed_count = 0

    for row in rows:
        if row.phone_fingerprint is None or row.raw_phone_protected is None:
            continue

        # This lock also guards the absence of a suppression row. DNC creation and
        # leasing use the same key, making the check and work-item creation atomic
        # with respect to those transactions.
        lock_phone_fingerprint(db, row.phone_fingerprint)
        contact = db.scalar(
            select(Contact).where(Contact.phone_fingerprint == row.phone_fingerprint)
        )
        if contact is None:
            contact = Contact(
                phone_ciphertext=row.raw_phone_protected,
                phone_fingerprint=row.phone_fingerprint,
                phone_key_version=settings.phone_fingerprint_key_version,
            )
            db.add(contact)
            db.flush()
        elif classify.already_in_campaign(db, campaign.id, contact.id):
            # Became a duplicate since preview (e.g. another job committed first); skip.
            continue

        # Revalidate suppression against CURRENT state, not the parse-time snapshot.
        currently_suppressed = classify.is_suppressed(db, row.phone_fingerprint)
        status = "suppressed" if currently_suppressed else "queued"

        campaign_contact = CampaignContact(
            campaign_id=campaign.id,
            contact_id=contact.id,
            original_phone_protected=row.raw_phone_protected,
            campaign_name_value=(row.canonical_values or {}).get("name"),
            approved_metadata=row.canonical_values,
            source_row_reference=str(row.row_number),
            status=status,
            imported_at=now,
        )
        db.add(campaign_contact)
        db.flush()
        inserted += 1

        if currently_suppressed:
            suppressed_count += 1
        else:
            db.add(WorkItem(campaign_contact_id=campaign_contact.id, state="queued", priority=0))

    result = {"job_id": str(job.id), "inserted": inserted, "suppressed": suppressed_count}
    job.state = "committed"
    job.committed_at = now
    job.idempotency_key = idempotency_key
    job.committed_result = result

    record_audit(
        db, action="import.commit", result="success", actor_user_id=actor_id,
        target_type="import_job", target_id=job.id, event_metadata=result,
    )

    return result


def cleanup_committed_source(job: ImportJob) -> None:
    """Delete staged input only after the caller has committed the database result."""
    storage.delete(job.generated_storage_key)


def cleanup_expired_jobs(db: Session) -> int:
    """Delete expired staging and retry source cleanup for committed jobs."""
    now = utcnow()
    expired = db.scalars(
        select(ImportJob).where(
            ImportJob.expires_at < now,
        )
    )
    count = 0
    for job in expired:
        storage.delete(job.generated_storage_key)
        if job.state == "committed":
            # A committed job is retained for audit and idempotent replay, but a
            # successful source deletion must not stay in the hourly retry set.
            job.expires_at = None
        else:
            db.query(ImportRow).filter(ImportRow.import_job_id == job.id).delete()
            job.state = "expired"
        count += 1
    return count
