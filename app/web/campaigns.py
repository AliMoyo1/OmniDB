"""Server-rendered campaign operations for the pilot.

This module is intentionally a thin browser layer over the existing campaign and
import services. It never fetches or renders raw phone data: managers can see
import health, invalid-row reasons, and aggregate campaign totals, while contact
data remains protected by the existing import and work-lease boundaries.
"""

from __future__ import annotations

import uuid
from collections.abc import Generator
from datetime import date
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.authz import service as authz
from app.authz.capabilities import (
    ARCHIVE_CAMPAIGN,
    ASSIGN_CAMPAIGN_AGENT,
    CREATE_CAMPAIGN,
    LAUNCH_CAMPAIGN,
    MANAGE_CAMPAIGN,
    PAUSE_CAMPAIGN,
    ROLE_AGENT,
    VIEW_CAMPAIGN,
    VIEW_CAMPAIGN_REPORTS,
)
from app.campaigns import service as campaign_service
from app.campaigns.service import (
    CampaignAssignmentError,
    CampaignStateError,
    DispositionPolicyError,
)
from app.db import get_session
from app.flags.service import FeatureDisabledError
from app.imports import service as import_service
from app.imports.service import (
    ImportNotReady,
    MissingProvenance,
    StaleDecisionVersion,
    UploadRejected,
)
from app.imports.tasks import parse_import_job_task
from app.models.campaign import Campaign, CampaignDispositionDefinition, CampaignUserAssignment
from app.models.identity import User
from app.models.imports import ImportDecision, ImportJob
from app.reporting import campaign_stats
from app.web.dependencies import require_page_user, verify_form_csrf
from app.web.templates import page_context, templates
from app.workforce import service as workforce_service

router = APIRouter(prefix="/campaigns", tags=["web-campaigns"])

_DEFAULT_PROVENANCE = {
    "default_region": "ZW",
    "timezone": "Africa/Harare",
}
_UPLOAD_CHUNK_SIZE = 65_536


def _redirect(
    path: str,
    *,
    success: str | None = None,
    error: str | None = None,
    import_id: uuid.UUID | None = None,
) -> RedirectResponse:
    params = {
        key: value for key, value in (("flash_success", success), ("flash_error", error)) if value
    }
    if import_id is not None:
        params["import"] = str(import_id)
    return RedirectResponse(path + ("?" + urlencode(params) if params else ""), status_code=303)


def _index_redirect(*, error: str) -> RedirectResponse:
    return _redirect("/campaigns", error=error)


def _campaign_redirect(
    campaign_id: uuid.UUID,
    *,
    success: str | None = None,
    error: str | None = None,
    import_id: uuid.UUID | None = None,
) -> RedirectResponse:
    return _redirect(f"/campaigns/{campaign_id}", success=success, error=error, import_id=import_id)


def _can_access(db: Session, user: User, capability: str, campaign: Campaign) -> bool:
    return authz.has_campaign_capability(db, user.id, capability, campaign.id)


def _read_chunks(fileobj) -> Generator[bytes, None, None]:
    while True:
        chunk = fileobj.read(_UPLOAD_CHUNK_SIZE)
        if not chunk:
            return
        yield chunk


