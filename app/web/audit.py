"""Server-rendered audit trail (/audit): plan 4A's "protected audit search" -
the actor's own visibility scope (app/api/admin.py::audit_visibility) narrowed by
optional action/result/date filters, never widened by them. Thin layer over
list_visible_audit_events, the same function the JSON endpoint already uses.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.admin import list_visible_audit_events
from app.authz import service as authz
from app.authz.capabilities import VIEW_AUDIT
from app.db import get_session
from app.models.identity import User
from app.web.dependencies import require_page_user
from app.web.templates import page_context, templates

router = APIRouter(prefix="/audit", tags=["web-audit"])

_RESULTS = ("success", "failure", "denied")


def _parse_day(value: str | None, *, end_of_day: bool) -> datetime | None:
    if not value:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    boundary = time.max if end_of_day else time.min
    return datetime.combine(parsed, boundary, tzinfo=UTC)


@router.get("")
def audit_list(
    request: Request,
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
):
    if not authz.has_assigned_capability(db, user.id, VIEW_AUDIT):
        return RedirectResponse("/dashboard?flash_error=Not+authorized+for+the+audit+trail.", 303)

    action = request.query_params.get("action", "").strip() or None
    result = request.query_params.get("result", "").strip() or None
    since_raw = request.query_params.get("since", "").strip()
    until_raw = request.query_params.get("until", "").strip()
    since = _parse_day(since_raw, end_of_day=False)
    until = _parse_day(until_raw, end_of_day=True)

    events = list_visible_audit_events(
        db, user.id, limit=200, action=action, result=result, since=since, until=until
    )
    actor_ids = {e.actor_user_id for e in events if e.actor_user_id}
    actors = (
        {u.id: u for u in db.scalars(select(User).where(User.id.in_(actor_ids)))}
        if actor_ids
        else {}
    )

    context = page_context(
        request, db, user,
        active_section="audit",
        events=events,
        actors=actors,
        results=_RESULTS,
        filter_action=action or "",
        filter_result=result or "",
        filter_since=since_raw,
        filter_until=until_raw,
    )
    return templates.TemplateResponse(request, "audit.html", context)
