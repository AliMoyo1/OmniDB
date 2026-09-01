"""Browser-facing login/logout: real HTML forms, not the JSON /api/v1/auth API.

Deliberately its own POST /login rather than a thin wrapper that calls the JSON
endpoint internally: a browser form can't easily inspect a JSON 401 body to show
"enter your authenticator code" inline, and this stays a plain form - no
JavaScript - so it keeps working even before any script on the page would.
Cookie handling is reused verbatim from app.auth.router (set_auth_cookies /
clear_auth_cookies), not duplicated: it is security-sensitive enough that two
independent copies could quietly drift out of sync.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.auth import ratelimit, service
from app.auth import sessions as sess
from app.auth.router import _MIN_PASSWORD_LENGTH, clear_auth_cookies, set_auth_cookies
from app.auth.service import AuthError
from app.db import get_session
from app.models.base import utcnow
from app.models.identity import User
from app.web.dependencies import require_page_session, verify_form_csrf
from app.web.templates import templates

router = APIRouter(tags=["web-auth"])


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def _redirect_for_existing_session(
    db: Session, token: str | None
) -> RedirectResponse | None:
    row = sess.load_session(db, token)
    if row is None:
        return None
    user = db.get(User, row.user_id)
    if user is None or not user.active:
        sess.revoke_session(db, row)
        db.commit()
        return None
    target = (
        "/dashboard"
        if user.totp_enrolled and row.mfa_state == sess.MFA_SATISFIED
        else "/security/mfa"
    )
    db.commit()
    return RedirectResponse(target, status_code=303)


@router.get("/login")
def login_form(request: Request, db: Session = Depends(get_session)):
    redirect = _redirect_for_existing_session(db, request.cookies.get(sess.COOKIE_NAME))
    if redirect is not None:
        return redirect
    return templates.TemplateResponse(request, "login.html", {})


@router.post("/login")
def login_submit(
    request: Request,
    db: Session = Depends(get_session),
    email: str = Form(...),
    password: str = Form(...),
    totp_code: str | None = Form(None),
):
    source = request.headers.get("user-agent", "")[:255]
    ip = _client_ip(request)
    rate_key = service.normalize_email(email)

    if not ratelimit.check_and_increment(rate_key, ip):
        record_audit(
            db, action="auth.login", result="denied", reason_code="rate_limited",
            source_ip=ip, user_agent_summary=source,
        )
        db.commit()
        return templates.TemplateResponse(
            request, "login.html",
            {"flash_error": "Too many attempts. Try again shortly.", "email": email},
            status_code=429,
        )

    try:
        user = service.authenticate(db, email, password, totp_code or None)
    except AuthError as exc:
        reason = str(exc)
        record_audit(
            db, action="auth.login", result="failure", reason_code=reason,
            source_ip=ip, user_agent_summary=source,
        )
        db.commit()
        message = (
            "Enter your authenticator code to finish signing in."
            if reason == "second factor required"
            else "Invalid email, password, or code."
        )
        return templates.TemplateResponse(
            request, "login.html", {"flash_error": message, "email": email}, status_code=401,
        )

    ratelimit.reset_account(rate_key)
    mfa_state = sess.MFA_SATISFIED if user.totp_enrolled else sess.MFA_ENROLLMENT_REQUIRED
    row, token = sess.create_session(
        db,
        user.id,
        source_summary=source,
        mfa_state=mfa_state,
        recently_reauthenticated=user.totp_enrolled,
    )
    user.last_login_at = utcnow()
    record_audit(
        db, action="auth.login", result="success", actor_user_id=user.id,
        target_type="session", target_id=row.id, source_ip=ip, user_agent_summary=source,
    )
    db.commit()
    target = "/dashboard" if user.totp_enrolled else "/security/mfa"
    response = RedirectResponse(target, status_code=303)
    set_auth_cookies(response, token, row.id)
    return response


@router.get("/activate")
def activate_form(request: Request, db: Session = Depends(get_session)):
    redirect = _redirect_for_existing_session(db, request.cookies.get(sess.COOKIE_NAME))
    if redirect is not None:
        return redirect
    return templates.TemplateResponse(
        request, "activate.html", {"password_min_length": _MIN_PASSWORD_LENGTH}
    )


@router.post("/activate")
def activate_submit(
    request: Request,
    db: Session = Depends(get_session),
    activation_token: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    context: dict[str, object] = {"password_min_length": _MIN_PASSWORD_LENGTH}
    if len(new_password) < _MIN_PASSWORD_LENGTH:
        context["flash_error"] = (
            f"Password must be at least {_MIN_PASSWORD_LENGTH} characters long."
        )
        return templates.TemplateResponse(request, "activate.html", context, status_code=400)
    if new_password != confirm_password:
        context["flash_error"] = "Passwords do not match."
        return templates.TemplateResponse(request, "activate.html", context, status_code=400)

    user = service.activate_user_password(db, activation_token.strip(), new_password)
    if user is None:
        record_audit(
            db,
            action="auth.activate",
            result="failure",
            reason_code="invalid_or_expired_token",
            source_ip=_client_ip(request),
            user_agent_summary=request.headers.get("user-agent", "")[:255],
        )
        db.commit()
        context["flash_error"] = "Activation failed. Verify the one-time code and try again."
        return templates.TemplateResponse(request, "activate.html", context, status_code=400)

    sess.revoke_all_for_user(db, user.id)
    record_audit(
        db,
        action="auth.activate",
        result="success",
        actor_user_id=user.id,
        source_ip=_client_ip(request),
        user_agent_summary=request.headers.get("user-agent", "")[:255],
    )
    db.commit()
    return templates.TemplateResponse(request, "activate.html", {"activated": True})

@router.post("/logout")
def logout_submit(
    db: Session = Depends(get_session),
    session=Depends(require_page_session),
    _csrf_check: None = Depends(verify_form_csrf),
):
    sess.revoke_session(db, session)
    record_audit(
        db, action="auth.logout", result="success", actor_user_id=session.user_id,
        target_type="session", target_id=session.id,
    )
    db.commit()
    response = RedirectResponse("/login", status_code=303)
    clear_auth_cookies(response)
    return response
