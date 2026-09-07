"""Integration tests for capability delegation (plan 11.3). Real Postgres.

Covers the security-critical part - that the authorization core honors an active
delegation exactly like a role grant, and nowhere else - plus the management
service's guardrails (no self-delegation, no non-delegable caps, no delegating
authority you don't hold) and the API.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.sessions import hash_token
from app.authz import delegations as delegation_service
from app.authz import service as authz
from app.authz.capabilities import (
    APPOINT_TEAM_CAPTAIN,
    MANAGE_ROLES,
    VIEW_CAMPAIGN,
)
from app.db import SessionLocal
from app.models.identity import Organization, Team
from app.models.session import Session as SessionModel
from tests.integration.conftest import (
    TEST_PASSWORD,
    TEST_TOTP_SECRET,
    csrf_headers,
    login,
    make_user,
    make_user_with_role,
)

pytestmark = pytest.mark.integration

_NOW = lambda: datetime.now(UTC)  # noqa: E731


def _client_for(email: str) -> TestClient:
    from app.main import app

    client = TestClient(app, follow_redirects=False)
    login(client, email)
    return client


def _two_teams() -> tuple[uuid.UUID, uuid.UUID]:
    with SessionLocal() as db:
        org = Organization(name=f"Deleg org {uuid.uuid4().hex[:8]}", status="active")
        db.add(org)
        db.flush()
        a = Team(organization_id=org.id, external_code=f"a-{uuid.uuid4().hex[:8]}", name="A")
        b = Team(organization_id=org.id, external_code=f"b-{uuid.uuid4().hex[:8]}", name="B")
        db.add_all([a, b])
        db.commit()
        return a.id, b.id


# --- Authorization-core resolution -------------------------------------------

def test_active_delegation_grants_a_scoped_capability():
    delegator_id = make_user_with_role(f"deleg-mgr-{uuid.uuid4().hex[:8]}@example.com", "manager")
    delegate_id = make_user(f"deleg-to-{uuid.uuid4().hex[:8]}@example.com")

    with SessionLocal() as db:
        # The delegate holds nothing yet.
        assert not authz.has_scope_capability(
            db, delegate_id, APPOINT_TEAM_CAPTAIN, scope_type="organization", scope_id=None
        )
        delegation_service.create_delegation(
            db, delegator_id=delegator_id, delegate_id=delegate_id,
            capability_set=[APPOINT_TEAM_CAPTAIN], scope_type="organization", scope_id=None,
            effective_from=_NOW() - timedelta(minutes=1), effective_to=None, reason_code="cover",
        )
        db.commit()

    with SessionLocal() as db:
        assert authz.has_scope_capability(
            db, delegate_id, APPOINT_TEAM_CAPTAIN, scope_type="organization", scope_id=None
        )
        assert authz.has_assigned_capability(db, delegate_id, APPOINT_TEAM_CAPTAIN)


def test_delegation_outside_its_window_grants_nothing():
    delegator_id = make_user_with_role(f"deleg-mgr-{uuid.uuid4().hex[:8]}@example.com", "manager")
    delegate_id = make_user(f"deleg-to-{uuid.uuid4().hex[:8]}@example.com")
    now = _NOW()

    with SessionLocal() as db:
        # Not yet effective.
        delegation_service.create_delegation(
            db, delegator_id=delegator_id, delegate_id=delegate_id,
            capability_set=[APPOINT_TEAM_CAPTAIN], scope_type="organization", scope_id=None,
            effective_from=now + timedelta(days=1), effective_to=None, reason_code="future",
        )
        # Already expired.
        delegation_service.create_delegation(
            db, delegator_id=delegator_id, delegate_id=delegate_id,
            capability_set=[APPOINT_TEAM_CAPTAIN], scope_type="organization", scope_id=None,
            effective_from=now - timedelta(days=2), effective_to=now - timedelta(days=1),
            reason_code="past",
        )
        db.commit()

    with SessionLocal() as db:
        assert not authz.has_scope_capability(
            db, delegate_id, APPOINT_TEAM_CAPTAIN, scope_type="organization", scope_id=None
        )


def test_revoking_a_delegation_removes_the_grant():
    delegator_id = make_user_with_role(f"deleg-mgr-{uuid.uuid4().hex[:8]}@example.com", "manager")
    delegate_id = make_user(f"deleg-to-{uuid.uuid4().hex[:8]}@example.com")

    with SessionLocal() as db:
        delegation = delegation_service.create_delegation(
            db, delegator_id=delegator_id, delegate_id=delegate_id,
            capability_set=[APPOINT_TEAM_CAPTAIN], scope_type="organization", scope_id=None,
            effective_from=_NOW() - timedelta(minutes=1), effective_to=None, reason_code="cover",
        )
        db.commit()
        delegation_id = delegation.id

    with SessionLocal() as db:
        assert authz.has_scope_capability(
            db, delegate_id, APPOINT_TEAM_CAPTAIN, scope_type="organization", scope_id=None
        )
        delegation_service.revoke_delegation(db, delegation_id, actor_id=delegator_id)
        db.commit()

    with SessionLocal() as db:
        assert not authz.has_scope_capability(
            db, delegate_id, APPOINT_TEAM_CAPTAIN, scope_type="organization", scope_id=None
        )


def test_delegation_is_confined_to_its_scope():
    team_a, team_b = _two_teams()
    delegator_id = make_user_with_role(f"deleg-mgr-{uuid.uuid4().hex[:8]}@example.com", "manager")
    delegate_id = make_user(f"deleg-to-{uuid.uuid4().hex[:8]}@example.com")

    with SessionLocal() as db:
        delegation_service.create_delegation(
            db, delegator_id=delegator_id, delegate_id=delegate_id,
            capability_set=[APPOINT_TEAM_CAPTAIN], scope_type="team", scope_id=team_a,
            effective_from=_NOW() - timedelta(minutes=1), effective_to=None, reason_code="cover",
        )
        db.commit()

    with SessionLocal() as db:
        assert authz.has_scope_capability(
            db, delegate_id, APPOINT_TEAM_CAPTAIN, scope_type="team", scope_id=team_a
        )
        assert not authz.has_scope_capability(
            db, delegate_id, APPOINT_TEAM_CAPTAIN, scope_type="team", scope_id=team_b
        )


def test_delegation_makes_a_campaign_visible_to_the_delegate():
    from datetime import date

    from app.campaigns import service as campaign_service

    manager_id = make_user_with_role(f"deleg-mgr-{uuid.uuid4().hex[:8]}@example.com", "manager")
    delegate_id = make_user(f"deleg-to-{uuid.uuid4().hex[:8]}@example.com")
    with SessionLocal() as db:
        campaign = campaign_service.create_campaign(
            db, created_by=manager_id, external_code=f"delc-{uuid.uuid4().hex[:8]}",
            name="Deleg campaign", description=None, owning_scope_type="organization",
            owning_scope_id=None, default_region="ZW", timezone="Africa/Harare",
            purpose="t", data_source="t", data_obtained_at=date(2026, 1, 1),
            lawful_basis_or_consent_reference="ref",
        )
        db.commit()
        campaign_id = campaign.id

    with SessionLocal() as db:
        assert not authz.has_campaign_capability(db, delegate_id, VIEW_CAMPAIGN, campaign_id)
        delegation_service.create_delegation(
            db, delegator_id=manager_id, delegate_id=delegate_id,
            capability_set=[VIEW_CAMPAIGN], scope_type="organization", scope_id=None,
            effective_from=_NOW() - timedelta(minutes=1), effective_to=None, reason_code="cover",
        )
        db.commit()

    with SessionLocal() as db:
        assert authz.has_campaign_capability(db, delegate_id, VIEW_CAMPAIGN, campaign_id)


# --- Management guardrails -----------------------------------------------------

def test_self_delegation_is_rejected():
    delegator_id = make_user_with_role(f"deleg-mgr-{uuid.uuid4().hex[:8]}@example.com", "manager")
    with SessionLocal() as db, pytest.raises(delegation_service.SelfDelegation):
        delegation_service.create_delegation(
            db, delegator_id=delegator_id, delegate_id=delegator_id,
            capability_set=[APPOINT_TEAM_CAPTAIN], scope_type="organization", scope_id=None,
            effective_from=_NOW(), effective_to=None, reason_code="x",
        )


def test_non_delegable_capability_is_rejected():
    delegator_id = make_user_with_role(f"deleg-mgr-{uuid.uuid4().hex[:8]}@example.com", "manager")
    delegate_id = make_user(f"deleg-to-{uuid.uuid4().hex[:8]}@example.com")
    with SessionLocal() as db, pytest.raises(delegation_service.NonDelegableCapability):
        delegation_service.create_delegation(
            db, delegator_id=delegator_id, delegate_id=delegate_id,
            capability_set=[MANAGE_ROLES], scope_type="organization", scope_id=None,
            effective_from=_NOW(), effective_to=None, reason_code="x",
        )


def test_unknown_capability_is_rejected():
    delegator_id = make_user_with_role(f"deleg-mgr-{uuid.uuid4().hex[:8]}@example.com", "manager")
    delegate_id = make_user(f"deleg-to-{uuid.uuid4().hex[:8]}@example.com")
    with SessionLocal() as db, pytest.raises(delegation_service.UnknownCapability):
        delegation_service.create_delegation(
            db, delegator_id=delegator_id, delegate_id=delegate_id,
            capability_set=["not_a_real_capability"], scope_type="organization", scope_id=None,
            effective_from=_NOW(), effective_to=None, reason_code="x",
        )


def test_cannot_delegate_a_capability_you_do_not_hold_at_the_scope():
    team_a, team_b = _two_teams()
    # A team leader scoped to team A holds APPOINT_TEAM_CAPTAIN there, not at team B.
    leader_id = make_user_with_role(
        f"deleg-tl-{uuid.uuid4().hex[:8]}@example.com", "team_leader",
        scope_type="team", scope_id=team_a,
    )
    delegate_id = make_user(f"deleg-to-{uuid.uuid4().hex[:8]}@example.com")
    with SessionLocal() as db:
        # At team A they hold it: allowed.
        delegation_service.create_delegation(
            db, delegator_id=leader_id, delegate_id=delegate_id,
            capability_set=[APPOINT_TEAM_CAPTAIN], scope_type="team", scope_id=team_a,
            effective_from=_NOW(), effective_to=None, reason_code="ok",
        )
        # At team B they do not: rejected.
        with pytest.raises(delegation_service.InsufficientDelegatorAuthority):
            delegation_service.create_delegation(
                db, delegator_id=leader_id, delegate_id=delegate_id,
                capability_set=[APPOINT_TEAM_CAPTAIN], scope_type="team", scope_id=team_b,
                effective_from=_NOW(), effective_to=None, reason_code="nope",
            )


# --- API ----------------------------------------------------------------------

def _stale_reauth(client: TestClient) -> None:
    token = client.cookies.get("cc_session")
    with SessionLocal() as db:
        session = db.scalar(
            select(SessionModel).where(SessionModel.token_hash == hash_token(token))
        )
        session.reauthenticated_at = datetime.now(UTC) - timedelta(hours=1)
        db.commit()


def _payload(delegate_id: uuid.UUID, caps: list[str]) -> dict:
    now = datetime.now(UTC)
    return {
        "delegate_id": str(delegate_id),
        "capability_set": caps,
        "scope_type": "organization",
        "scope_id": None,
        "effective_from": now.isoformat(),
        "effective_to": (now + timedelta(days=7)).isoformat(),
        "reason_code": "cover",
    }


def test_api_create_list_and_revoke():
    email = f"deleg-apimgr-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(email, "manager")
    client = _client_for(email)  # a fresh login is recently reauthenticated
    delegate_id = make_user(f"deleg-apito-{uuid.uuid4().hex[:8]}@example.com")

    created = client.post(
        "/api/v1/delegations", json=_payload(delegate_id, [APPOINT_TEAM_CAPTAIN]),
        headers=csrf_headers(client),
    )
    assert created.status_code == 200, created.text
    delegation_id = created.json()["id"]
    assert created.json()["capability_set"] == [APPOINT_TEAM_CAPTAIN]

    listed = client.get("/api/v1/delegations").json()
    assert any(d["id"] == delegation_id for d in listed)

    revoked = client.delete(f"/api/v1/delegations/{delegation_id}", headers=csrf_headers(client))
    assert revoked.status_code == 200, revoked.text
    after = {d["id"]: d for d in client.get("/api/v1/delegations").json()}
    assert after[delegation_id]["revoked_at"] is not None


def test_api_create_requires_step_up():
    email = f"deleg-stepup-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(email, "manager")
    client = _client_for(email)
    delegate_id = make_user(f"deleg-stepto-{uuid.uuid4().hex[:8]}@example.com")
    _stale_reauth(client)

    denied = client.post(
        "/api/v1/delegations", json=_payload(delegate_id, [APPOINT_TEAM_CAPTAIN]),
        headers=csrf_headers(client),
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "reauthentication_required"

    # After reauth it succeeds.
    reauth = client.post(
        "/api/v1/auth/reauthenticate",
        json={"password": TEST_PASSWORD, "totp_code": pyotp.TOTP(TEST_TOTP_SECRET).now()},
        headers=csrf_headers(client),
    )
    assert reauth.status_code == 200, reauth.text
    ok = client.post(
        "/api/v1/delegations", json=_payload(delegate_id, [APPOINT_TEAM_CAPTAIN]),
        headers=csrf_headers(client),
    )
    assert ok.status_code == 200, ok.text


def test_api_create_rejects_delegating_authority_you_lack():
    # An agent holds no delegable capability, so cannot delegate one.
    email = f"deleg-agent-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(email, "agent")
    client = _client_for(email)
    delegate_id = make_user(f"deleg-agto-{uuid.uuid4().hex[:8]}@example.com")

    resp = client.post(
        "/api/v1/delegations", json=_payload(delegate_id, [APPOINT_TEAM_CAPTAIN]),
        headers=csrf_headers(client),
    )
    assert resp.status_code == 403, resp.text
