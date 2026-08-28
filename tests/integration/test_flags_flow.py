"""Integration tests for the feature-flags system (master plan 21.2): the
service layer (seeding, the ai_enabled hard lock, audit) and each of the five
real enforcement points, proving both directions - off rejects cleanly, on is
unaffected - not just one. The web (/flags) and JSON (/api/v1/flags) surfaces
get one end-to-end test each; the exception-to-response mapping itself is
simple enough not to need re-proving per flag.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.campaigns import service as campaign_service
from app.db import SessionLocal
from app.flags import service as flags_service
from app.flags.service import (
    FeatureDisabledError,
    PermanentlyDisabledFlag,
    UnknownFlag,
)
from app.imports import service as import_service
from app.models.audit import AuditEvent
from app.models.campaign import Campaign, CampaignDispositionDefinition
from app.models.contact import CampaignContact, Contact
from app.models.identity import User
from app.models.work import WorkItem
from app.security.passwords import hash_password
from app.security.phone import protect
from app.work import service as work_service
from app.workforce import service as workforce_service
from tests.integration.conftest import (
    TEST_PASSWORD,
    assign_agent_to_campaign,
    csrf_headers,
    login,
    make_user_with_role,
    zw_numbers,
)

pytestmark = pytest.mark.integration


def _set_flag(flag_key: str, enabled: bool, *, actor_id: uuid.UUID) -> None:
    with SessionLocal() as db:
        flags_service.set_flag(db, flag_key, enabled, actor_id=actor_id)
        db.commit()


def _new_user(prefix: str) -> uuid.UUID:
    with SessionLocal() as db:
        user = User(
            workforce_id=f"{prefix}-{uuid.uuid4().hex[:8]}",
            email=f"{prefix}-{uuid.uuid4().hex[:8]}@example.com",
            display_name="Flags Test User",
            password_hash=hash_password(TEST_PASSWORD),
        )
        db.add(user)
        db.commit()
        return user.id


def _draft_campaign(actor_id: uuid.UUID) -> uuid.UUID:
    """A draft campaign with one committed contact - launch_campaign's own
    precondition (a campaign with zero contacts can never launch, flag or no
    flag), not something the flags feature itself needs to be bare for."""
    number = zw_numbers(1)[0]
    with SessionLocal() as db:
        campaign = Campaign(
            owning_scope_type="organization",
            name=f"Flags test {uuid.uuid4().hex[:6]}",
            default_region="ZW",
            timezone="Africa/Harare",
            status="draft",
            purpose="Test",
            data_source="Test",
            data_obtained_at=datetime.now(UTC).date(),
            lawful_basis_or_consent_reference="TEST",
            created_by=actor_id,
        )
        db.add(campaign)
        db.flush()
        protected = protect(number, "ZW")
        contact = Contact(
            phone_ciphertext=protected.ciphertext, phone_fingerprint=protected.fingerprint,
        )
        db.add(contact)
        db.flush()
        db.add(
            CampaignContact(
                campaign_id=campaign.id, contact_id=contact.id, status="queued",
                imported_at=datetime.now(UTC),
            )
        )
        db.commit()
        return campaign.id


def _launched_campaign_with_work_item(actor_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    """A campaign with one queued work item and one active disposition, ready to
    lease and complete - built directly, not through the import pipeline, so this
    setup doesn't itself depend on campaign_import_enabled staying on."""
    number = zw_numbers(1)[0]
    with SessionLocal() as db:
        campaign = Campaign(
            owning_scope_type="organization",
            name=f"Flags leasing test {uuid.uuid4().hex[:6]}",
            default_region="ZW",
            timezone="Africa/Harare",
            status="active",
            purpose="Test",
            data_source="Test",
            data_obtained_at=datetime.now(UTC).date(),
            lawful_basis_or_consent_reference="TEST",
            created_by=actor_id,
        )
        db.add(campaign)
        db.flush()
        disposition = CampaignDispositionDefinition(
            campaign_id=campaign.id,
            label="Complete",
            stable_semantic_code=f"complete_{uuid.uuid4().hex[:8]}",
            next_action="complete",
        )
        db.add(disposition)
        protected = protect(number, "ZW")
        contact = Contact(
            phone_ciphertext=protected.ciphertext, phone_fingerprint=protected.fingerprint,
        )
        db.add(contact)
        db.flush()
        campaign_contact = CampaignContact(
            campaign_id=campaign.id, contact_id=contact.id, status="queued",
            imported_at=datetime.now(UTC),
        )
        db.add(campaign_contact)
        db.flush()
        db.add(WorkItem(campaign_contact_id=campaign_contact.id, state="queued", priority=0))
        db.commit()
        return campaign.id, disposition.id


