"""End-to-end coverage for mandatory API and browser TOTP enrollment."""

from __future__ import annotations

import uuid

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import totp as totp_mod
from app.db import SessionLocal
from app.models.identity import User
from app.models.session import Session
from app.security.tokens import hash_token
from tests.integration.conftest import (
    TEST_PASSWORD,
    TEST_TOTP_SECRET,
    csrf_headers,
    make_user,
    make_user_with_role,
)

pytestmark = pytest.mark.integration


def _csrf(client: TestClient) -> str:
    token = client.cookies.get("cc_csrf")
    assert token
    return token


def test_api_password_session_is_restricted_until_totp_is_verified():
    from app.main import app

    email = f"api-mfa-{uuid.uuid4().hex[:8]}@example.com"
    user_id = make_user(email, totp_enrolled=False)
    client = TestClient(app, follow_redirects=False)

    signed_in = client.post(
        "/api/v1/auth/login", json={"email": email, "password": TEST_PASSWORD}
    )
    assert signed_in.status_code == 200, signed_in.text
    assert signed_in.json()["mfa_enrollment_required"] is True
    original_token = client.cookies.get("cc_session")
    original_csrf = client.cookies.get("cc_csrf")
    assert original_token
    assert original_csrf

    blocked = client.get("/api/v1/auth/me")
    assert blocked.status_code == 403
    assert blocked.json()["detail"]["code"] == "mfa_enrollment_required"

    without_csrf = client.post(
        "/api/v1/auth/reauthenticate", json={"password": TEST_PASSWORD}
    )
    assert without_csrf.status_code == 403

    too_early = client.post("/api/v1/auth/totp/enroll", headers=csrf_headers(client))
    assert too_early.status_code == 403
    assert too_early.json()["detail"]["code"] == "reauthentication_required"

    confirmed = client.post(
        "/api/v1/auth/reauthenticate",
        json={"password": TEST_PASSWORD},
        headers=csrf_headers(client),
    )
    assert confirmed.status_code == 200, confirmed.text
    still_blocked = client.get("/api/v1/auth/me")
    assert still_blocked.status_code == 403
    assert still_blocked.json()["detail"]["code"] == "mfa_enrollment_required"

    started = client.post("/api/v1/auth/totp/enroll", headers=csrf_headers(client))
    assert started.status_code == 200, started.text
    secret = started.json()["secret"]
    assert secret
    assert secret not in str(started.url)
    assert started.headers["cache-control"] == "no-store"

    invalid = client.post(
        "/api/v1/auth/totp/verify",
        json={"code": "not-six"},
        headers=csrf_headers(client),
    )
    assert invalid.status_code == 400

    verified = client.post(
        "/api/v1/auth/totp/verify",
        json={"code": pyotp.TOTP(secret).now()},
        headers=csrf_headers(client),
    )
    assert verified.status_code == 200, verified.text
    assert secret not in verified.text
    replacement_token = client.cookies.get("cc_session")
    replacement_csrf = client.cookies.get("cc_csrf")
    assert replacement_token and replacement_token != original_token
    assert replacement_csrf and replacement_csrf != original_csrf

    stale_csrf = client.post(
        "/api/v1/auth/reauthenticate",
        json={
            "password": TEST_PASSWORD,
            "totp_code": pyotp.TOTP(secret).now(),
        },
        headers={"x-csrf-token": original_csrf},
    )
    assert stale_csrf.status_code == 403

    with SessionLocal() as db:
        user = db.get(User, user_id)
        assert user is not None and user.totp_enrolled
        original_session = db.scalar(
            select(Session).where(Session.token_hash == hash_token(original_token))
        )
        assert original_session is not None and original_session.revoked_at is not None

    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["mfa_enrollment_required"] is False

    cannot_replace = client.post(
        "/api/v1/auth/totp/enroll", headers=csrf_headers(client)
    )
    assert cannot_replace.status_code == 409


