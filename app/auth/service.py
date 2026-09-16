"""Authentication service: credential checks and one-time activation tokens."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.audit.service import record_audit
from app.auth import sessions as sess
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


class TotpEnrollmentError(Exception):
    """TOTP enrollment could not continue in the user's current state."""


class AccountNotEligible(Exception):
    """The target account is not in a state where the requested credential
    operation is allowed (plan 6.4 step 2: verify eligibility under the lock,
    not just authority)."""


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


def begin_totp_enrollment(db: DbSession, user_id: uuid.UUID) -> tuple[User, str]:
    """Create one pending TOTP secret without replacing an active enrollment."""
    user = db.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None or not user.active:
        raise TotpEnrollmentError("account unavailable")
    if user.totp_enrolled:
        raise TotpEnrollmentError("already enrolled")
    secret = totp_mod.new_secret()
    user.totp_secret_ciphertext = totp_mod.encrypt_secret(secret)
    db.flush()
    return user, secret


def complete_totp_enrollment(db: DbSession, user_id: uuid.UUID, code: str) -> User:
    """Verify and activate the pending TOTP secret while holding the user row lock."""
    user = db.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None or not user.active:
        raise TotpEnrollmentError("account unavailable")
    if user.totp_enrolled:
        raise TotpEnrollmentError("already enrolled")
    if not user.totp_secret_ciphertext:
        raise TotpEnrollmentError("no enrollment in progress")
    normalized_code = code.strip().replace(" ", "")
    secret = totp_mod.decrypt_secret(user.totp_secret_ciphertext)
    if len(normalized_code) != 6 or not normalized_code.isdigit():
        raise TotpEnrollmentError("invalid code")
    if not totp_mod.verify_code(secret, normalized_code):
        raise TotpEnrollmentError("invalid code")
    user.totp_enrolled = True
    db.flush()
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


def _lock_user(db: DbSession, user_id: uuid.UUID) -> User:
    """Re-select the identity-mapped User under FOR UPDATE (plan 6.4 step 1) so
    the eligibility check right after it can't race a concurrent change. Safe to
    call again inside issue_activation_token in the same transaction - Postgres
    row locks are reentrant for the holder."""
    user = db.execute(select(User).where(User.id == user_id).with_for_update()).scalar_one_or_none()
    if user is None:
        raise ValueError("user does not exist")
    return user


def _issue_with_expiry(
    db: DbSession, user_id: uuid.UUID, *, created_by: uuid.UUID
) -> tuple[str, datetime]:
    """issue_activation_token computes its own expiry internally and returns
    only the plaintext; callers that need to display the expiry (plan 5.4)
    would otherwise have to re-query for it. Capturing `now` here first and
    deriving the same expiry the token itself will get avoids that extra
    round trip while staying in exact lockstep with _ACTIVATION_MAX_AGE."""
    now = utcnow()
    token = issue_activation_token(db, user_id, created_by=created_by)
    return token, now + timedelta(seconds=_ACTIVATION_MAX_AGE)


def reset_password(db: DbSession, target: User, *, actor_id: uuid.UUID) -> tuple[str, datetime]:
    """Clears the password, revokes every active session, and issues a fresh
    activation token in one operation (plan 6.4/9.8) - the target must
    re-activate before signing in again. Returns (plaintext, expiry); callers
    must render the plaintext only to the current response and never log or
    persist it (plan 9.10/9.12). Authority and self-reset checks are the
    caller's job (plan 6.4 step 3), before this is invoked."""
    locked = _lock_user(db, target.id)
    if not locked.active:
        raise AccountNotEligible("this account is disabled")
    locked.password_hash = None
    sess.revoke_all_for_user(db, locked.id)
    token, expires_at = _issue_with_expiry(db, locked.id, created_by=actor_id)
    record_audit(
        db, action="admin.reset_password", result="success", actor_user_id=actor_id,
        target_type="user", target_id=locked.id,
    )
    return token, expires_at


def reset_mfa(db: DbSession, target: User, *, actor_id: uuid.UUID) -> None:
    """Clears TOTP enrollment and revokes every active session (plan 9.8) - the
    target must re-enroll before completing another sign-in."""
    locked = _lock_user(db, target.id)
    if not locked.active:
        raise AccountNotEligible("this account is disabled")
    locked.totp_secret_ciphertext = None
    locked.totp_enrolled = False
    sess.revoke_all_for_user(db, locked.id)
    record_audit(
        db, action="admin.reset_2fa", result="success", actor_user_id=actor_id,
        target_type="user", target_id=locked.id,
    )


def issue_or_replace_activation_code(
    db: DbSession, target: User, *, actor_id: uuid.UUID
) -> tuple[str, datetime]:
    """Plan 4.2/6.4: initial or replacement activation issuance for a user who
    has not yet set a password. Reissuing invalidates every older unused code
    (issue_activation_token). Not for an already-activated account - reset
    the password there instead, which also issues a fresh code. Returns
    (plaintext, expiry) - see reset_password's docstring on why."""
    locked = _lock_user(db, target.id)
    if not locked.active:
        raise AccountNotEligible("this account is disabled")
    if locked.password_hash is not None:
        raise AccountNotEligible("this account has already been activated")
    token, expires_at = _issue_with_expiry(db, locked.id, created_by=actor_id)
    record_audit(
        db, action="admin.issue_activation_code", result="success", actor_user_id=actor_id,
        target_type="user", target_id=locked.id,
    )
    return token, expires_at


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
