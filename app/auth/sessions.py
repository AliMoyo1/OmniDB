"""Opaque server-side sessions. PostgreSQL is authoritative for validity (ADR-018)."""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.config import get_settings
from app.models.base import utcnow
from app.models.session import Session as SessionModel
from app.security.tokens import generate_token, hash_token

COOKIE_NAME = "cc_session"


def create_session(
    db: DbSession,
    user_id: uuid.UUID,
    *,
    source_summary: str | None = None,
    mfa_state: str = "satisfied",
) -> tuple[SessionModel, str]:
    settings = get_settings()
    now = utcnow()
    token = generate_token()
    row = SessionModel(
        user_id=user_id,
        token_hash=hash_token(token),
        last_seen_at=now,
        idle_expires_at=now + timedelta(minutes=settings.session_idle_minutes),
        absolute_expires_at=now + timedelta(hours=settings.session_absolute_hours),
        source_summary=source_summary,
        mfa_state=mfa_state,
    )
    db.add(row)
    db.flush()
    return row, token


def load_session(db: DbSession, token: str | None) -> SessionModel | None:
    if not token:
        return None
    row = db.scalar(select(SessionModel).where(SessionModel.token_hash == hash_token(token)))
    if row is None or row.revoked_at is not None:
        return None
    now = utcnow()
    if row.idle_expires_at <= now or row.absolute_expires_at <= now:
        return None
    # Slide the idle window, never past the absolute expiry.
    settings = get_settings()
    row.last_seen_at = now
    row.idle_expires_at = min(
        now + timedelta(minutes=settings.session_idle_minutes), row.absolute_expires_at
    )
    return row


def revoke_session(db: DbSession, row: SessionModel) -> None:
    if row.revoked_at is None:
        row.revoked_at = utcnow()


def revoke_all_for_user(db: DbSession, user_id: uuid.UUID) -> None:
    rows = db.scalars(
        select(SessionModel).where(
            SessionModel.user_id == user_id, SessionModel.revoked_at.is_(None)
        )
    )
    now = utcnow()
    for row in rows:
        row.revoked_at = now


def rotate_session(
    db: DbSession, row: SessionModel, *, mfa_state: str | None = None
) -> tuple[SessionModel, str]:
    """Revoke the current session and issue a fresh one (login, privilege change)."""
    revoke_session(db, row)
    return create_session(
        db,
        row.user_id,
        source_summary=row.source_summary,
        mfa_state=mfa_state or row.mfa_state,
    )
