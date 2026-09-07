"""Integration tests for the server-rendered delegation console (/delegations).

Thin web layer over the delegation service, so these focus on what the browser
path adds: the page renders a user's granted and held delegations, the create
form enforces step-up re-authentication (as the JSON API does), revoke works from
the page, and - the increment-2 hardening - a capability held only through a
delegation cannot be re-delegated from here. Real Postgres.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.sessions import hash_token
from app.authz import delegations as delegation_service
from app.authz import service as authz
from app.authz.capabilities import APPOINT_TEAM_CAPTAIN, MANAGE_ROLES
from app.db import SessionLocal
from app.models.session import Session as SessionModel
from tests.integration.conftest import login, make_user, make_user_with_role

pytestmark = pytest.mark.integration

_CAP_TEXT = APPOINT_TEAM_CAPTAIN.replace("_", " ")


def _client(email: str) -> TestClient:
    from app.main import app

    client = TestClient(app, follow_redirects=False)
    login(client, email)  # a fresh login is recently reauthenticated
    return client


def _csrf(client: TestClient) -> str:
    token = client.cookies.get("cc_csrf")
    assert token, "csrf cookie not set; did you log in first?"
    return token


def _stale_reauth(client: TestClient) -> None:
    token = client.cookies.get("cc_session")
    with SessionLocal() as db:
        session = db.scalar(
            select(SessionModel).where(SessionModel.token_hash == hash_token(token))
        )
        session.reauthenticated_at = datetime.now(UTC) - timedelta(hours=1)
        db.commit()


def _grant_via_service(
    delegator_id: uuid.UUID, delegate_id: uuid.UUID, caps: list[str]
) -> uuid.UUID:
    with SessionLocal() as db:
        delegation = delegation_service.create_delegation(
            db, delegator_id=delegator_id, delegate_id=delegate_id,
            capability_set=caps, scope_type="organization", scope_id=None,
            effective_from=datetime.now(UTC) - timedelta(minutes=1), effective_to=None,
            reason_code="cover",
        )
        db.commit()
        return delegation.id


def _org_form(client: TestClient, delegate_id: uuid.UUID, caps: list[str]) -> dict:
    return {
        "csrf_token": _csrf(client),
        "delegate_id": str(delegate_id),
        "capabilities": caps,
        "scope_type": "organization",
        "effective_from": "",
        "effective_to": "",
        "reason_code": "covering leave",
    }


def test_web_page_renders_granted_and_held():
    mgr_email = f"delw-mgr-{uuid.uuid4().hex[:8]}@example.com"
    delegator_id = make_user_with_role(mgr_email, "manager")
    delegate_email = f"delw-to-{uuid.uuid4().hex[:8]}@example.com"
    delegate_id = make_user(delegate_email)
    _grant_via_service(delegator_id, delegate_id, [APPOINT_TEAM_CAPTAIN])

    granter = _client(mgr_email)
    granted_page = granter.get("/delegations")
    assert granted_page.status_code == 200
    assert "Delegations you granted" in granted_page.text
    assert _CAP_TEXT in granted_page.text
    assert "active" in granted_page.text

    holder = _client(delegate_email)
    held_page = holder.get("/delegations")
    assert held_page.status_code == 200
    assert "Delegations you hold" in held_page.text
    assert _CAP_TEXT in held_page.text


def test_web_create_grants_a_capability():
    mgr_email = f"delw-cmgr-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(mgr_email, "manager")
    delegate_id = make_user(f"delw-cto-{uuid.uuid4().hex[:8]}@example.com")

    with SessionLocal() as db:
        assert not authz.has_scope_capability(
            db, delegate_id, APPOINT_TEAM_CAPTAIN, scope_type="organization", scope_id=None
        )

    client = _client(mgr_email)
    resp = client.post("/delegations", data=_org_form(client, delegate_id, [APPOINT_TEAM_CAPTAIN]))
    assert resp.status_code == 303
    assert "flash_success" in resp.headers["location"]

    with SessionLocal() as db:
        assert authz.has_scope_capability(
            db, delegate_id, APPOINT_TEAM_CAPTAIN, scope_type="organization", scope_id=None
        )


def test_web_create_requires_step_up():
    mgr_email = f"delw-smgr-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(mgr_email, "manager")
    delegate_id = make_user(f"delw-sto-{uuid.uuid4().hex[:8]}@example.com")

    client = _client(mgr_email)
    _stale_reauth(client)
    resp = client.post("/delegations", data=_org_form(client, delegate_id, [APPOINT_TEAM_CAPTAIN]))
    assert resp.status_code == 303
    assert "flash_error" in resp.headers["location"]

    # Nothing was granted.
    with SessionLocal() as db:
        assert not authz.has_scope_capability(
            db, delegate_id, APPOINT_TEAM_CAPTAIN, scope_type="organization", scope_id=None
        )


def test_web_revoke_drops_the_grant():
    mgr_email = f"delw-rmgr-{uuid.uuid4().hex[:8]}@example.com"
    delegator_id = make_user_with_role(mgr_email, "manager")
    delegate_id = make_user(f"delw-rto-{uuid.uuid4().hex[:8]}@example.com")
    delegation_id = _grant_via_service(delegator_id, delegate_id, [APPOINT_TEAM_CAPTAIN])

    client = _client(mgr_email)
    resp = client.post(
        f"/delegations/{delegation_id}/revoke", data={"csrf_token": _csrf(client)}
    )
    assert resp.status_code == 303
    assert "flash_success" in resp.headers["location"]

    with SessionLocal() as db:
        assert not authz.has_scope_capability(
            db, delegate_id, APPOINT_TEAM_CAPTAIN, scope_type="organization", scope_id=None
        )


def test_web_create_rejects_a_non_delegable_capability():
    mgr_email = f"delw-nmgr-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(mgr_email, "manager")
    delegate_id = make_user(f"delw-nto-{uuid.uuid4().hex[:8]}@example.com")

    client = _client(mgr_email)
    resp = client.post("/delegations", data=_org_form(client, delegate_id, [MANAGE_ROLES]))
    assert resp.status_code == 303
    assert "flash_error" in resp.headers["location"]

    with SessionLocal() as db:
        assert not authz.has_assigned_capability(db, delegate_id, MANAGE_ROLES)


def test_web_cannot_redelegate_a_delegation_only_capability():
    # A manager lends APPOINT_TEAM_CAPTAIN to B. B holds it only through that
    # delegation, so B must not be able to re-delegate it to C from the web form.
    mgr_email = f"delw-chain-mgr-{uuid.uuid4().hex[:8]}@example.com"
    manager_id = make_user_with_role(mgr_email, "manager")
    b_email = f"delw-chain-b-{uuid.uuid4().hex[:8]}@example.com"
    b_id = make_user(b_email)
    c_id = make_user(f"delw-chain-c-{uuid.uuid4().hex[:8]}@example.com")
    _grant_via_service(manager_id, b_id, [APPOINT_TEAM_CAPTAIN])

    # B genuinely holds the capability (can exercise it)...
    with SessionLocal() as db:
        assert authz.has_scope_capability(
            db, b_id, APPOINT_TEAM_CAPTAIN, scope_type="organization", scope_id=None
        )

    # ...but re-delegating it is refused, and C gains nothing.
    b_client = _client(b_email)
    resp = b_client.post("/delegations", data=_org_form(b_client, c_id, [APPOINT_TEAM_CAPTAIN]))
    assert resp.status_code == 303
    assert "flash_error" in resp.headers["location"]

    with SessionLocal() as db:
        assert not authz.has_scope_capability(
            db, c_id, APPOINT_TEAM_CAPTAIN, scope_type="organization", scope_id=None
        )
