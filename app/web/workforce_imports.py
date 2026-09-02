"""Server-rendered bulk-workforce import wizard (/workforce/imports).

Thin browser layer over app/workforce_imports/service.py, same relationship
app/web/workforce.py has to app/workforce/service.py - every action here calls
the same, already-tested service functions the JSON API uses.
"""

from __future__ import annotations

import uuid
from collections.abc import Generator
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.authz import service as authz
from app.db import get_session
from app.flags.service import FeatureDisabledError
from app.models.identity import User
from app.models.workforce_imports import WorkforceImportJob
from app.web.dependencies import require_page_user, verify_form_csrf
from app.web.templates import page_context, templates
from app.workforce_imports import service as import_service
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

router = APIRouter(prefix="/workforce/imports", tags=["web-workforce-imports"])
_UPLOAD_CHUNK_SIZE = 65536

_TEMPLATES: dict[str, str] = {
    "users": (
        "action,external_workforce_id,login_identifier,display_name,start_date,end_date\r\n"
    ),
    "explicit_deactivations": "external_workforce_id,reason_code\r\n",
    "team_memberships": "action,external_workforce_id,team_code,reason_code\r\n",
    "role_assignments": (
        "action,external_workforce_id,role_code,scope_type,scope_code,reason_code\r\n"
    ),
    "reporting_assignments": "external_workforce_id,supervisor_workforce_id,reason_code\r\n",
    "campaign_user_assignments": (
        "action,external_workforce_id,campaign_code,team_code,reason_code\r\n"
    ),
}


def _any_appointment_capability(db: Session, user: User) -> bool:
    return any(
        authz.has_assigned_capability(db, user.id, capability)
        for capability in import_service.UPLOAD_CAPABILITIES
    )


def _redirect(path: str, *, success: str | None = None, error: str | None = None):
    params = {
        key: value for key, value in (("flash_success", success), ("flash_error", error)) if value
    }
    return RedirectResponse(path + ("?" + urlencode(params) if params else ""), status_code=303)


def _index_redirect(*, error: str):
    return _redirect("/workforce/imports", error=error)


def _job_redirect(job_id: uuid.UUID, *, success: str | None = None, error: str | None = None):
    return _redirect(f"/workforce/imports/{job_id}", success=success, error=error)


def _read_chunks(fileobj) -> Generator[bytes, None, None]:
    while True:
        chunk = fileobj.read(_UPLOAD_CHUNK_SIZE)
        if not chunk:
            break
        yield chunk


@router.get("/templates/{import_type}.csv")
def download_template(import_type: str, user: User = Depends(require_page_user)) -> Response:
    body = _TEMPLATES.get(import_type)
    if body is None:
        return Response("unknown import type", status_code=404)
    return Response(
        body, media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="workforce-{import_type}-template-v1.csv"'
        },
    )


@router.get("")
def imports_list(
    request: Request,
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
):
    if not _any_appointment_capability(db, user):
        return RedirectResponse("/dashboard?flash_error=Not+authorized+for+workforce+imports.", 303)
    jobs = import_service.visible_jobs(db, user.id)
    context = page_context(
        request, db, user,
        active_section="workforce_imports",
        import_jobs=jobs,
        import_types=import_service.IMPORT_TYPES,
        flash_error=request.query_params.get("flash_error"),
        flash_success=request.query_params.get("flash_success"),
    )
    return templates.TemplateResponse(request, "workforce_imports_list.html", context)


@router.post("", dependencies=[Depends(verify_form_csrf)])
def upload_workforce_import(
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
    import_type: str = Form(...),
    file: UploadFile = File(...),
):
    if not _any_appointment_capability(db, user):
        return _index_redirect(error="Not authorized for workforce imports.")
    try:
        job = import_service.create_import_job(
            db, import_type=import_type, uploader_id=user.id,
            display_filename=file.filename or "upload", file_chunks=_read_chunks(file.file),
        )
    except (UploadRejected, UnknownImportType, FeatureDisabledError) as exc:
        db.rollback()
        return _index_redirect(error=str(exc))
    db.commit()
    parse_workforce_import_job_task.delay(str(job.id))
    return _job_redirect(job.id, success="Import received. Refresh to see its validation result.")


