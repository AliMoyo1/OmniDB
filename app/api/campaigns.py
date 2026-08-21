"""Campaign and import API (/api/v1/campaigns, /api/v1/imports)."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Generator

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_csrf
from app.authz import service as authz
from app.authz.capabilities import (
    ARCHIVE_CAMPAIGN,
    CREATE_CAMPAIGN,
    LAUNCH_CAMPAIGN,
    MANAGE_CAMPAIGN,
    PAUSE_CAMPAIGN,
    VIEW_CAMPAIGN,
)
from app.campaigns import service as campaign_service
from app.campaigns.schemas import (
    CampaignCreateRequest,
    CampaignOut,
    CampaignUpdateRequest,
    DispositionCreateRequest,
    DispositionOut,
)
from app.campaigns.service import CampaignStateError, DispositionPolicyError
from app.db import get_session
from app.imports import service as import_service
from app.imports.schemas import (
    ImportCommitOut,
    ImportCommitRequest,
    ImportDecisionOut,
    ImportDecisionRequest,
    ImportJobOut,
    ImportPreviewOut,
    InvalidExample,
)
from app.imports.service import (
    ImportNotReady,
    MissingProvenance,
    StaleDecisionVersion,
    UploadRejected,
)
from app.imports.tasks import parse_import_job_task
from app.models.campaign import Campaign
from app.models.identity import Organization, Team, User
from app.models.imports import ImportJob

campaigns_router = APIRouter(prefix="/api/v1/campaigns", tags=["campaigns"])
imports_router = APIRouter(prefix="/api/v1/imports", tags=["imports"])

_UPLOAD_CHUNK_SIZE = 65536
_logger = logging.getLogger(__name__)


def _load_campaign_or_404(db: Session, campaign_id: uuid.UUID) -> Campaign:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "campaign not found")
    return campaign


def _require_campaign_capability(
    db: Session, user: User, capability: str, campaign: Campaign
) -> None:
    if not authz.has_campaign_capability(db, user.id, capability, campaign.id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not authorized")


def _campaign_out(c: Campaign) -> CampaignOut:
    return CampaignOut(
        id=str(c.id), name=c.name, description=c.description, status=c.status,
        default_region=c.default_region, timezone=c.timezone, purpose=c.purpose,
        data_source=c.data_source, data_obtained_at=c.data_obtained_at,
        lawful_basis_or_consent_reference=c.lawful_basis_or_consent_reference,
        created_at=c.created_at, launched_at=c.launched_at, archived_at=c.archived_at,
    )


def _job_out(job: ImportJob) -> ImportJobOut:
    return ImportJobOut(
        id=str(job.id), campaign_id=str(job.campaign_id), state=job.state,
        source_filename_display=job.source_filename_display, total_rows=job.total_rows,
        valid_rows=job.valid_rows, invalid_rows=job.invalid_rows,
        duplicate_rows=job.duplicate_rows, suppression_hits=job.suppression_hits,
        decision_version=job.decision_version, created_at=job.created_at,
        committed_at=job.committed_at,
    )


@campaigns_router.post("", response_model=CampaignOut, dependencies=[Depends(require_csrf)])
def create_campaign(
    payload: CampaignCreateRequest,
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> CampaignOut:
    owning_scope_id = payload.owning_scope_id
    if not authz.has_scope_capability(
        db,
        user.id,
        CREATE_CAMPAIGN,
        scope_type=payload.owning_scope_type,
        scope_id=owning_scope_id,
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not authorized")
    if payload.owning_scope_type == "team":
        team = db.get(Team, owning_scope_id)
        if team is None or team.status != "active":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid owning team")
    elif owning_scope_id is not None:
        organization = db.get(Organization, owning_scope_id)
        if organization is None or organization.status != "active":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid owning organization")
    campaign = campaign_service.create_campaign(
        db, created_by=user.id, name=payload.name, description=payload.description,
        owning_scope_type=payload.owning_scope_type, owning_scope_id=owning_scope_id,
        default_region=payload.default_region, timezone=payload.timezone,
        purpose=payload.purpose, data_source=payload.data_source,
        data_obtained_at=payload.data_obtained_at,
        lawful_basis_or_consent_reference=payload.lawful_basis_or_consent_reference,
    )
    db.commit()
    return _campaign_out(campaign)


@campaigns_router.get("", response_model=list[CampaignOut])
def list_campaigns(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> list[CampaignOut]:
    if not authz.has_assigned_capability(db, user.id, VIEW_CAMPAIGN):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not authorized")
    rows = db.scalars(
        select(Campaign)
        .where(authz.campaign_scope_filter(db, user.id, VIEW_CAMPAIGN))
        .order_by(Campaign.created_at.desc())
        .limit(limit)
    )
    return [_campaign_out(c) for c in rows]


@campaigns_router.get("/{campaign_id}", response_model=CampaignOut)
def get_campaign(
    campaign_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> CampaignOut:
    campaign = _load_campaign_or_404(db, campaign_id)
    _require_campaign_capability(db, user, VIEW_CAMPAIGN, campaign)
    return _campaign_out(campaign)


@campaigns_router.patch(
    "/{campaign_id}", response_model=CampaignOut, dependencies=[Depends(require_csrf)]
)
def update_campaign(
    campaign_id: uuid.UUID,
    payload: CampaignUpdateRequest,
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> CampaignOut:
    campaign = _load_campaign_or_404(db, campaign_id)
    _require_campaign_capability(db, user, MANAGE_CAMPAIGN, campaign)
    try:
        campaign_service.update_campaign(
            db, campaign, actor_id=user.id, **payload.model_dump(exclude_unset=True)
        )
    except CampaignStateError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    db.commit()
    return _campaign_out(campaign)


@campaigns_router.post(
    "/{campaign_id}/launch", response_model=CampaignOut, dependencies=[Depends(require_csrf)]
)
def launch_campaign(
    campaign_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> CampaignOut:
    campaign = _load_campaign_or_404(db, campaign_id)
    _require_campaign_capability(db, user, LAUNCH_CAMPAIGN, campaign)
    try:
        campaign_service.launch_campaign(db, campaign, actor_id=user.id)
    except CampaignStateError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    db.commit()
    return _campaign_out(campaign)


@campaigns_router.post(
    "/{campaign_id}/pause", response_model=CampaignOut, dependencies=[Depends(require_csrf)]
)
def pause_campaign(
    campaign_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> CampaignOut:
    campaign = _load_campaign_or_404(db, campaign_id)
    _require_campaign_capability(db, user, PAUSE_CAMPAIGN, campaign)
    try:
        campaign_service.pause_campaign(db, campaign, actor_id=user.id)
    except CampaignStateError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    db.commit()
    return _campaign_out(campaign)


@campaigns_router.post(
    "/{campaign_id}/archive", response_model=CampaignOut, dependencies=[Depends(require_csrf)]
)
def archive_campaign(
    campaign_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> CampaignOut:
    campaign = _load_campaign_or_404(db, campaign_id)
    _require_campaign_capability(db, user, ARCHIVE_CAMPAIGN, campaign)
    try:
        campaign_service.archive_campaign(db, campaign, actor_id=user.id)
    except CampaignStateError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    db.commit()
    return _campaign_out(campaign)


@campaigns_router.post(
    "/{campaign_id}/dispositions",
    response_model=DispositionOut,
    dependencies=[Depends(require_csrf)],
)
def create_disposition(
    campaign_id: uuid.UUID,
    payload: DispositionCreateRequest,
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> DispositionOut:
    campaign = _load_campaign_or_404(db, campaign_id)
    _require_campaign_capability(db, user, MANAGE_CAMPAIGN, campaign)
    try:
        disposition = campaign_service.create_disposition(
            db, campaign, actor_id=user.id, **payload.model_dump()
        )
    except DispositionPolicyError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    db.commit()
    return DispositionOut(
        id=str(disposition.id), label=disposition.label,
        stable_semantic_code=disposition.stable_semantic_code,
        causes_dnc=disposition.causes_dnc, active=disposition.active,
    )


def _read_chunks(fileobj) -> Generator[bytes, None, None]:
    while True:
        chunk = fileobj.read(_UPLOAD_CHUNK_SIZE)
        if not chunk:
            break
        yield chunk


@campaigns_router.post(
    "/{campaign_id}/imports", response_model=ImportJobOut, dependencies=[Depends(require_csrf)]
)
def upload_import(
    campaign_id: uuid.UUID,
    file: UploadFile = File(...),
    phone_column: str = Form(...),
    name_column: str | None = Form(None),
    metadata_columns: str = Form(""),
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> ImportJobOut:
    campaign = _load_campaign_or_404(db, campaign_id)
    _require_campaign_capability(db, user, MANAGE_CAMPAIGN, campaign)

    meta_cols = [c.strip() for c in metadata_columns.split(",") if c.strip()]
    try:
        job = import_service.create_import_job(
            db, campaign=campaign, uploader_id=user.id,
            display_filename=file.filename or "upload",
            file_chunks=_read_chunks(file.file),
        )
    except UploadRejected as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    db.commit()

    parse_import_job_task.delay(str(job.id), phone_column, name_column, meta_cols)
    return _job_out(job)


def _load_job_or_404(db: Session, import_id: uuid.UUID) -> ImportJob:
    job = db.get(ImportJob, import_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "import job not found")
    return job


def _require_job_capability(db: Session, user: User, capability: str, job: ImportJob) -> Campaign:
    campaign = _load_campaign_or_404(db, job.campaign_id)
    _require_campaign_capability(db, user, capability, campaign)
    return campaign


@imports_router.get("/{import_id}", response_model=ImportJobOut)
def get_import(
    import_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> ImportJobOut:
    job = _load_job_or_404(db, import_id)
    _require_job_capability(db, user, VIEW_CAMPAIGN, job)
    return _job_out(job)


@imports_router.get("/{import_id}/preview", response_model=ImportPreviewOut)
def preview_import(
    import_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> ImportPreviewOut:
    job = _load_job_or_404(db, import_id)
    _require_job_capability(db, user, MANAGE_CAMPAIGN, job)
    examples = import_service.get_preview(db, job)
    return ImportPreviewOut(
        job=_job_out(job),
        invalid_examples=[
            InvalidExample(row_number=r.row_number, detail=r.validation_detail) for r in examples
        ],
    )


@imports_router.patch(
    "/{import_id}/decisions", response_model=ImportDecisionOut, dependencies=[Depends(require_csrf)]
)
def decide_import(
    import_id: uuid.UUID,
    payload: ImportDecisionRequest,
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> ImportDecisionOut:
    job = _load_job_or_404(db, import_id)
    _require_job_capability(db, user, MANAGE_CAMPAIGN, job)
    try:
        decision = import_service.record_decision(
            db, job, decided_by=user.id, decision=payload.decision, note=payload.note
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    db.commit()
    return ImportDecisionOut(decision_version=decision.decision_version, decision=decision.decision)


@imports_router.post(
    "/{import_id}/commit", response_model=ImportCommitOut, dependencies=[Depends(require_csrf)]
)
def commit_import(
    import_id: uuid.UUID,
    payload: ImportCommitRequest,
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> ImportCommitOut:
    job = _load_job_or_404(db, import_id)
    _require_job_capability(db, user, MANAGE_CAMPAIGN, job)
    try:
        result = import_service.commit_job(
            db, job.id, actor_id=user.id,
            decision_version=payload.decision_version,
            idempotency_key=payload.idempotency_key,
        )
    except StaleDecisionVersion as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, {"code": "stale_version", "message": str(exc)}
        ) from None
    except MissingProvenance as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, {"code": "provenance_required", "message": str(exc)}
        ) from None
    except ImportNotReady as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, {"code": "approval_required", "message": str(exc)}
        ) from None
    db.commit()
    try:
        import_service.cleanup_committed_source(job)
    except OSError:
        # The business transaction is already durable. Retain a visible operational
        # error and let expiry cleanup retry instead of returning a false rollback.
        _logger.exception("Committed import source cleanup failed for job %s", job.id)
    return ImportCommitOut(**result)
