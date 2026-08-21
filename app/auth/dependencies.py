"""FastAPI dependencies for authenticated, CSRF-protected requests."""

from __future__ import annotations

from datetime import timedelta

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth import csrf
from app.auth import sessions as sess
from app.config import get_settings
from app.db import get_session
from app.models.base import utcnow
from app.models.identity import User
from app.models.session import Session as SessionModel


def get_current_session(
    request: Request, db: Session = Depends(get_session)
) -> SessionModel:
    token = request.cookies.get(sess.COOKIE_NAME)
    row = sess.load_session(db, token)
    if row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    # load_session slides the idle deadline. Persist it here because read-only
    # endpoints do not otherwise commit their database session.
    db.commit()
    return row


def get_current_user(
    db: Session = Depends(get_session),
    session: SessionModel = Depends(get_current_session),
) -> User:
    user = db.get(User, session.user_id)
    if user is None or not user.active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="account unavailable")
    return user


def require_csrf(
    request: Request, session: SessionModel = Depends(get_current_session)
) -> None:
    # Defense in depth: reject obvious cross-site requests via Fetch Metadata.
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is not None and fetch_site not in ("same-origin", "same-site", "none"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="cross-site request")
    token = request.headers.get(csrf.CSRF_HEADER)
    if not csrf.validate(token, str(session.id)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid csrf token")


def require_recent_reauthentication(
    session: SessionModel = Depends(get_current_session),
) -> None:
    cutoff = utcnow() - timedelta(minutes=get_settings().step_up_minutes)
    if session.reauthenticated_at is None or session.reauthenticated_at < cutoff:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "reauthentication_required"},
        )