@router.get("/{job_id}")
def import_detail(
    job_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
):
    if not _any_appointment_capability(db, user):
        return RedirectResponse("/dashboard?flash_error=Not+authorized+for+workforce+imports.", 303)
    job = db.get(WorkforceImportJob, job_id)
    if job is None or not import_service.can_access_job(db, user.id, job):
        # Same message either way - a job outside the viewer's scope should not
        # be distinguishable from one that doesn't exist (finding #1).
        return _index_redirect(error="Import job not found.")
    decisions = import_service.current_decisions(db, job)
    decider_ids = {d.decided_by for d in decisions.values() if d is not None}
    deciders = {
        u.id: u for u in db.scalars(select(User).where(User.id.in_(decider_ids)))
    } if decider_ids else {}
    context = page_context(
        request, db, user,
        active_section="workforce_imports",
        job=job,
        invalid_examples=import_service.get_preview(db, job),
        decisions=decisions,
        deciders=deciders,
        idempotency_key=str(uuid.uuid4()),
        flash_error=request.query_params.get("flash_error"),
        flash_success=request.query_params.get("flash_success"),
    )
    return templates.TemplateResponse(request, "workforce_import_detail.html", context)


@router.post("/{job_id}/decisions", dependencies=[Depends(verify_form_csrf)])
def decide_workforce_import(
    job_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
    decision: str = Form(...),
    decision_tier: str = Form(...),
    note: str = Form(""),
    acknowledge_warnings: bool = Form(False),
):
    if not _any_appointment_capability(db, user):
        return _index_redirect(error="Not authorized for workforce imports.")
    try:
        import_service.record_decision(
            db, job_id, decided_by=user.id, decision=decision, decision_tier=decision_tier,
            note=note.strip() or None, acknowledge_warnings=acknowledge_warnings,
        )
    except ImportNotReady:
        return _index_redirect(error="Import job not found.")
    except SelfApproval:
        db.rollback()
        return _job_redirect(
            job_id, error="You uploaded this import; a different approver is required."
        )
    except InsufficientApprovalAuthority as exc:
        db.rollback()
        return _job_redirect(job_id, error=str(exc))
    except (UnresolvedBlockingErrors, WarningsNotAcknowledged) as exc:
        db.rollback()
        return _job_redirect(job_id, error=str(exc))
    except ValueError as exc:
        db.rollback()
        return _job_redirect(job_id, error=str(exc))
    db.commit()
    return _job_redirect(job_id, success="Decision recorded.")


@router.post("/{job_id}/commit", dependencies=[Depends(verify_form_csrf)])
def commit_workforce_import(
    job_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
    decision_version: int = Form(...),
    idempotency_key: str = Form(...),
):
    if not _any_appointment_capability(db, user):
        return _index_redirect(error="Not authorized for workforce imports.")
    job = db.get(WorkforceImportJob, job_id)
    if job is None:
        return _index_redirect(error="Import job not found.")
    try:
        result = import_service.commit_job(
            db, job.id, actor_id=user.id, decision_version=decision_version,
            idempotency_key=idempotency_key,
        )
    except (
        ImportNotReady,
        StaleDecisionVersion,
        InsufficientApprovalAuthority,
        UnresolvedBlockingErrors,
    ) as exc:
        db.rollback()
        return _job_redirect(job_id, error=str(exc))
    db.commit()
    try:
        import_service.cleanup_committed_source(job)
    except OSError:
        pass
    if not result["activation_tokens"]:
        return _job_redirect(job_id, success="Import committed.")
    # Rendered directly, not redirected: one-time secrets do not belong in a URL
    # query string even for a moment (same reasoning as user_created.html).
    context = page_context(
        request, db, user, active_section="workforce_imports", job=job, commit_result=result,
    )
    return templates.TemplateResponse(request, "workforce_import_committed.html", context)


@router.post("/{job_id}/reverse", dependencies=[Depends(verify_form_csrf)])
def reverse_workforce_import(
    job_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
):
    if not _any_appointment_capability(db, user):
        return _index_redirect(error="Not authorized for workforce imports.")
    job = db.get(WorkforceImportJob, job_id)
    if job is None:
        return _index_redirect(error="Import job not found.")
    try:
        result = import_service.reverse_job(db, job.id, actor_id=user.id)
    except (SelfApproval, InsufficientApprovalAuthority) as exc:
        db.rollback()
        return _job_redirect(job_id, error=str(exc))
    except ImportNotReady as exc:
        db.rollback()
        return _job_redirect(job_id, error=str(exc))
    db.commit()
    return _job_redirect(
        job_id,
        success=f"Reversal complete: {len(result['reversed'])} reverted, "
                f"{len(result['skipped'])} left unchanged (conflicting).",
    )
