"""Browser account-security flow for mandatory TOTP enrollment.

Password-only sessions can reach this router and logout, but normal application
pages remain gated until enrollment completes. Secrets are rendered only in a
POST response, never placed in a URL, cookie, log, or browser storage.
"""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.auth import ratelimit, service
from app.auth import sessions as sess
from app.auth.dependencies import is_recently_reauthenticated
from app.auth.router import set_auth_cookies
from app.db import get_session
from app.models.base import utcnow
from app.models.identity import User
from app.models.session import Session as SessionModel
from app.web.dependencies import (
    require_authenticated_page_user,
    require_page_session,
    verify_form_csrf,
)
from app.web.templates import page_context, templates

router = APIRouter(prefix="/security/mfa", tags=["web-security"])


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def _source_summary(request: Request) -> str:
    return request.headers.get("user-agent", "")[:255]


def _redirect(*, success: str | None = None, error: str | None = None) -> RedirectResponse:
    params: dict[str, str] = {}
    if success:
        params["flash_success"] = success
    if error:
        params["flash_error"] = error
    target = "/security/mfa" + ("?" + urlencode(params) if params else "")
    return RedirectResponse(target, status_code=303)


def _security_context(
    request: Request,
    db: Session,
    user: User,
    session: SessionModel,
    *,
    enrollment_secret: str | None = None,
    flash_error: str | None = None,
    flash_success: str | None = None,
) -> dict:
    mfa_active = user.totp_enrolled and session.mfa_state == sess.MFA_SATISFIED
    context = page_context(
        request,
        db,
        user,
        active_section="security",
        mfa_enrollment_required=not mfa_active,
        mfa_active=mfa_active,
        recently_reauthenticated=is_recently_reauthenticated(session),
        setup_in_progress=bool(user.totp_secret_ciphertext and not user.totp_enrolled),
        enrollment_secret=enrollment_secret,
        enrollment_secret_display=(
            " ".join(
                enrollment_secret[index : index + 4]
                for index in range(0, len(enrollment_secret), 4)
            )
            if enrollment_secret
            else None
        ),
        flash_error=flash_error,
        flash_success=flash_success,
    )
    return context


def _render_security(
    request: Request,
    db: Session,
    user: User,
    session: SessionModel,
    *,
    enrollment_secret: str | None = None,
    flash_error: str | None = None,
    flash_success: str | None = None,
    status_code: int = 200,
):
    return templates.TemplateResponse(
        request,
        "security_mfa.html",
        _security_context(
            request,
            db,
            user,
            session,
            enrollment_secret=enrollment_secret,
            flash_error=flash_error,
            flash_success=flash_success,
        ),
        status_code=status_code,
    )


@router.get("")
def mfa_security_page(
    request: Request,
    db: Session = Depends(get_session),
    session: SessionModel = Depends(require_page_session),
    user: User = Depends(require_authenticated_page_user),
):
    return _render_security(
        request,
        db,
        user,
        session,
        flash_error=request.query_params.get("flash_error"),
        flash_success=request.query_params.get("flash_success"),
    )


@router.post("/reauthenticate", dependencies=[Depends(verify_form_csrf)])
def mfa_reauthenticate(
    request: Request,
    db: Session = Depends(get_session),
    session: SessionModel = Depends(require_page_session),
    user: User = Depends(require_authenticated_page_user),
    password: str = Form(...),
    totp_code: str | None = Form(None),
):
    rate_key = f"reauth:{service.normalize_email(user.email)}"
    if not ratelimit.check_and_increment(rate_key, _client_ip(request)):
        record_audit(
            db,
            action="auth.reauthenticate",
            result="denied",
            actor_user_id=user.id,
            reason_code="rate_limited",
            source_ip=_client_ip(request),
            user_agent_summary=_source_summary(request),
        )
        db.commit()
        return _render_security(
            request,
            db,
            user,
            session,
            flash_error="Too many attempts. Wait before trying again.",
            status_code=429,
        )
    try:
        service.authenticate(db, user.email, password, totp_code or None)
    except service.AuthError:
        record_audit(
            db,
            action="auth.reauthenticate",
            result="failure",
            actor_user_id=user.id,
            reason_code="invalid_credentials",
            source_ip=_client_ip(request),
            user_agent_summary=_source_summary(request),
        )
        db.commit()
        return _render_security(
            request,
            db,
            user,
            session,
            flash_error="Password or authenticator code was not accepted.",
            status_code=401,
        )

    ratelimit.reset_account(rate_key)
    session.reauthenticated_at = utcnow()
    session.mfa_state = (
        sess.MFA_SATISFIED if user.totp_enrolled else sess.MFA_ENROLLMENT_REQUIRED
    )
    record_audit(
        db,
        action="auth.reauthenticate",
        result="success",
        actor_user_id=user.id,
        source_ip=_client_ip(request),
        user_agent_summary=_source_summary(request),
    )
    db.commit()
    return _redirect(success="Identity confirmed. Continue with authenticator setup.")


