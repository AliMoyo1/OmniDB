"""Page-request auth: a browser tab gets redirected to /login, not a JSON 401.

Kept separate from app/auth/dependencies.py rather than translating its exceptions:
API and page failure modes are genuinely different responses, and the CSRF source
differs too (a header for fetch/JSON, a hidden form field for a plain HTML form).
"""

from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.auth import csrf
from app.auth import sessions as sess
from app.db import get_session
from app.models.identity import User
from app.models.session import Session as SessionModel


class RedirectToLogin(Exception):
    """Raised instead of a 401 so a registered handler can send a page-friendly
    redirect. See app/main.py's exception_handler registration."""


class InvalidFormCsrf(Exception):
    """Raised instead of a 403 for the same page-friendly-response reason."""


class RedirectToMfaEnrollment(Exception):
    """Raised when a valid password session has not completed TOTP enrollment."""


def require_page_session(
    request: Request, db: Session = Depends(get_session)
) -> SessionModel:
    token = request.cookies.get(sess.COOKIE_NAME)
    row = sess.load_session(db, token)
    if row is None:
        raise RedirectToLogin()
    db.commit()
    return row


def require_authenticated_page_user(
    db: Session = Depends(get_session),
    session: SessionModel = Depends(require_page_session),
) -> User:
    user = db.get(User, session.user_id)
    if user is None or not user.active:
        raise RedirectToLogin()
    return user


def require_page_user(
    user: User = Depends(require_authenticated_page_user),
    session: SessionModel = Depends(require_page_session),
) -> User:
    if not user.totp_enrolled or session.mfa_state != sess.MFA_SATISFIED:
        raise RedirectToMfaEnrollment()
    return user


async def verify_form_csrf(
    request: Request, session: SessionModel = Depends(require_page_session)
) -> None:
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is not None and fetch_site not in ("same-origin", "same-site", "none"):
        raise InvalidFormCsrf()
    form = await request.form()
    token = form.get("csrf_token")
    if not isinstance(token, str) or not csrf.validate(token, str(session.id)):
        raise InvalidFormCsrf()
