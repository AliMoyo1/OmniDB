"""Shared fixtures for integration tests (require a real Postgres and Redis)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models.authz import RoleAssignment
from app.models.identity import User
from app.security.passwords import hash_password

TEST_PASSWORD = "correct horse battery staple"


@pytest.fixture
def client() -> TestClient:
    from app.main import app

    return TestClient(app)


def make_user(email: str, password: str = TEST_PASSWORD) -> uuid.UUID:
    with SessionLocal() as db:
        user = User(
            workforce_id=email.split("@")[0],
            email=email,
            display_name="Test User",
            password_hash=hash_password(password),
        )
        db.add(user)
        db.commit()
        return user.id


def make_user_with_role(
    email: str, role_code: str, password: str = TEST_PASSWORD, scope_type: str = "organization"
) -> uuid.UUID:
    with SessionLocal() as db:
        user = User(
            workforce_id=email.split("@")[0],
            email=email,
            display_name="Test User",
            password_hash=hash_password(password),
        )
        db.add(user)
        db.flush()
        db.add(
            RoleAssignment(
                user_id=user.id,
                role_code=role_code,
                scope_type=scope_type,
                effective_from=datetime.now(timezone.utc),
            )
        )
        db.commit()
        return user.id


def login(client: TestClient, email: str, password: str = TEST_PASSWORD) -> None:
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text


def csrf_headers(client: TestClient) -> dict[str, str]:
    token = client.cookies.get("cc_csrf")
    assert token, "csrf cookie not set; did you log in first?"
    return {"x-csrf-token": token}


@pytest.fixture
def manager_client(client: TestClient) -> TestClient:
    from app.authz.capabilities import ROLE_MANAGER

    email = f"manager-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(email, ROLE_MANAGER)
    login(client, email)
    return client


@pytest.fixture
def agent_client(client: TestClient) -> TestClient:
    from app.authz.capabilities import ROLE_AGENT

    email = f"agent-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(email, ROLE_AGENT)
    login(client, email)
    return client
