"""Server-rendered feature-flags page (/flags): view and toggle the rollout
flags in app/flags/service.py. Same MANAGE_ROLES gate as the JSON API.
"""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.authz import service as authz
from app.authz.capabilities import MANAGE_ROLES
from app.db import get_session
from app.flags import service as flags_service
from app.flags.service import PermanentlyDisabledFlag, UnknownFlag
from app.models.identity import User
from app.web.dependencies import require_page_user, verify_form_csrf
from app.web.templates import page_context, templates

router = APIRouter(prefix="/flags", tags=["web-flags"])


def _redirect(*, success: str | None = None, error: str | None = None) -> RedirectResponse:
    params = {
        key: value for key, value in (("flash_success", success), ("flash_error", error)) if value
    }
    return RedirectResponse("/flags" + ("?" + urlencode(params) if params else ""), status_code=303)


@router.get("")
def flags_list(
    request: Request,
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
):
    if not authz.has_assigned_capability(db, user.id, MANAGE_ROLES):
        return RedirectResponse("/dashboard?flash_error=Not+authorized+for+feature+flags.", 303)

    context = page_context(
        request, db, user,
        active_section="flags",
        flags=flags_service.list_flags(db),
        permanently_disabled=flags_service.PERMANENTLY_DISABLED,
        flash_error=request.query_params.get("flash_error"),
        flash_success=request.query_params.get("flash_success"),
    )
    return templates.TemplateResponse(request, "flags.html", context)


@router.post("/{flag_key}", dependencies=[Depends(verify_form_csrf)])
def set_flag_action(
    flag_key: str,
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
    enabled: str = Form(...),
    reason_code: str = Form(""),
):
    if not authz.has_assigned_capability(db, user.id, MANAGE_ROLES):
        return _redirect(error="Not authorized for feature flags.")
    try:
        flags_service.set_flag(
            db, flag_key, enabled == "true",
            actor_id=user.id, reason_code=reason_code.strip() or None,
        )
    except (UnknownFlag, PermanentlyDisabledFlag) as exc:
        db.rollback()
        return _redirect(error=str(exc))
    db.commit()
    return _redirect(success=f"{flag_key} updated.")
