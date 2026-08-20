"""Integration tests. Require a database and Redis (CI provides both)."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    from app.main import app

    return TestClient(app)


def _make_user(email: str, password: str) -> None:
    from app.db import SessionLocal
    from app.models.identity import User
    from app.security.passwords import hash_password

    with SessionLocal() as db:
        db.add(
            User(
                workforce_id=email.split("@")[0],
                email=email,
                display_name="Test User",
                password_hash=hash_password(password),
            )
        )
        db.commit()


@pytest.mark.integration
def test_healthz(client: TestClient):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.integration
def test_login_then_me(client: TestClient):
    email = f"user-{uuid.uuid4().hex[:8]}@example.com"
    password = "correct horse battery staple"
    _make_user(email, password)

    login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    assert "cc_session" in login.cookies

    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == email


@pytest.mark.integration
def test_me_requires_auth(client: TestClient):
    fresh = TestClient(client.app)
    assert fresh.get("/api/v1/auth/me").status_code == 401
