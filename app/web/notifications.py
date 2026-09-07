"""Server-rendered notification inbox (/notifications).

Every authenticated user has an inbox; there is no capability gate, because a
notification is private to its recipient. Thin browser layer over
app/notifications/service.py.
"""

from __future__ import annotations

import uuid
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_session
from app.models.identity import User
from app.notifications import service as notifications_service
from app.web.dependencies import require_page_user, verify_form_csrf
from app.web.templates import page_context, templates

router = APIRouter(prefix="/notifications", tags=["web-notifications"])


def _redirect(*, success: str | None = None, error: str | None = None) -> RedirectResponse:
    params = {
        key: value for key, value in (("flash_success", success), ("flash_error", error)) if value
    }
    return RedirectResponse(
        "/notifications" + ("?" + urlencode(params) if params else ""), status_code=303
    )


@router.get("")
def inbox(
    request: Request,
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
):
    notifications = notifications_service.list_for_user(db, user.id, limit=100)
    context = page_context(
        request, db, user,
        active_section="notifications",
        notifications=notifications,
        flash_error=request.query_params.get("flash_error"),
        flash_success=request.query_params.get("flash_success"),
    )
    return templates.TemplateResponse(request, "notifications.html", context)


@router.post("/{notification_id}/read", dependencies=[Depends(verify_form_csrf)])
def mark_read_action(
    notification_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
):
    # Whether or not it existed / belonged to this user, redirect back the same
    # way - the inbox never confirms another user's notification even exists.
    notifications_service.mark_read(db, notification_id, user.id)
    db.commit()
    return _redirect()


@router.post("/read-all", dependencies=[Depends(verify_form_csrf)])
def mark_all_read_action(
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
):
    marked = notifications_service.mark_all_read(db, user.id)
    db.commit()
    if marked:
        return _redirect(success=f"Marked {marked} notification(s) as read.")
    return _redirect()