# ---------------------------------------------------------------------------
# Service layer
# ---------------------------------------------------------------------------


def test_seeded_defaults_match_shipped_behavior():
    """A flag defaulting to a value that breaks an already-working feature would
    be a regression, not a rollout gate - this is the migration's seed data,
    not just service-layer logic, so it's worth asserting directly."""
    with SessionLocal() as db:
        seeded = {flag.flag_key: flag.enabled for flag in flags_service.list_flags(db)}
    assert seeded["campaign_import_enabled"] is True
    assert seeded["campaign_launch_enabled"] is True
    assert seeded["shared_pool_enabled"] is True
    assert seeded["callbacks_enabled"] is True
    assert seeded["viewer_enabled"] is True
    assert seeded["retention_execution_enabled"] is False
    assert seeded["analytics_enabled"] is False
    assert seeded["ai_enabled"] is False


def test_is_enabled_fails_safe_for_unknown_key():
    with SessionLocal() as db:
        assert flags_service.is_enabled(db, f"not-a-real-flag-{uuid.uuid4().hex[:8]}") is False


def test_set_flag_rejects_unknown_key():
    actor_id = _new_user("flagsetter")
    with SessionLocal() as db:
        with pytest.raises(UnknownFlag):
            flags_service.set_flag(
                db, f"not-a-real-flag-{uuid.uuid4().hex[:8]}", True, actor_id=actor_id
            )


def test_ai_enabled_cannot_be_turned_on():
    actor_id = _new_user("aiattempt")
    with SessionLocal() as db:
        with pytest.raises(PermanentlyDisabledFlag):
            flags_service.set_flag(db, "ai_enabled", True, actor_id=actor_id)
        # The attempt must not have silently partially applied.
        assert flags_service.is_enabled(db, "ai_enabled") is False
    # Disabling it (a no-op, since it's already off) is not itself forbidden -
    # only turning it on is.
    with SessionLocal() as db:
        flags_service.set_flag(db, "ai_enabled", False, actor_id=actor_id)
        db.commit()


def test_set_flag_is_audited():
    actor_id = _new_user("flagsetter")
    with SessionLocal() as db:
        flags_service.set_flag(
            db, "retention_execution_enabled", True, actor_id=actor_id, reason_code="test_audit"
        )
        db.commit()
    with SessionLocal() as db:
        event = db.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "flags.set", AuditEvent.actor_user_id == actor_id,
            )
        )
        assert event is not None
        assert event.event_metadata == {"flag_key": "retention_execution_enabled", "enabled": True}
    # Restore the seeded default so this test doesn't leak state into others.
    with SessionLocal() as db:
        flags_service.set_flag(db, "retention_execution_enabled", False, actor_id=actor_id)
        db.commit()


# ---------------------------------------------------------------------------
# Enforcement: campaign_import_enabled
# ---------------------------------------------------------------------------


def test_campaign_import_enabled_gates_new_imports():
    actor_id = _new_user("importmgr")
    campaign_id = _draft_campaign(actor_id)
    number = zw_numbers(1)[0]

    _set_flag("campaign_import_enabled", False, actor_id=actor_id)
    try:
        with SessionLocal() as db:
            campaign = db.get(Campaign, campaign_id)
            with pytest.raises(FeatureDisabledError):
                import_service.create_import_job(
                    db, campaign=campaign, uploader_id=actor_id,
                    display_filename="c.csv",
                    file_chunks=[f"phone,name\n{number},Alice\n".encode()],
                )
    finally:
        _set_flag("campaign_import_enabled", True, actor_id=actor_id)

    with SessionLocal() as db:
        campaign = db.get(Campaign, campaign_id)
        job = import_service.create_import_job(
            db, campaign=campaign, uploader_id=actor_id,
            display_filename="c.csv",
            file_chunks=[f"phone,name\n{number},Alice\n".encode()],
        )
        db.commit()
        assert job is not None


