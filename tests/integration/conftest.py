"""Shared fixtures for integration tests (require a real Postgres and Redis)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models.authz import RoleAssignment
from app.models.campaign import CampaignUserAssignment
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
    email: str,
    role_code: str,
    password: str = TEST_PASSWORD,
    scope_type: str = "organization",
    scope_id: uuid.UUID | None = None,
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
                scope_id=scope_id,
                effective_from=datetime.now(UTC),
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
def manager_client() -> Iterator[TestClient]:
    from app.authz.capabilities import ROLE_MANAGER
    from app.main import app

    email = f"manager-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(email, ROLE_MANAGER)
    with TestClient(app) as role_client:
        login(role_client, email)
        yield role_client


@pytest.fixture
def agent_client() -> Iterator[TestClient]:
    from app.authz.capabilities import ROLE_AGENT
    from app.main import app

    email = f"agent-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(email, ROLE_AGENT)
    with TestClient(app) as role_client:
        login(role_client, email)
        yield role_client


def zw_numbers(count: int) -> list[str]:
    """A handful of distinct, individually-valid Zimbabwe national numbers, derived by
    varying the trailing digit of the library's own example number and keeping only
    variants that still validate. Not exhaustive, but stable enough for test fixtures."""
    import phonenumbers

    example = phonenumbers.example_number("ZW")
    if example is None:
        pytest.skip("no example number available for region ZW")
    base = str(example.national_number)
    prefix, last = base[:-1], base[-1]
    candidates = [base] + [prefix + str(d) for d in range(10) if str(d) != last]

    valid = []
    for candidate in candidates:
        try:
            parsed = phonenumbers.parse(candidate, "ZW")
        except phonenumbers.NumberParseException:
            continue
        if phonenumbers.is_valid_number(parsed):
            valid.append(candidate)
        if len(valid) >= count:
            break

    if len(valid) < count:
        pytest.skip(f"could not derive {count} distinct valid ZW numbers for this fixture")
    return valid[:count]


def assign_agent_to_campaign(
    agent_id: uuid.UUID, campaign_id: uuid.UUID, *, assignment_type: str = "primary"
) -> None:
    """Direct DB insert: campaign-user-assignment issuance has no API yet (Phase 4)."""
    with SessionLocal() as db:
        db.add(
            CampaignUserAssignment(
                campaign_id=campaign_id,
                user_id=agent_id,
                campaign_role="agent",
                assignment_type=assignment_type,
                effective_from=datetime.now(UTC),
                status="active",
            )
        )
        db.commit()
