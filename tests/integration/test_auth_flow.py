"""Integration tests for authentication. Require a database and Redis (CI provides both)."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import service as auth_service
from app.authz.capabilities import ROLE_SUPER_ADMIN
from app.db import SessionLocal
from app.models.base import utcnow
from app.models.identity import User
from app.models.session import Session
from app.security.passwords import verify_password
from app.security.tokens import hash_token
from tests.integration.conftest import (
    TEST_PASSWORD,
    csrf_headers,
    login,
    make_user,
    make_user_with_role,
)


@pytest.mark.integration
def test_healthz(client: TestClient):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.integration
def test_login_then_me(client: TestClient):
    email = f"user-{uuid.uuid4().hex[:8]}@example.com"
    make_user(email)

    login = client.post(
        "/api/v1/auth/login", json={"email": email, "password": TEST_PASSWORD}
    )
    assert login.status_code == 200, login.text
    assert "cc_session" in login.cookies

    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == email


@pytest.mark.integration
def test_me_requires_auth(client: TestClient):
    fresh = TestClient(client.app)
    assert fresh.get("/api/v1/auth/me").status_code == 401


@pytest.mark.integration
def test_read_only_request_persists_sliding_idle_expiry(client: TestClient):
    email = f"idle-slide-{uuid.uuid4().hex[:8]}@example.com"
    make_user(email)
    login(client, email)

    session_token = client.cookies.get("cc_session")
    assert session_token is not None
    short_deadline = utcnow() + timedelta(minutes=1)
    with SessionLocal() as db:
        session = db.scalar(
            select(Session).where(Session.token_hash == hash_token(session_token))
        )
        assert session is not None
        session.idle_expires_at = short_deadline
        db.commit()

    response = client.get("/api/v1/auth/me")
    assert response.status_code == 200, response.text

    with SessionLocal() as db:
        session = db.scalar(
            select(Session).where(Session.token_hash == hash_token(session_token))
        )
        assert session is not None
        assert session.idle_expires_at > short_deadline


@pytest.mark.integration
def test_activation_token_is_single_use(client: TestClient):
    email = f"activation-{uuid.uuid4().hex[:8]}@example.com"
    user_id = make_user(email)
    with SessionLocal() as db:
        user = db.get(User, user_id)
        assert user is not None
        user.password_hash = None
        token = auth_service.issue_activation_token(db, user.id)
        db.commit()

    new_password = "new correct horse battery staple"
    first = client.post(
        "/api/v1/auth/activate",
        json={"token": token, "new_password": new_password},
    )
    second = client.post(
        "/api/v1/auth/activate",
        json={"token": token, "new_password": "another strong password"},
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 400
    with SessionLocal() as db:
        user = db.get(User, user_id)
        assert user is not None and user.password_hash is not None
        assert verify_password(new_password, user.password_hash)


@pytest.mark.integration
def test_sensitive_admin_reset_requires_recent_reauthentication(client: TestClient):
    admin_email = f"step-admin-{uuid.uuid4().hex[:8]}@example.com"
    target_email = f"step-target-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(admin_email, ROLE_SUPER_ADMIN, scope_type="installation")
    target_id = make_user(target_email)
    login(client, admin_email)

    session_token = client.cookies.get("cc_session")
    assert session_token is not None
    with SessionLocal() as db:
        session = db.scalar(
            select(Session).where(Session.token_hash == hash_token(session_token))
        )
        assert session is not None
        session.reauthenticated_at = utcnow() - timedelta(hours=1)
        db.commit()

    denied = client.post(
        f"/api/v1/admin/users/{target_id}/reset-password",
        headers=csrf_headers(client),
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "reauthentication_required"

    reauthenticated = client.post(
        "/api/v1/auth/reauthenticate",
        json={"password": TEST_PASSWORD},
        headers=csrf_headers(client),
    )
    assert reauthenticated.status_code == 200, reauthenticated.text

    allowed = client.post(
        f"/api/v1/admin/users/{target_id}/reset-password",
        headers=csrf_headers(client),
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["activation_token"]
