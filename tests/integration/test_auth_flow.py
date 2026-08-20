"""Integration tests for authentication. Require a database and Redis (CI provides both)."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import TEST_PASSWORD, make_user


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
