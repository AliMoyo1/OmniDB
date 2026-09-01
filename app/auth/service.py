"""Authentication service: credential checks and one-time activation tokens."""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.auth import totp as totp_mod
from app.models.activation import ActivationToken
from app.models.base import utcnow
from app.models.identity import User
from app.security.passwords import dummy_verify, hash_password, verify_password
from app.security.tokens import generate_token, hash_token

_ACTIVATION_MAX_AGE = 24 * 3600
_ACTIVATION_PURPOSE = "password_activation"


class AuthError(Exception):
    """Login failed. The message is intentionally generic to avoid enumeration."""


def normalize_email(email: str) -> str:
    return email.strip().lower()


def authenticate(db: DbSession, email: str, password: str, totp_code: str | None) -> User:
    user = db.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None or not user.active or user.password_hash is None:
        dummy_verify()  # equalize timing for missing or unusable accounts
        raise AuthError("invalid credentials")
    if not verify_password(password, user.password_hash):
        raise AuthError("invalid credentials")
    if user.totp_enrolled:
        if not totp_code:
            raise AuthError("second factor required")
        secret = (
            totp_mod.decrypt_secret(user.totp_secret_ciphertext)
            if user.totp_secret_ciphertext
            else None
        )
        if not secret or not totp_mod.verify_code(secret, totp_code):
            raise AuthError("invalid credentials")
    return user


def issue_activation_token(
    db: DbSession,
    user_id: uuid.UUID,
    *,
    created_by: uuid.UUID | None = None,
) -> str:
    """Issue one opaque token and invalidate every prior unused token for the user."""
    locked_user_id = db.scalar(
        select(User.id).where(User.id == user_id).with_for_update()
    )
    if locked_user_id is None:
        raise ValueError("activation user does not exist")
    now = utcnow()
    prior_tokens = db.scalars(
        select(ActivationToken).where(
            ActivationToken.user_id == user_id,
            ActivationToken.purpose == _ACTIVATION_PURPOSE,
            ActivationToken.used_at.is_(None),
        )
    )
    for prior in prior_tokens:
        prior.used_at = now

    token = generate_token()
    db.add(
        ActivationToken(
            user_id=user_id,
            token_hash=hash_token(token),
            purpose=_ACTIVATION_PURPOSE,
            expires_at=now + timedelta(seconds=_ACTIVATION_MAX_AGE),
            created_by=created_by,
        )
    )
    db.flush()
    return token


def consume_activation_token(db: DbSession, token: str) -> uuid.UUID | None:
    """Atomically consume a valid token. A successful token can never be replayed."""
    now = utcnow()
    token_hash = hash_token(token)
    candidate_user_id = db.scalar(
        select(ActivationToken.user_id).where(
            ActivationToken.token_hash == token_hash,
            ActivationToken.purpose == _ACTIVATION_PURPOSE,
            ActivationToken.used_at.is_(None),
            ActivationToken.expires_at > now,
        )
    )
    if candidate_user_id is None:
        return None

    # Reset issuance also locks the user before inspecting tokens. Keeping the same
    # order prevents an activation/reset deadlock and makes their ordering explicit.
    locked_user_id = db.scalar(
        select(User.id).where(User.id == candidate_user_id).with_for_update()
    )
    if locked_user_id is None:
        return None
    row = db.scalar(
        select(ActivationToken)
        .where(
            ActivationToken.token_hash == token_hash,
            ActivationToken.user_id == locked_user_id,
            ActivationToken.purpose == _ACTIVATION_PURPOSE,
            ActivationToken.used_at.is_(None),
            ActivationToken.expires_at > now,
        )
        .with_for_update()
    )
    if row is None:
        return None
    row.used_at = now
    db.flush()
    return row.user_id


def activate_user_password(db: DbSession, token: str, new_password: str) -> User | None:
    """Consume one activation token and set its user password in this transaction."""
    user_id = consume_activation_token(db, token)
    if user_id is None:
        return None
    user = db.get(User, user_id)
    if user is None:
        return None
    user.password_hash = hash_password(new_password)
    return user