# ---------------------------------------------------------------------------
# Enforcement: campaign_launch_enabled
# ---------------------------------------------------------------------------


def test_campaign_launch_enabled_gates_launch():
    actor_id = _new_user("launchmgr")
    campaign_id = _draft_campaign(actor_id)

    _set_flag("campaign_launch_enabled", False, actor_id=actor_id)
    try:
        with SessionLocal() as db:
            campaign = db.get(Campaign, campaign_id)
            with pytest.raises(FeatureDisabledError):
                campaign_service.launch_campaign(db, campaign, actor_id=actor_id)
    finally:
        _set_flag("campaign_launch_enabled", True, actor_id=actor_id)

    with SessionLocal() as db:
        campaign = db.get(Campaign, campaign_id)
        campaign_service.launch_campaign(db, campaign, actor_id=actor_id)
        db.commit()
        assert campaign.status == "active"


# ---------------------------------------------------------------------------
# Enforcement: shared_pool_enabled
# ---------------------------------------------------------------------------


def test_shared_pool_enabled_gates_new_leases_but_not_resuming_one():
    actor_id = _new_user("poolmgr")
    agent_id = _new_user("poolagent")
    campaign_id, _ = _launched_campaign_with_work_item(actor_id)
    assign_agent_to_campaign(agent_id, campaign_id)

    _set_flag("shared_pool_enabled", False, actor_id=actor_id)
    try:
        with SessionLocal() as db:
            with pytest.raises(FeatureDisabledError):
                work_service.lease_next(db, agent_id)
    finally:
        _set_flag("shared_pool_enabled", True, actor_id=actor_id)

    with SessionLocal() as db:
        result = work_service.lease_next(db, agent_id)
        db.commit()
        assert result is not None

    # Now that the agent holds an active lease, turning the flag off again must
    # not block them from resuming it - only from acquiring a *new* one.
    _set_flag("shared_pool_enabled", False, actor_id=actor_id)
    try:
        with SessionLocal() as db:
            resumed = work_service.lease_next(db, agent_id)
            assert resumed is not None
            assert resumed.work_item_id == result.work_item_id
    finally:
        _set_flag("shared_pool_enabled", True, actor_id=actor_id)


# ---------------------------------------------------------------------------
# Enforcement: callbacks_enabled
# ---------------------------------------------------------------------------


def test_callbacks_enabled_gates_scheduling_a_callback_not_plain_completion():
    actor_id = _new_user("cbmgr")
    agent_id = _new_user("cbagent")
    campaign_id, disposition_id = _launched_campaign_with_work_item(actor_id)
    assign_agent_to_campaign(agent_id, campaign_id)
    with SessionLocal() as db:
        lease = work_service.lease_next(db, agent_id)
        db.commit()
    assert lease is not None

    future_callback = datetime.now(UTC).replace(microsecond=0) + timedelta(hours=1)

    _set_flag("callbacks_enabled", False, actor_id=actor_id)
    try:
        with SessionLocal() as db:
            with pytest.raises(FeatureDisabledError):
                work_service.complete_work_item(
                    db, work_item_id=lease.work_item_id, agent_id=agent_id,
                    lease_id=lease.lease_id, disposition_id=disposition_id,
                    notes=None, callback_at=future_callback,
                    self_reported_duration_seconds=None,
                    idempotency_key=str(uuid.uuid4()),
                )
        # A completion that does NOT try to schedule a callback is unaffected.
        with SessionLocal() as db:
            result = work_service.complete_work_item(
                db, work_item_id=lease.work_item_id, agent_id=agent_id,
                lease_id=lease.lease_id, disposition_id=disposition_id,
                notes=None, callback_at=None, self_reported_duration_seconds=None,
                idempotency_key=str(uuid.uuid4()),
            )
            db.commit()
            assert result.callback_at is None
    finally:
        _set_flag("callbacks_enabled", True, actor_id=actor_id)


