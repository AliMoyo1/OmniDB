"""Shared fixtures for integration tests (require a real Postgres and Redis)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pyotp
import pytest
from fastapi.testclient import TestClient

from app.auth import totp as totp_mod
from app.db import SessionLocal
from app.models.authz import RoleAssignment
from app.models.campaign import CampaignUserAssignment
from app.models.identity import User
from app.security.passwords import hash_password

TEST_PASSWORD = "correct horse battery staple"
TEST_TOTP_SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"


@pytest.fixture
def client() -> TestClient:
    from app.main import app

    return TestClient(app)


def make_user(
    email: str, password: str = TEST_PASSWORD, *, totp_enrolled: bool = True
) -> uuid.UUID:
    with SessionLocal() as db:
        user = User(
            workforce_id=email.split("@")[0],
            email=email,
            display_name="Test User",
            password_hash=hash_password(password),
            totp_secret_ciphertext=(
                totp_mod.encrypt_secret(TEST_TOTP_SECRET) if totp_enrolled else None
            ),
            totp_enrolled=totp_enrolled,
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
    totp_enrolled: bool = True,
) -> uuid.UUID:
    with SessionLocal() as db:
        user = User(
            workforce_id=email.split("@")[0],
            email=email,
            display_name="Test User",
            password_hash=hash_password(password),
            totp_secret_ciphertext=(
                totp_mod.encrypt_secret(TEST_TOTP_SECRET) if totp_enrolled else None
            ),
            totp_enrolled=totp_enrolled,
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
    response = client.post(
        "/api/v1/auth/login",
        json={
            "email": email,
            "password": password,
            "totp_code": pyotp.TOTP(TEST_TOTP_SECRET).now(),
        },
    )
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
    """count distinct, individually-valid Zimbabwe national numbers, derived by
    randomizing the trailing 4 digits of the library's own example number and keeping
    only variants that still validate. Randomized rather than fixed: DNC-suppression
    tests permanently and correctly suppress whichever number they touch (suppression
    is real, cross-campaign, and not rolled back between tests), so two unrelated
    tests must never be handed the same number in the same run."""
    import secrets

    import phonenumbers

    example = phonenumbers.example_number("ZW")
    if example is None:
        pytest.skip("no example number available for region ZW")
    base = str(example.national_number)
    prefix, suffix_len = base[:-4], 4

    seen: set[str] = set()
    valid: list[str] = []
    for _ in range(500):
        if len(valid) >= count:
            break
        candidate = prefix + f"{secrets.randbelow(10**suffix_len):0{suffix_len}d}"
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            parsed = phonenumbers.parse(candidate, "ZW")
        except phonenumbers.NumberParseException:
            continue
        if phonenumbers.is_valid_number(parsed):
            valid.append(candidate)

    if len(valid) < count:
        pytest.skip(f"could not derive {count} distinct valid ZW numbers for this fixture")
    return valid


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
