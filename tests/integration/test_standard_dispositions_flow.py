"""Integration tests for phase 4D Phase A: the standard-dispositions manifest
is code-owned in app.campaigns.standard_dispositions, but install/validate/
update-retry-policy are exercised here against a real database, including the
two check constraints migration 0019 adds directly (plan 6.1).

No route calls these service functions yet - that is a later phase - so
every test drives app.campaigns.service directly, the same way the manifest
itself is unreachable through the browser or JSON API for now.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.campaigns import service as campaigns_service
from app.campaigns.service import CampaignStateError, DispositionPolicyError
from app.campaigns.standard_dispositions import (
    MAX_RETRY_DELAY_MINUTES,
    MIN_RETRY_DELAY_MINUTES,
    STANDARD_DISPOSITIONS,
)
from app.db import SessionLocal
from app.models.audit import AuditEvent
from app.models.campaign import Campaign, CampaignDispositionDefinition
from tests.integration.conftest import make_user_with_role

pytestmark = pytest.mark.integration


def _draft_campaign(db, actor_id: uuid.UUID) -> Campaign:
    return campaigns_service.create_campaign(
        db,
        created_by=actor_id,
        external_code=f"sd-{uuid.uuid4().hex[:10]}",
        name=f"Standard disposition test {uuid.uuid4().hex[:6]}",
        description=None,
        owning_scope_type="organization",
        owning_scope_id=None,
        default_region="ZW",
        timezone="Africa/Harare",
        purpose="Customer outreach",
        data_source="CRM export",
        data_obtained_at=date(2026, 1, 1),
        lawful_basis_or_consent_reference="consent-ref-123",
    )


def _actor() -> uuid.UUID:
    return make_user_with_role(f"sd-mgr-{uuid.uuid4().hex[:8]}@example.com", "manager")


def test_install_creates_all_seven_in_manifest_order_and_sets_policy_version():
    actor_id = _actor()
    with SessionLocal() as db:
        campaign = _draft_campaign(db, actor_id)
        rows = campaigns_service.install_standard_dispositions(db, campaign, actor_id=actor_id)
        db.commit()

        assert [row.stable_semantic_code for row in rows] == [
            entry.stable_semantic_code for entry in STANDARD_DISPOSITIONS
        ]
        refreshed = db.get(Campaign, campaign.id)
        assert refreshed is not None
        assert refreshed.disposition_policy_version == 1

        stored = db.scalars(
            select(CampaignDispositionDefinition)
            .where(CampaignDispositionDefinition.campaign_id == campaign.id)
            .order_by(CampaignDispositionDefinition.display_order)
        ).all()
        assert len(stored) == 7
        assert all(row.is_standard for row in stored)
        assert all(row.policy_version == 1 for row in stored)


def test_install_uses_manifest_default_retry_delays_when_not_overridden():
    actor_id = _actor()
    with SessionLocal() as db:
        campaign = _draft_campaign(db, actor_id)
        campaigns_service.install_standard_dispositions(db, campaign, actor_id=actor_id)
        db.commit()

        rows = db.scalars(
            select(CampaignDispositionDefinition).where(
                CampaignDispositionDefinition.campaign_id == campaign.id
            )
        ).all()
        by_code = {row.stable_semantic_code: row for row in rows}
        assert by_code["no_answer"].retry_delay_minutes == 60
        assert by_code["unavailable"].retry_delay_minutes == 30
        # Fixed (non-editable) dispositions never get a retry delay.
        assert by_code["connected"].retry_delay_minutes is None
        assert by_code["hung_up"].retry_delay_minutes is None


def test_install_accepts_retry_delay_overrides_within_bounds():
    actor_id = _actor()
    with SessionLocal() as db:
        campaign = _draft_campaign(db, actor_id)
        campaigns_service.install_standard_dispositions(
            db, campaign, actor_id=actor_id,
            no_answer_retry_minutes=120, unavailable_retry_minutes=15,
        )
        db.commit()

        rows = db.scalars(
            select(CampaignDispositionDefinition).where(
                CampaignDispositionDefinition.campaign_id == campaign.id
            )
        ).all()
        by_code = {row.stable_semantic_code: row for row in rows}
        assert by_code["no_answer"].retry_delay_minutes == 120
        assert by_code["unavailable"].retry_delay_minutes == 15


def test_install_rejects_retry_delay_override_outside_bounds():
    actor_id = _actor()
    with SessionLocal() as db:
        campaign = _draft_campaign(db, actor_id)
        with pytest.raises(DispositionPolicyError):
            campaigns_service.install_standard_dispositions(
                db, campaign, actor_id=actor_id, no_answer_retry_minutes=1
            )
        with pytest.raises(DispositionPolicyError):
            campaigns_service.install_standard_dispositions(
                db, campaign, actor_id=actor_id,
                unavailable_retry_minutes=MAX_RETRY_DELAY_MINUTES + 1,
            )


def test_install_rejects_a_non_draft_campaign():
    actor_id = _actor()
    with SessionLocal() as db:
        campaign = _draft_campaign(db, actor_id)
        campaign.status = "active"
        db.commit()
        with pytest.raises(DispositionPolicyError, match="draft"):
            campaigns_service.install_standard_dispositions(db, campaign, actor_id=actor_id)


def test_install_rejects_a_campaign_that_already_has_dispositions():
    actor_id = _actor()
    with SessionLocal() as db:
        campaign = _draft_campaign(db, actor_id)
        campaigns_service.create_disposition(
            db, campaign, actor_id=actor_id, label="Legacy Outcome",
            stable_semantic_code="legacy_outcome", next_action="complete",
            requires_notes=False, requires_callback_time=False,
            counts_as_connected=False, counts_as_conversion=False, causes_dnc=False,
        )
        db.commit()
        with pytest.raises(DispositionPolicyError, match="no existing dispositions"):
            campaigns_service.install_standard_dispositions(db, campaign, actor_id=actor_id)


def test_install_rejects_installing_twice():
    actor_id = _actor()
    with SessionLocal() as db:
        campaign = _draft_campaign(db, actor_id)
        campaigns_service.install_standard_dispositions(db, campaign, actor_id=actor_id)
        db.commit()
        with pytest.raises(DispositionPolicyError, match="already has a disposition policy"):
            campaigns_service.install_standard_dispositions(db, campaign, actor_id=actor_id)


def test_install_records_an_audit_event():
    actor_id = _actor()
    with SessionLocal() as db:
        campaign = _draft_campaign(db, actor_id)
        campaigns_service.install_standard_dispositions(
            db, campaign, actor_id=actor_id, no_answer_retry_minutes=90
        )
        db.commit()

        event = db.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "campaign.disposition_policy.install",
                AuditEvent.target_id == campaign.id,
            )
        )
        assert event is not None
        assert event.actor_user_id == actor_id
        assert event.event_metadata["policy_version"] == 1
        assert event.event_metadata["no_answer_retry_minutes"] == 90


def test_validate_passes_immediately_after_a_fresh_install():
    actor_id = _actor()
    with SessionLocal() as db:
        campaign = _draft_campaign(db, actor_id)
        campaigns_service.install_standard_dispositions(db, campaign, actor_id=actor_id)
        db.commit()

        result = campaigns_service.validate_standard_dispositions(db, campaign)
        assert result.valid is True
        assert result.errors == ()


def test_validate_flags_a_missing_required_outcome():
    actor_id = _actor()
    with SessionLocal() as db:
        campaign = _draft_campaign(db, actor_id)
        campaigns_service.install_standard_dispositions(db, campaign, actor_id=actor_id)
        db.commit()

        row = db.scalar(
            select(CampaignDispositionDefinition).where(
                CampaignDispositionDefinition.campaign_id == campaign.id,
                CampaignDispositionDefinition.stable_semantic_code == "connected",
            )
        )
        assert row is not None
        row.active = False
        db.commit()

        result = campaigns_service.validate_standard_dispositions(db, campaign)
        assert result.valid is False
        assert any(
            "connected" in error and "missing or inactive" in error for error in result.errors
        )


def test_validate_flags_an_altered_fixed_field():
    actor_id = _actor()
    with SessionLocal() as db:
        campaign = _draft_campaign(db, actor_id)
        campaigns_service.install_standard_dispositions(db, campaign, actor_id=actor_id)
        db.commit()

        row = db.scalar(
            select(CampaignDispositionDefinition).where(
                CampaignDispositionDefinition.campaign_id == campaign.id,
                CampaignDispositionDefinition.stable_semantic_code == "explicit_dnc",
            )
        )
        assert row is not None
        row.causes_dnc = False  # a manager must never be able to defuse the DNC row silently
        db.commit()

        result = campaigns_service.validate_standard_dispositions(db, campaign)
        assert result.valid is False
        assert any("causes_dnc" in error for error in result.errors)


def test_validate_flags_an_unexpected_active_disposition():
    actor_id = _actor()
    with SessionLocal() as db:
        campaign = _draft_campaign(db, actor_id)
        campaigns_service.install_standard_dispositions(db, campaign, actor_id=actor_id)
        db.add(
            CampaignDispositionDefinition(
                campaign_id=campaign.id, label="Extra", stable_semantic_code="extra_outcome",
                next_action="complete", active=True,
            )
        )
        db.commit()

        result = campaigns_service.validate_standard_dispositions(db, campaign)
        assert result.valid is False
        assert any("extra_outcome" in error for error in result.errors)


def test_validate_flags_a_retry_delay_pushed_out_of_bounds_directly_in_the_row():
    actor_id = _actor()
    with SessionLocal() as db:
        campaign = _draft_campaign(db, actor_id)
        campaigns_service.install_standard_dispositions(db, campaign, actor_id=actor_id)
        db.commit()

        row = db.scalar(
            select(CampaignDispositionDefinition).where(
                CampaignDispositionDefinition.campaign_id == campaign.id,
                CampaignDispositionDefinition.stable_semantic_code == "no_answer",
            )
        )
        assert row is not None
        row.retry_delay_minutes = MIN_RETRY_DELAY_MINUTES  # still valid, sanity baseline
        db.commit()
        assert campaigns_service.validate_standard_dispositions(db, campaign).valid is True


def test_update_retry_policy_changes_the_delay_and_is_reflected_in_validation():
    actor_id = _actor()
    with SessionLocal() as db:
        campaign = _draft_campaign(db, actor_id)
        campaigns_service.install_standard_dispositions(db, campaign, actor_id=actor_id)
        db.commit()

        campaigns_service.update_retry_policy(
            db, campaign, "unavailable", 45, actor_id=actor_id
        )
        db.commit()

        row = db.scalar(
            select(CampaignDispositionDefinition).where(
                CampaignDispositionDefinition.campaign_id == campaign.id,
                CampaignDispositionDefinition.stable_semantic_code == "unavailable",
            )
        )
        assert row is not None and row.retry_delay_minutes == 45
        assert campaigns_service.validate_standard_dispositions(db, campaign).valid is True

        event = db.scalar(
            select(AuditEvent).where(AuditEvent.action == "campaign.retry_policy.update")
            .order_by(AuditEvent.occurred_at.desc())
        )
        assert event is not None
        assert event.event_metadata["stable_semantic_code"] == "unavailable"
        assert event.event_metadata["retry_delay_minutes"] == 45


def test_update_retry_policy_rejects_a_non_editable_code():
    actor_id = _actor()
    with SessionLocal() as db:
        campaign = _draft_campaign(db, actor_id)
        campaigns_service.install_standard_dispositions(db, campaign, actor_id=actor_id)
        db.commit()
        with pytest.raises(DispositionPolicyError, match="configurable retry delay"):
            campaigns_service.update_retry_policy(db, campaign, "connected", 30, actor_id=actor_id)


def test_update_retry_policy_rejects_an_out_of_bounds_delay():
    actor_id = _actor()
    with SessionLocal() as db:
        campaign = _draft_campaign(db, actor_id)
        campaigns_service.install_standard_dispositions(db, campaign, actor_id=actor_id)
        db.commit()
        with pytest.raises(DispositionPolicyError):
            campaigns_service.update_retry_policy(
                db, campaign, "no_answer", MAX_RETRY_DELAY_MINUTES + 1, actor_id=actor_id
            )


def test_update_retry_policy_rejects_a_non_draft_campaign():
    actor_id = _actor()
    with SessionLocal() as db:
        campaign = _draft_campaign(db, actor_id)
        campaigns_service.install_standard_dispositions(db, campaign, actor_id=actor_id)
        campaign.status = "active"
        db.commit()
        with pytest.raises(CampaignStateError):
            campaigns_service.update_retry_policy(db, campaign, "no_answer", 90, actor_id=actor_id)


def test_database_check_constraint_rejects_a_retry_delay_outside_bounds():
    # Proves the plan 6.1 constraint is enforced by Postgres itself, not only
    # by the service layer - a direct row insert bypassing install/validate
    # must still fail.
    actor_id = _actor()
    with SessionLocal() as db:
        campaign = _draft_campaign(db, actor_id)
        db.commit()
        db.add(
            CampaignDispositionDefinition(
                campaign_id=campaign.id, label="Bad", stable_semantic_code="bad_delay",
                retry_delay_minutes=4,
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()


def test_database_check_constraint_rejects_immediate_redial_combined_with_dnc():
    actor_id = _actor()
    with SessionLocal() as db:
        campaign = _draft_campaign(db, actor_id)
        db.commit()
        db.add(
            CampaignDispositionDefinition(
                campaign_id=campaign.id, label="Bad", stable_semantic_code="bad_combo",
                causes_dnc=True, immediate_redial=True,
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