def _parse_uuid(value: str, *, field_name: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid user ID") from exc


@router.get("")
def campaign_list(
    request: Request,
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
):
    if not authz.has_assigned_capability(db, user.id, VIEW_CAMPAIGN):
        return RedirectResponse("/dashboard?flash_error=Not+authorized+for+campaigns.", 303)

    campaigns = list(
        db.scalars(
            select(Campaign)
            .where(authz.campaign_scope_filter(db, user.id, VIEW_CAMPAIGN))
            .order_by(Campaign.created_at.desc())
            .limit(100)
        )
    )
    cards = []
    for campaign in campaigns:
        stats = (
            campaign_stats.get_campaign_stats(db, campaign.id)
            if _can_access(db, user, VIEW_CAMPAIGN_REPORTS, campaign)
            else None
        )
        cards.append({"campaign": campaign, "stats": stats})

    context = page_context(
        request,
        db,
        user,
        active_section="campaigns",
        campaign_cards=cards,
        can_create_campaign=authz.has_scope_capability(
            db, user.id, CREATE_CAMPAIGN, scope_type="organization", scope_id=None
        ),
        default_provenance=_DEFAULT_PROVENANCE,
        flash_error=request.query_params.get("flash_error"),
        flash_success=request.query_params.get("flash_success"),
    )
    return templates.TemplateResponse(request, "campaign_list.html", context)


@router.post("", dependencies=[Depends(verify_form_csrf)])
def create_campaign(
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
    name: str = Form(...),
    purpose: str = Form(...),
    data_source: str = Form(...),
    data_obtained_at: str = Form(...),
    lawful_basis_or_consent_reference: str = Form(...),
    description: str = Form(""),
):
    if not authz.has_scope_capability(
        db, user.id, CREATE_CAMPAIGN, scope_type="organization", scope_id=None
    ):
        return _index_redirect(error="Not authorized to create a campaign.")
    try:
        obtained_at = date.fromisoformat(data_obtained_at)
    except ValueError:
        return _index_redirect(error="Data-obtained date must be a valid date.")
    campaign = campaign_service.create_campaign(
        db,
        created_by=user.id,
        name=name.strip(),
        description=description.strip() or None,
        owning_scope_type="organization",
        owning_scope_id=None,
        default_region=_DEFAULT_PROVENANCE["default_region"],
        timezone=_DEFAULT_PROVENANCE["timezone"],
        purpose=purpose.strip(),
        data_source=data_source.strip(),
        data_obtained_at=obtained_at,
        lawful_basis_or_consent_reference=lawful_basis_or_consent_reference.strip(),
    )
    db.commit()
    return _campaign_redirect(
        campaign.id, success="Campaign created. Add and approve data before launch."
    )


@router.get("/{campaign_id}")
def campaign_detail(
    campaign_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
):
    campaign = db.get(Campaign, campaign_id)
    if campaign is None or not _can_access(db, user, VIEW_CAMPAIGN, campaign):
        return _index_redirect(error="Campaign not found or not authorized.")

    can_manage = _can_access(db, user, MANAGE_CAMPAIGN, campaign)
    can_assign = _can_access(db, user, ASSIGN_CAMPAIGN_AGENT, campaign)
    import_jobs = list(
        db.scalars(
            select(ImportJob)
            .where(ImportJob.campaign_id == campaign.id)
            .order_by(ImportJob.created_at.desc())
            .limit(25)
        )
    )
    selected_id = request.query_params.get("import")
    selected_job = next((job for job in import_jobs if str(job.id) == selected_id), None)
    if selected_job is None and import_jobs:
        selected_job = import_jobs[0]

    latest_decisions: dict[uuid.UUID, ImportDecision] = {}
    if import_jobs:
        decisions = db.scalars(
            select(ImportDecision)
            .where(ImportDecision.import_job_id.in_([job.id for job in import_jobs]))
            .order_by(ImportDecision.import_job_id, ImportDecision.decision_version.desc())
        )
        for decision in decisions:
            latest_decisions.setdefault(decision.import_job_id, decision)

    assignments = list(
        db.execute(
            select(CampaignUserAssignment, User)
            .join(User, CampaignUserAssignment.user_id == User.id)
            .where(CampaignUserAssignment.campaign_id == campaign.id)
            .order_by(CampaignUserAssignment.created_at.desc())
        )
    )
    agents = []
    if can_assign:
        for candidate in workforce_service.list_visible_users(db, user.id, limit=100):
            if candidate.active and ROLE_AGENT in authz.effective_roles(db, candidate.id):
                agents.append(candidate)

    context = page_context(
        request,
        db,
        user,
        active_section="campaigns",
        campaign=campaign,
        stats=(
            campaign_stats.get_campaign_stats(db, campaign.id)
            if _can_access(db, user, VIEW_CAMPAIGN_REPORTS, campaign)
            else None
        ),
        can_manage_campaign=can_manage,
        can_assign_campaign=can_assign,
        can_launch_campaign=_can_access(db, user, LAUNCH_CAMPAIGN, campaign),
        can_pause_campaign=_can_access(db, user, PAUSE_CAMPAIGN, campaign),
        can_archive_campaign=_can_access(db, user, ARCHIVE_CAMPAIGN, campaign),
        import_jobs=import_jobs,
        selected_job=selected_job,
        selected_preview=(
            import_service.get_preview(db, selected_job) if selected_job and can_manage else []
        ),
        latest_decisions=latest_decisions,
        dispositions=list(
            db.scalars(
                select(CampaignDispositionDefinition)
                .where(CampaignDispositionDefinition.campaign_id == campaign.id)
                .order_by(
                    CampaignDispositionDefinition.display_order,
                    CampaignDispositionDefinition.label,
                )
            )
        ),
        assignments=assignments,
        available_agents=agents,
        idempotency_key=str(uuid.uuid4()),
        flash_error=request.query_params.get("flash_error"),
        flash_success=request.query_params.get("flash_success"),
    )
    return templates.TemplateResponse(request, "campaign_detail.html", context)


@router.post("/{campaign_id}/imports", dependencies=[Depends(verify_form_csrf)])
def upload_import(
    campaign_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
    file: UploadFile = File(...),
    phone_column: str = Form(...),
    name_column: str = Form(""),
    metadata_columns: str = Form(""),
):
    campaign = db.get(Campaign, campaign_id)
    if campaign is None or not _can_access(db, user, MANAGE_CAMPAIGN, campaign):
        return _index_redirect(error="Campaign not found or not authorized.")
    try:
        job = import_service.create_import_job(
            db,
            campaign=campaign,
            uploader_id=user.id,
            display_filename=file.filename or "upload",
            file_chunks=_read_chunks(file.file),
        )
    except (UploadRejected, FeatureDisabledError) as exc:
        return _campaign_redirect(campaign_id, error=str(exc))
    db.commit()
    metadata = [column.strip() for column in metadata_columns.split(",") if column.strip()]
    parse_import_job_task.delay(
        str(job.id), phone_column.strip(), name_column.strip() or None, metadata
    )
    return _campaign_redirect(
        campaign_id,
        success="Import received. Refresh this page to review its validation result.",
        import_id=job.id,
    )


@router.post(
    "/{campaign_id}/imports/{import_id}/decision", dependencies=[Depends(verify_form_csrf)]
)
def decide_import(
    campaign_id: uuid.UUID,
    import_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
    decision: str = Form(...),
    note: str = Form(""),
):
    campaign = db.get(Campaign, campaign_id)
    job = db.get(ImportJob, import_id)
    if campaign is None or job is None or job.campaign_id != campaign.id:
        return _campaign_redirect(campaign_id, error="Import job not found.")
    if not _can_access(db, user, MANAGE_CAMPAIGN, campaign):
        return _campaign_redirect(campaign_id, error="Not authorized to review this import.")
    try:
        import_service.record_decision(
            db, job, decided_by=user.id, decision=decision, note=note.strip() or None
        )
    except ValueError as exc:
        db.rollback()
        return _campaign_redirect(campaign_id, error=str(exc), import_id=job.id)
    db.commit()
    return _campaign_redirect(campaign_id, success="Import decision recorded.", import_id=job.id)


@router.post("/{campaign_id}/imports/{import_id}/commit", dependencies=[Depends(verify_form_csrf)])
def commit_import(
    campaign_id: uuid.UUID,
    import_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
    decision_version: int = Form(...),
    idempotency_key: str = Form(...),
):
    campaign = db.get(Campaign, campaign_id)
    job = db.get(ImportJob, import_id)
    if campaign is None or job is None or job.campaign_id != campaign.id:
        return _campaign_redirect(campaign_id, error="Import job not found.")
    if not _can_access(db, user, MANAGE_CAMPAIGN, campaign):
        return _campaign_redirect(campaign_id, error="Not authorized to commit this import.")
    try:
        result = import_service.commit_job(
            db,
            job.id,
            actor_id=user.id,
            decision_version=decision_version,
            idempotency_key=idempotency_key,
        )
    except (ImportNotReady, MissingProvenance, StaleDecisionVersion) as exc:
        db.rollback()
        return _campaign_redirect(campaign_id, error=str(exc), import_id=job.id)
    db.commit()
    try:
        import_service.cleanup_committed_source(job)
    except OSError:
        # Source cleanup is retried by the existing task. The durable commit must
        # still be reported accurately to the manager.
        pass
    return _campaign_redirect(
        campaign_id,
        success=(
            f"Import committed: {result['inserted']} contacts queued, "
            f"{result['suppressed']} suppressed."
        ),
        import_id=job.id,
    )


@router.post("/{campaign_id}/dispositions", dependencies=[Depends(verify_form_csrf)])
def create_disposition(
    campaign_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
    label: str = Form(...),
    stable_semantic_code: str = Form(...),
    next_action: str = Form("complete"),
    requires_notes: bool = Form(False),
    requires_callback_time: bool = Form(False),
    counts_as_connected: bool = Form(False),
    counts_as_conversion: bool = Form(False),
    causes_dnc: bool = Form(False),
):
    campaign = db.get(Campaign, campaign_id)
    if campaign is None or not _can_access(db, user, MANAGE_CAMPAIGN, campaign):
        return _campaign_redirect(campaign_id, error="Not authorized to configure dispositions.")
    try:
        campaign_service.create_disposition(
            db,
            campaign,
            actor_id=user.id,
            label=label.strip(),
            stable_semantic_code=stable_semantic_code.strip(),
            next_action=next_action or None,
            requires_notes=requires_notes,
            requires_callback_time=requires_callback_time,
            counts_as_connected=counts_as_connected,
            counts_as_conversion=counts_as_conversion,
            causes_dnc=causes_dnc,
        )
    except DispositionPolicyError as exc:
        db.rollback()
        return _campaign_redirect(campaign_id, error=str(exc))
    db.commit()
    return _campaign_redirect(campaign_id, success="Disposition added.")


@router.post("/{campaign_id}/assignments", dependencies=[Depends(verify_form_csrf)])
def assign_agent(
    campaign_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
    agent_id: str = Form(...),
    assignment_type: str = Form("primary"),
):
    campaign = db.get(Campaign, campaign_id)
    if campaign is None or not _can_access(db, user, ASSIGN_CAMPAIGN_AGENT, campaign):
        return _campaign_redirect(campaign_id, error="Not authorized to assign an agent.")
    try:
        campaign_service.assign_agent_to_campaign(
            db,
            campaign,
            agent_id=_parse_uuid(agent_id, field_name="Agent"),
            team_id=None,
            actor_id=user.id,
            assignment_type=assignment_type,
        )
    except (CampaignAssignmentError, ValueError) as exc:
        db.rollback()
        return _campaign_redirect(campaign_id, error=str(exc))
    db.commit()
    return _campaign_redirect(campaign_id, success="Agent assigned to campaign.")


@router.post("/{campaign_id}/lifecycle", dependencies=[Depends(verify_form_csrf)])
def campaign_lifecycle(
    campaign_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
    action: str = Form(...),
):
    campaign = db.get(Campaign, campaign_id)
    handlers = {
        "launch": (LAUNCH_CAMPAIGN, campaign_service.launch_campaign, "Campaign launched."),
        "pause": (PAUSE_CAMPAIGN, campaign_service.pause_campaign, "Campaign paused."),
        "archive": (ARCHIVE_CAMPAIGN, campaign_service.archive_campaign, "Campaign archived."),
    }
    selected = handlers.get(action)
    if campaign is None or selected is None:
        return _campaign_redirect(campaign_id, error="Campaign action not available.")
    capability, handler, success = selected
    if not _can_access(db, user, capability, campaign):
        return _campaign_redirect(campaign_id, error="Not authorized for that campaign action.")
    try:
        handler(db, campaign, actor_id=user.id)
    except (CampaignStateError, FeatureDisabledError) as exc:
        db.rollback()
        return _campaign_redirect(campaign_id, error=str(exc))
    db.commit()
    return _campaign_redirect(campaign_id, success=success)
