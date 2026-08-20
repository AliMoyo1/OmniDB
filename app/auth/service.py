"""Authentication service: credential checks and activation tokens."""

from __future__ import annotations

import uuid

from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.auth import totp as totp_mod
from app.config import get_settings
from app.models.identity import User
from app.security.passwords import dummy_verify, verify_password

_ACTIVATION_SALT = "activation"
_ACTIVATION_MAX_AGE = 24 * 3600


class AuthError(Exception):
    """Login failed. The message is intentionally generic to avoid enumeration."""


def _serializer(salt: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().app_secret_key.get_secret_value(), salt=salt)


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


def issue_activation_token(user_id: uuid.UUID) -> str:
    return _serializer(_ACTIVATION_SALT).dumps(str(user_id))


def verify_activation_token(token: str) -> uuid.UUID | None:
    try:
        raw = _serializer(_ACTIVATION_SALT).loads(token, max_age=_ACTIVATION_MAX_AGE)
    except BadSignature:
        return None
    try:
        return uuid.UUID(raw)
    except (ValueError, TypeError):
        return None