# ---------------------------------------------------------------------------
# Enforcement: viewer_enabled
# ---------------------------------------------------------------------------


def test_viewer_enabled_gates_new_viewer_grants_only():
    # workforce_service.assign_role checks the flag and target-role rules, not
    # the actor's own capability - that pre-check lives in the API/web callers
    # (already covered elsewhere), so actor_id needs no role of its own here.
    actor_id = _new_user("viewermgr")
    target_id = _new_user("viewertarget")
    other_target_id = _new_user("agenttarget")

    _set_flag("viewer_enabled", False, actor_id=actor_id)
    try:
        with SessionLocal() as db:
            with pytest.raises(FeatureDisabledError):
                workforce_service.assign_role(
                    db, target_user_id=target_id, role_code="viewer",
                    scope_type="organization", scope_id=None, appointed_by=actor_id,
                )
        # A non-viewer role grant is unaffected by the same flag.
        with SessionLocal() as db:
            assignment = workforce_service.assign_role(
                db, target_user_id=other_target_id, role_code="agent",
                scope_type="organization", scope_id=None, appointed_by=actor_id,
            )
            db.commit()
            assert assignment.role_code == "agent"
    finally:
        _set_flag("viewer_enabled", True, actor_id=actor_id)

    with SessionLocal() as db:
        assignment = workforce_service.assign_role(
            db, target_user_id=target_id, role_code="viewer",
            scope_type="organization", scope_id=None, appointed_by=actor_id,
        )
        db.commit()
        assert assignment.role_code == "viewer"


# ---------------------------------------------------------------------------
# Surfaces: /flags (web) and /api/v1/flags (JSON)
# ---------------------------------------------------------------------------


def test_agent_cannot_view_flags_page():
    from app.main import app

    email = f"flagsagent-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(email, "agent")
    client = TestClient(app, follow_redirects=False)
    login(client, email)
    resp = client.get("/flags")
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/dashboard")


def test_manager_can_view_and_toggle_flags_via_web(manager_client):
    resp = manager_client.get("/flags")
    assert resp.status_code == 200
    assert "retention_execution_enabled" in resp.text

    resp = manager_client.post(
        "/flags/retention_execution_enabled",
        data={"csrf_token": csrf_headers(manager_client)["x-csrf-token"], "enabled": "true"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    with SessionLocal() as db:
        assert flags_service.is_enabled(db, "retention_execution_enabled") is True
    # Restore.
    manager_client.post(
        "/flags/retention_execution_enabled",
        data={"csrf_token": csrf_headers(manager_client)["x-csrf-token"], "enabled": "false"},
    )


def test_ai_enabled_cannot_be_toggled_on_via_web(manager_client):
    resp = manager_client.post(
        "/flags/ai_enabled",
        data={"csrf_token": csrf_headers(manager_client)["x-csrf-token"], "enabled": "true"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "error" in resp.headers["location"]
    with SessionLocal() as db:
        assert flags_service.is_enabled(db, "ai_enabled") is False


def test_flags_json_api_list_and_set(manager_client):
    resp = manager_client.get("/api/v1/flags")
    assert resp.status_code == 200
    keys = {flag["flag_key"] for flag in resp.json()}
    assert "shared_pool_enabled" in keys

    resp = manager_client.post(
        "/api/v1/flags/analytics_enabled",
        json={"enabled": True},
        headers=csrf_headers(manager_client),
    )
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True
    # Restore.
    manager_client.post(
        "/api/v1/flags/analytics_enabled",
        json={"enabled": False},
        headers=csrf_headers(manager_client),
    )


def test_flags_json_api_rejects_ai_enabled(manager_client):
    resp = manager_client.post(
        "/api/v1/flags/ai_enabled", json={"enabled": True}, headers=csrf_headers(manager_client),
    )
    assert resp.status_code == 409


def test_flags_json_api_rejects_unknown_flag(manager_client):
    resp = manager_client.post(
        "/api/v1/flags/not-a-real-flag",
        json={"enabled": True},
        headers=csrf_headers(manager_client),
    )
    assert resp.status_code == 404


def test_agent_cannot_use_flags_json_api(agent_client):
    resp = agent_client.get("/api/v1/flags")
    assert resp.status_code == 403
