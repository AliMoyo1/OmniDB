"""Keyboard-first browser workflow for agents.

The page deliberately exposes only the currently leased contact. Callback lists
remain masked, and every mutation delegates to the same work service used by the
JSON API so authorization, DNC handling, idempotency, and lease rules cannot drift.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.authz import service as authz
from app.authz.capabilities import WORK_QUEUE
from app.db import get_session
from app.models.campaign import Campaign, CampaignDispositionDefinition
from app.models.identity import User
from app.reporting import agent_stats
from app.web.dependencies import require_page_user, verify_form_csrf
from app.web.templates import page_context, templates
from app.work import service as work_service
from app.work.service import (
    DispositionMismatch,
    IdempotencyConflict,
    LeaseConflict,
    MissingRequiredField,
    WorkItemError,
)

router = APIRouter(prefix="/agent/work", tags=["web-agent-work"])


def _redirect(*, success: str | None = None, error: str | None = None) -> RedirectResponse:
    params = {
        key: value for key, value in (("flash_success", success), ("flash_error", error)) if value
    }
    url = "/agent/work" + ("?" + urlencode(params) if params else "")
    return RedirectResponse(url, status_code=303)


def _authorized(db: Session, user: User) -> bool:
    return authz.has_assigned_capability(db, user.id, WORK_QUEUE)


def _parse_callback(value: str, timezone_name: str) -> datetime | None:
    if not value.strip():
        return None
    try:
        local_value = datetime.fromisoformat(value)
        timezone = ZoneInfo(timezone_name)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise MissingRequiredField("callback time is invalid") from exc
    if local_value.tzinfo is None:
        local_value = local_value.replace(tzinfo=timezone)
    return local_value.astimezone(UTC)


@router.get("")
def workbench(
    request: Request,
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
):
    if not _authorized(db, user):
        return RedirectResponse("/dashboard?flash_error=Not+authorized+for+agent+work.", 303)

    try:
        lease = work_service.get_active_lease(db, user.id)
        db.commit()
    except WorkItemError:
        db.rollback()
        lease = None

    campaign = db.get(Campaign, lease.campaign_id) if lease else None
    dispositions = []
    if campaign is not None:
        dispositions = list(
            db.scalars(
                select(CampaignDispositionDefinition)
                .where(
                    CampaignDispositionDefinition.campaign_id == campaign.id,
                    CampaignDispositionDefinition.active.is_(True),
                )
                .order_by(
                    CampaignDispositionDefinition.display_order,
                    CampaignDispositionDefinition.label,
                )
            )
        )

    context = page_context(
        request,
        db,
        user,
        active_section="workbench",
        lease=lease,
        campaign=campaign,
        dispositions=dispositions,
        callbacks=work_service.list_agent_callbacks(db, user.id),
        stats=agent_stats.get_today_stats(db, user.id),
        idempotency_key=str(uuid.uuid4()),
        flash_error=request.query_params.get("flash_error"),
        flash_success=request.query_params.get("flash_success"),
    )
    return templates.TemplateResponse(request, "agent_work.html", context)


@router.post("/next", dependencies=[Depends(verify_form_csrf)])
def next_contact(
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
):
    if not _authorized(db, user):
        return _redirect(error="Not authorized for agent work.")
    result = work_service.lease_next(db, user.id)
    db.commit()
    if result is None:
        return _redirect(error="No contact is available in your assigned campaign.")
    return _redirect(success="Contact secured. Complete or release it before moving on.")


@router.post("/{work_item_id}/complete", dependencies=[Depends(verify_form_csrf)])
def complete_contact(
    work_item_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
    lease_id: uuid.UUID = Form(...),
    disposition_id: uuid.UUID = Form(...),
    notes: str = Form(""),
    callback_at: str = Form(""),
    duration_seconds: int | None = Form(None),
    idempotency_key: str = Form(...),
):
    if not _authorized(db, user):
        return _redirect(error="Not authorized for agent work.")
    campaign_id = db.scalar(
        select(CampaignDispositionDefinition.campaign_id).where(
            CampaignDispositionDefinition.id == disposition_id
        )
    )
    campaign = db.get(Campaign, campaign_id) if campaign_id else None
    try:
        callback = _parse_callback(callback_at, campaign.timezone if campaign else "UTC")
        result = work_service.complete_work_item(
            db,
            work_item_id=work_item_id,
            agent_id=user.id,
            lease_id=lease_id,
            disposition_id=disposition_id,
            notes=notes.strip() or None,
            callback_at=callback,
            self_reported_duration_seconds=duration_seconds,
            idempotency_key=idempotency_key,
        )
    except (LeaseConflict, DispositionMismatch, MissingRequiredField, IdempotencyConflict) as exc:
        db.rollback()
        return _redirect(error=str(exc))
    db.commit()
    message = "Callback scheduled." if result.callback_at else "Disposition saved."
    return _redirect(success=message)


@router.post("/{work_item_id}/skip", dependencies=[Depends(verify_form_csrf)])
def skip_contact(
    work_item_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
    lease_id: uuid.UUID = Form(...),
    reason: str = Form(...),
):
    if not _authorized(db, user):
        return _redirect(error="Not authorized for agent work.")
    try:
        work_service.skip_work_item(
            db,
            work_item_id=work_item_id,
            agent_id=user.id,
            lease_id=lease_id,
            reason=reason,
        )
    except (LeaseConflict, MissingRequiredField) as exc:
        db.rollback()
        return _redirect(error=str(exc))
    db.commit()
    return _redirect(success="Contact released with a recorded reason.")


@router.post("/{work_item_id}/renew", dependencies=[Depends(verify_form_csrf)])
def renew_contact(
    work_item_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
    lease_id: uuid.UUID = Form(...),
):
    if not _authorized(db, user):
        return _redirect(error="Not authorized for agent work.")
    try:
        work_service.renew_lease(db, work_item_id, user.id, lease_id)
    except LeaseConflict as exc:
        db.rollback()
        return _redirect(error=str(exc))
    db.commit()
    return _redirect(success="Contact hold extended.")
