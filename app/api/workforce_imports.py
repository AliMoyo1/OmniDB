"""Staged bulk-workforce import API (/api/v1/workforce/imports).

Thin HTTP layer over app/workforce_imports/service.py - the two-person high-risk
approval rule and per-row authority checks live in the service layer itself
(PHASE-4B-PLAN.md), not here, so this file only ever narrows access further.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Generator

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_csrf
from app.authz import service as authz
from app.db import get_session
from app.flags.service import FeatureDisabledError
from app.models.identity import User
from app.models.workforce_imports import WorkforceImportJob
from app.workforce_imports import service as import_service
from app.workforce_imports.schemas import (
    InvalidExample,
    WorkforceImportCommitOut,
    WorkforceImportCommitRequest,
    WorkforceImportDecisionOut,
    WorkforceImportDecisionRequest,
    WorkforceImportJobOut,
    WorkforceImportPreviewOut,
    WorkforceImportReverseOut,
)
from app.workforce_imports.service import (
    ImportNotReady,
    InsufficientApprovalAuthority,
    SelfApproval,
    StaleDecisionVersion,
    UnknownImportType,
    UnresolvedBlockingErrors,
    UploadRejected,
    WarningsNotAcknowledged,
)
from app.workforce_imports.tasks import parse_workforce_import_job_task

router = APIRouter(prefix="/api/v1/workforce/imports", tags=["workforce-imports"])
_logger = logging.getLogger(__name__)
_UPLOAD_CHUNK_SIZE = 65536


def _require_any_appointment_capability(
    db: Session = Depends(get_session), user: User = Depends(get_current_user)
) -> User:
    if not any(
        authz.has_assigned_capability(db, user.id, capability)
        for capability in import_service.UPLOAD_CAPABILITIES
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not authorized")
    return user


def _job_out(job: WorkforceImportJob) -> WorkforceImportJobOut:
    return WorkforceImportJobOut(
        id=str(job.id), import_type=job.import_type, state=job.state,
        source_filename_display=job.source_filename_display, total_rows=job.total_rows,
        valid_rows=job.valid_rows, warning_rows=job.warning_rows, invalid_rows=job.invalid_rows,
        high_risk_rows=job.high_risk_rows, decision_version=job.decision_version,
        created_at=job.created_at, committed_at=job.committed_at, reversed_at=job.reversed_at,
    )


def _load_job_or_404(db: Session, import_id: uuid.UUID) -> WorkforceImportJob:
    job = db.get(WorkforceImportJob, import_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "import job not found")
    return job


def _load_accessible_job_or_404(
    db: Session, import_id: uuid.UUID, actor_id: uuid.UUID
) -> WorkforceImportJob:
    job = db.get(WorkforceImportJob, import_id)
    if job is None or not import_service.can_access_job(db, actor_id, job):
        # Same 404 either way - a job outside the caller's scope should not be
        # distinguishable from one that doesn't exist (finding #1).
        raise HTTPException(status.HTTP_404_NOT_FOUND, "import job not found")
    return job


def _read_chunks(fileobj) -> Generator[bytes, None, None]:
    while True:
        chunk = fileobj.read(_UPLOAD_CHUNK_SIZE)
        if not chunk:
            break
        yield chunk


@router.post("", response_model=WorkforceImportJobOut, dependencies=[Depends(require_csrf)])
def upload_workforce_import(
    import_type: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_session),
    user: User = Depends(_require_any_appointment_capability),
) -> WorkforceImportJobOut:
    try:
        job = import_service.create_import_job(
            db, import_type=import_type, uploader_id=user.id,
            display_filename=file.filename or "upload", file_chunks=_read_chunks(file.file),
        )
    except (UploadRejected, UnknownImportType) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    except FeatureDisabledError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    db.commit()
    parse_workforce_import_job_task.delay(str(job.id))
    return _job_out(job)


@router.get("/{import_id}", response_model=WorkforceImportJobOut)
def get_workforce_import(
    import_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: User = Depends(_require_any_appointment_capability),
) -> WorkforceImportJobOut:
    return _job_out(_load_accessible_job_or_404(db, import_id, user.id))


@router.get("/{import_id}/preview", response_model=WorkforceImportPreviewOut)
def preview_workforce_import(
    import_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: User = Depends(_require_any_appointment_capability),
) -> WorkforceImportPreviewOut:
    job = _load_accessible_job_or_404(db, import_id, user.id)
    examples = import_service.get_preview(db, job)
    return WorkforceImportPreviewOut(
        job=_job_out(job),
        invalid_examples=[
            InvalidExample(row_number=r.row_number, detail=r.validation_detail,
                            conflict_type=r.conflict_type)
            for r in examples
        ],
    )


@router.patch(
    "/{import_id}/decisions",
    response_model=WorkforceImportDecisionOut,
    dependencies=[Depends(require_csrf)],
)
def decide_workforce_import(
    import_id: uuid.UUID,
    payload: WorkforceImportDecisionRequest,
    db: Session = Depends(get_session),
    user: User = Depends(_require_any_appointment_capability),
) -> WorkforceImportDecisionOut:
    try:
        decision = import_service.record_decision(
            db, import_id, decided_by=user.id, decision=payload.decision,
            decision_tier=payload.decision_tier, note=payload.note,
            acknowledge_warnings=payload.acknowledge_warnings,
        )
    except ImportNotReady as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    except SelfApproval as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from None
    except InsufficientApprovalAuthority as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from None
    except (UnresolvedBlockingErrors, WarningsNotAcknowledged) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    db.commit()
    return WorkforceImportDecisionOut(
        decision_version=decision.decision_version, decision=decision.decision,
        decision_tier=decision.decision_tier,
    )


@router.post(
    "/{import_id}/commit", response_model=WorkforceImportCommitOut,
    dependencies=[Depends(require_csrf)],
)
def commit_workforce_import(
    import_id: uuid.UUID,
    payload: WorkforceImportCommitRequest,
    db: Session = Depends(get_session),
    user: User = Depends(_require_any_appointment_capability),
) -> WorkforceImportCommitOut:
    job = _load_job_or_404(db, import_id)
    try:
        result = import_service.commit_job(
            db, job.id, actor_id=user.id, decision_version=payload.decision_version,
            idempotency_key=payload.idempotency_key,
        )
    except StaleDecisionVersion as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, {"code": "stale_version", "message": str(exc)}
        ) from None
    except InsufficientApprovalAuthority as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, {"code": "approval_authority_changed", "message": str(exc)}
        ) from None
    except ImportNotReady as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, {"code": "approval_required", "message": str(exc)}
        ) from None
    except UnresolvedBlockingErrors as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"code": "blocking_errors_unresolved", "message": str(exc)},
        ) from None
    db.commit()
    try:
        import_service.cleanup_committed_source(job)
    except OSError:
        _logger.exception("Committed workforce import source cleanup failed for job %s", job.id)
    return WorkforceImportCommitOut(**result)


@router.post(
    "/{import_id}/reverse", response_model=WorkforceImportReverseOut,
    dependencies=[Depends(require_csrf)],
)
def reverse_workforce_import(
    import_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: User = Depends(_require_any_appointment_capability),
) -> WorkforceImportReverseOut:
    job = _load_job_or_404(db, import_id)
    try:
        result = import_service.reverse_job(db, job.id, actor_id=user.id)
    except (SelfApproval, InsufficientApprovalAuthority) as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from None
    except ImportNotReady as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    db.commit()
    return WorkforceImportReverseOut(**result)