def test_browser_enrollment_never_places_the_secret_in_a_url_or_log(caplog):
    from app.main import app

    email = f"web-mfa-{uuid.uuid4().hex[:8]}@example.com"
    user_id = make_user_with_role(email, "manager", totp_enrolled=False)
    client = TestClient(app, follow_redirects=False)

    signed_in = client.post("/login", data={"email": email, "password": TEST_PASSWORD})
    assert signed_in.status_code == 303
    assert signed_in.headers["location"] == "/security/mfa"
    original_token = client.cookies.get("cc_session")
    assert original_token

    blocked = client.get("/dashboard")
    assert blocked.status_code == 303
    assert blocked.headers["location"] == "/security/mfa"

    page = client.get("/security/mfa")
    assert page.status_code == 200
    assert "Confirm your password" in page.text
    assert 'action="/security/mfa/reauthenticate"' in page.text
    assert 'class="icon-dock"' not in page.text

    premature = client.post(
        "/security/mfa/start", data={"csrf_token": _csrf(client)}
    )
    assert premature.status_code == 403
    with SessionLocal() as db:
        user = db.get(User, user_id)
        assert user is not None and user.totp_secret_ciphertext is None

    bad_csrf = client.post(
        "/security/mfa/reauthenticate",
        data={"csrf_token": "stale", "password": TEST_PASSWORD},
    )
    assert bad_csrf.status_code == 303
    assert bad_csrf.headers["location"].startswith("/security/mfa?flash_error=")

    confirmed = client.post(
        "/security/mfa/reauthenticate",
        data={"csrf_token": _csrf(client), "password": TEST_PASSWORD},
    )
    assert confirmed.status_code == 303

    caplog.clear()
    started = client.post("/security/mfa/start", data={"csrf_token": _csrf(client)})
    assert started.status_code == 200, started.text
    assert started.headers["cache-control"] == "no-store"
    assert 'action="/security/mfa/verify"' in started.text

    with SessionLocal() as db:
        user = db.get(User, user_id)
        assert user is not None and user.totp_secret_ciphertext
        secret = totp_mod.decrypt_secret(user.totp_secret_ciphertext)

    assert secret not in str(started.url)
    assert secret not in caplog.text
    assert " ".join(secret[index : index + 4] for index in range(0, len(secret), 4)) in started.text

    refreshed = client.get("/security/mfa")
    assert secret not in refreshed.text
    assert 'action="/security/mfa/verify"' in refreshed.text

    invalid = client.post(
        "/security/mfa/verify",
        data={"csrf_token": _csrf(client), "code": "12x456"},
    )
    assert invalid.status_code == 400
    assert secret not in invalid.text

    verified = client.post(
        "/security/mfa/verify",
        data={"csrf_token": _csrf(client), "code": pyotp.TOTP(secret).now()},
    )
    assert verified.status_code == 303, verified.text
    assert verified.headers["location"].startswith("/security/mfa?flash_success=")
    assert secret not in verified.headers["location"]
    replacement_token = client.cookies.get("cc_session")
    assert replacement_token and replacement_token != original_token

    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    active = client.get("/security/mfa")
    assert active.status_code == 200
    assert "Your authenticator is connected" in active.text
    assert secret not in active.text
    assert 'class="icon-dock"' in active.text


def test_enrolled_browser_login_requires_a_current_authenticator_code():
    from app.main import app

    email = f"login-mfa-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(email, "manager")
    client = TestClient(app, follow_redirects=False)

    missing = client.post("/login", data={"email": email, "password": TEST_PASSWORD})
    assert missing.status_code == 401
    assert "Enter your authenticator code" in missing.text

    accepted = client.post(
        "/login",
        data={
            "email": email,
            "password": TEST_PASSWORD,
            "totp_code": pyotp.TOTP(TEST_TOTP_SECRET).now(),
        },
    )
    assert accepted.status_code == 303
    assert accepted.headers["location"] == "/dashboard"


def test_unauthenticated_security_page_redirects_to_login():
    from app.main import app

    client = TestClient(app, follow_redirects=False)
    response = client.get("/security/mfa")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