@router.post("/start", dependencies=[Depends(verify_form_csrf)])
def mfa_start(
    request: Request,
    db: Session = Depends(get_session),
    session: SessionModel = Depends(require_page_session),
    user: User = Depends(require_authenticated_page_user),
):
    if user.totp_enrolled:
        return _redirect(success="Two-step verification is already active.")
    if not is_recently_reauthenticated(session):
        return _render_security(
            request,
            db,
            user,
            session,
            flash_error="Confirm your password before starting authenticator setup.",
            status_code=403,
        )
    try:
        _, secret = service.begin_totp_enrollment(db, user.id)
    except service.TotpEnrollmentError:
        db.rollback()
        return _render_security(
            request,
            db,
            user,
            session,
            flash_error="Authenticator setup could not be started.",
            status_code=409,
        )
    session.mfa_state = sess.MFA_ENROLLMENT_REQUIRED
    record_audit(
        db,
        action="auth.totp.enroll_start",
        result="success",
        actor_user_id=user.id,
        source_ip=_client_ip(request),
        user_agent_summary=_source_summary(request),
    )
    db.commit()
    return _render_security(
        request,
        db,
        user,
        session,
        enrollment_secret=secret,
        flash_success="A new one-time setup key was created.",
    )


@router.post("/verify", dependencies=[Depends(verify_form_csrf)])
def mfa_verify(
    request: Request,
    db: Session = Depends(get_session),
    session: SessionModel = Depends(require_page_session),
    user: User = Depends(require_authenticated_page_user),
    code: str = Form(...),
):
    if not is_recently_reauthenticated(session):
        return _render_security(
            request,
            db,
            user,
            session,
            flash_error="Confirm your password again before verifying setup.",
            status_code=403,
        )

    rate_key = f"totp-enrollment:{service.normalize_email(user.email)}"
    if not ratelimit.check_and_increment(rate_key, _client_ip(request)):
        record_audit(
            db,
            action="auth.totp.verify",
            result="denied",
            actor_user_id=user.id,
            reason_code="rate_limited",
            source_ip=_client_ip(request),
            user_agent_summary=_source_summary(request),
        )
        db.commit()
        return _render_security(
            request,
            db,
            user,
            session,
            flash_error="Too many verification attempts. Wait before trying again.",
            status_code=429,
        )

    try:
        enrolled_user = service.complete_totp_enrollment(db, user.id, code)
    except service.TotpEnrollmentError as exc:
        record_audit(
            db,
            action="auth.totp.verify",
            result="failure",
            actor_user_id=user.id,
            reason_code=str(exc).replace(" ", "_"),
            source_ip=_client_ip(request),
            user_agent_summary=_source_summary(request),
        )
        db.commit()
        return _render_security(
            request,
            db,
            user,
            session,
            flash_error="That code was not accepted. Check the six digits and try again.",
            status_code=400,
        )

    ratelimit.reset_account(rate_key)
    sess.revoke_all_for_user(db, enrolled_user.id)
    new_session, token = sess.create_session(
        db,
        enrolled_user.id,
        source_summary=session.source_summary,
        mfa_state=sess.MFA_SATISFIED,
    )
    record_audit(
        db,
        action="auth.totp.verify",
        result="success",
        actor_user_id=enrolled_user.id,
        target_type="session",
        target_id=new_session.id,
        source_ip=_client_ip(request),
        user_agent_summary=_source_summary(request),
    )
    db.commit()
    response = _redirect(success="Two-step verification is active.")
    set_auth_cookies(response, token, new_session.id)
    return response
