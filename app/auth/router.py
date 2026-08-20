"""Authentication API (/api/v1/auth)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.auth import csrf, ratelimit, service
from app.auth import sessions as sess
from app.auth import totp as totp_mod
from app.auth.dependencies import get_current_session, get_current_user, require_csrf
from app.auth.schemas import (
    ActivateRequest,
    LoginRequest,
    ReauthRequest,
    TotpEnrollOut,
    TotpVerifyRequest,
    UserOut,
)
from app.auth.service import AuthError
from app.config import get_settings
from app.db import get_session
from app.models.base import utcnow
from app.models.identity import User
from app.models.session import Session as SessionModel
from app.security.passwords import hash_password

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

_MIN_PASSWORD_LENGTH = 12


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def _source_summary(request: Request) -> str:
    return request.headers.get("user-agent", "")[:255]


def _set_auth_cookies(response: Response, token: str, session_id: uuid.UUID) -> None:
    secure = get_settings().cookie_secure
    response.set_cookie(
        sess.COOKIE_NAME, token, httponly=True, secure=secure, samesite="lax", path="/"
    )
    response.set_cookie(
        csrf.CSRF_COOKIE,
        csrf.issue(str(session_id)),
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/",
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(sess.COOKIE_NAME, path="/")
    response.delete_cookie(csrf.CSRF_COOKIE, path="/")


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        workforce_id=user.workforce_id,
    )


@router.post("/login", response_model=UserOut)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_session),
) -> UserOut:
    source = _source_summary(request)
    ip = _client_ip(request)
    rate_key = service.normalize_email(payload.email)

    if not ratelimit.check_and_increment(rate_key):
        record_audit(
            db, action="auth.login", result="denied", reason_code="rate_limited",
            source_ip=ip, user_agent_summary=source,
        )
        db.commit()
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many attempts")

    try:
        user = service.authenticate(db, payload.email, payload.password, payload.totp_code)
    except AuthError as exc:
        reason = str(exc)
        record_audit(
            db, action="auth.login", result="failure", reason_code=reason,
            source_ip=ip, user_agent_summary=source,
        )
        db.commit()
        if reason == "second factor required":
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, detail={"code": "totp_required"}
            ) from None
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials") from None

    ratelimit.reset(rate_key)
    row, token = sess.create_session(db, user.id, source_summary=source, mfa_state="satisfied")
    user.last_login_at = utcnow()
    record_audit(
        db, action="auth.login", result="success", actor_user_id=user.id,
        target_type="session", target_id=row.id, source_ip=ip, user_agent_summary=source,
    )
    db.commit()
    _set_auth_cookies(response, token, row.id)
    return _user_out(user)


@router.post("/logout", dependencies=[Depends(require_csrf)])
def logout(
    response: Response,
    db: Session = Depends(get_session),
    session: SessionModel = Depends(get_current_session),
) -> dict[str, str]:
    sess.revoke_session(db, session)
    record_audit(
        db, action="auth.logout", result="success", actor_user_id=session.user_id,
        target_type="session", target_id=session.id,
    )
    db.commit()
    _clear_auth_cookies(response)
    return {"status": "ok"}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> UserOut:
    return _user_out(user)


@router.post("/reauthenticate", dependencies=[Depends(require_csrf)])
def reauthenticate(
    payload: ReauthRequest,
    db: Session = Depends(get_session),
    session: SessionModel = Depends(get_current_session),
    user: User = Depends(get_current_user),
) -> dict[str, str]:
    try:
        service.authenticate(db, user.email, payload.password, payload.totp_code)
    except AuthError:
        record_audit(db, action="auth.reauthenticate", result="failure", actor_user_id=user.id)
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials") from None
    session.mfa_state = "satisfied"
    record_audit(db, action="auth.reauthenticate", result="success", actor_user_id=user.id)
    db.commit()
    return {"status": "ok"}


@router.get("/sessions")
def list_sessions(
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> list[dict]:
    rows = db.scalars(
        select(SessionModel).where(
            SessionModel.user_id == user.id, SessionModel.revoked_at.is_(None)
        )
    )
    return [
        {
            "id": str(row.id),
            "created_at": row.created_at.isoformat(),
            "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
            "source": row.source_summary,
        }
        for row in rows
    ]


@router.delete("/sessions/{session_id}", dependencies=[Depends(require_csrf)])
def revoke_session(
    session_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict[str, str]:
    row = db.get(SessionModel, session_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    sess.revoke_session(db, row)
    record_audit(
        db, action="auth.session.revoke", result="success", actor_user_id=user.id,
        target_type="session", target_id=row.id,
    )
    db.commit()
    return {"status": "ok"}


@router.post("/totp/enroll", response_model=TotpEnrollOut, dependencies=[Depends(require_csrf)])
def totp_enroll(
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> TotpEnrollOut:
    secret = totp_mod.new_secret()
    user.totp_secret_ciphertext = totp_mod.encrypt_secret(secret)
    user.totp_enrolled = False  # not active until a code is verified
    record_audit(db, action="auth.totp.enroll_start", result="success", actor_user_id=user.id)
    db.commit()
    return TotpEnrollOut(
        secret=secret, provisioning_uri=totp_mod.provisioning_uri(secret, user.email)
    )


@router.post("/totp/verify", dependencies=[Depends(require_csrf)])
def totp_verify(
    payload: TotpVerifyRequest,
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict[str, str]:
    if not user.totp_secret_ciphertext:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no enrollment in progress")
    secret = totp_mod.decrypt_secret(user.totp_secret_ciphertext)
    if not totp_mod.verify_code(secret, payload.code):
        record_audit(db, action="auth.totp.verify", result="failure", actor_user_id=user.id)
        db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid code")
    user.totp_enrolled = True
    record_audit(db, action="auth.totp.verify", result="success", actor_user_id=user.id)
    db.commit()
    return {"status": "ok"}


@router.post("/activate")
def activate(
    payload: ActivateRequest,
    db: Session = Depends(get_session),
) -> dict[str, str]:
    user_id = service.verify_activation_token(payload.token)
    if user_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid or expired token")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid or expired token")
    if len(payload.new_password) < _MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"password must be at least {_MIN_PASSWORD_LENGTH} characters",
        )
    user.password_hash = hash_password(payload.new_password)
    sess.revoke_all_for_user(db, user.id)
    record_audit(db, action="auth.activate", result="success", actor_user_id=user.id)
    db.commit()
    return {"status": "ok"}
