"""Single shared Jinja2Templates instance, plus the context every authenticated
page needs (the signed-in user, their roles, the CSRF token for the base layout's
logout form, and the nav-visibility flags the dock/sidebar shell reads).

The nav flags are computed here rather than left to each route to pass, on
purpose: the shell (app/templates/base.html) is shared by every authenticated
page, not just the dashboard, and a route that forgot to pass a flag would
silently show an incomplete or wrong nav for that one page - the same class of
bug (an omitted context key defaulting to Jinja2's falsy Undefined, not an
error) already found once this session in the dashboard's own Campaigns section.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import csrf
from app.authz import service as authz
from app.authz.capabilities import MANAGE_ROLES, VIEW_AUDIT, VIEW_CAMPAIGN, WORK_QUEUE
from app.models.identity import User
from app.workforce.service import ROLE_APPOINTMENT_CAPABILITY

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def nav_flags(db: Session, user: User) -> dict:
    can_manage_workforce = any(
        authz.has_assigned_capability(db, user.id, capability)
        for capability in ROLE_APPOINTMENT_CAPABILITY.values()
    )
    can_manage_teams = authz.has_assigned_capability(db, user.id, MANAGE_ROLES)
    return {
        "can_work_queue": authz.has_assigned_capability(db, user.id, WORK_QUEUE),
        "can_view_campaigns": authz.has_assigned_capability(db, user.id, VIEW_CAMPAIGN),
        "can_manage_workforce": can_manage_workforce,
        "can_manage_teams": can_manage_teams,
        "can_view_teams_nav": can_manage_teams or can_manage_workforce,
        "can_view_audit": authz.has_assigned_capability(db, user.id, VIEW_AUDIT),
    }


def page_context(request: Request, db: Session, user: User, **extra) -> dict:
    """The CSRF token is read back from its own (deliberately non-httponly) cookie
    set at login, not re-issued per render - it is already the current valid token
    for this session, and csrf.validate() has no single-use/nonce semantics to lose
    by reusing it across page loads."""
    roles = sorted(authz.effective_roles(db, user.id))
    context = {
        "user": user,
        "roles": roles,
        "csrf_token": request.cookies.get(csrf.CSRF_COOKIE, ""),
        **nav_flags(db, user),
    }
    context.update(extra)
    return context
