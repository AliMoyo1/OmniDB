"""CSRF tokens bound to the session and signed with the app secret.

The signed token encodes the session id, so validity does not need extra server
storage: a token is valid only for the session it was issued to.
"""

from __future__ import annotations

from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.config import get_settings

CSRF_COOKIE = "cc_csrf"
CSRF_HEADER = "x-csrf-token"
_SALT = "csrf"
_MAX_AGE = 8 * 3600


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().app_secret_key.get_secret_value(), salt=_SALT)


def issue(session_id: str) -> str:
    return _serializer().dumps(session_id)


def validate(token: str | None, session_id: str) -> bool:
    if not token:
        return False
    try:
        decoded = _serializer().loads(token, max_age=_MAX_AGE)
    except BadSignature:
        return False
    return decoded == session_id
