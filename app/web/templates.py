"""Single shared Jinja2Templates instance, plus the context every authenticated
page needs (the signed-in user, their roles, and the CSRF token for the base
layout's logout form)."""

from __future__ import annotations

from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import csrf
from app.authz import service as authz
from app.models.identity import User

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def page_context(request: Request, db: Session, user: User, **extra) -> dict:
    """The CSRF token is read back from its own (deliberately non-httponly) cookie
    set at login, not re-issued per render - it is already the current valid token
    for this session, and csrf.validate() has no single-use/nonce semantics to lose
    by reusing it across page loads."""
    roles = sorted(authz.effective_roles(db, user.id))
    return {
        "user": user,
        "roles": roles,
        "csrf_token": request.cookies.get(csrf.CSRF_COOKIE, ""),
        **extra,
    }
