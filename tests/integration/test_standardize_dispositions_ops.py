"""Integration tests for phase 4D Phase F's operator command: dry-run
classification of every campaign, and the apply mode's narrow, honest scope
(only a brand-new empty draft, or re-validating an already-adopted policy).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.campaigns import service as campaigns_service
from app.campaigns.service import DispositionPolicyError
from app.db import SessionLocal
from app.models.audit import AuditEvent
from app.models.campaign import Campaign, CampaignDispositionDefinition
from app.ops import standardize_dispositions as ops
from tests.integration.conftest import make_user_with_role

pytestmark = pytest.mark.integration


def _campaign(db, *, status: str = "draft") -> Campaign:
    campaign = Campaign(
        owning_scope_type="organization", external_code=f"ops-{uuid.uuid4().hex[:8]}",
        name=f"Ops test {uuid.uuid4().hex[:6]}", default_region="ZW",
        timezone="Africa/Harare", status=status,
    )
    db.add(campaign)
    db.flush()
    return campaign


def _manager_id() -> uuid.UUID:
    return make_user_with_role(f"ops-mgr-{uuid.uuid4().hex[:8]}@example.com", "manager")


def test_classifies_a_new_empty_draft():
    with SessionLocal() as db:
        campaign = _campaign(db)
        db.commit()
        result = ops.classify_campaign(db, campaign)
        assert result.classification == ops.NEW_EMPTY_DRAFT


def test_classifies_a_draft_with_no_code_overlap_as_compatible():
    with SessionLocal() as db:
        campaign = _campaign(db)
        db.add(
            CampaignDispositionDefinition(
                campaign_id=campaign.id, label="Reached", stable_semantic_code="reached_custom",
                next_action="complete",
            )
        )
        db.commit()
        result = ops.classify_campaign(db, campaign)
        assert result.classification == ops.DRAFT_COMPATIBLE
        assert result.conflicting_codes == ()


def test_classifies_a_draft_with_a_mismatched_standard_code_as_conflicting():
    with SessionLocal() as db:
        campaign = _campaign(db)
        # Same stable code as the standard manifest's "connected", but
        # configured differently (missing counts_as_connected) - a real
        # collision, not a coincidence.
        db.add(
            CampaignDispositionDefinition(
                campaign_id=campaign.id, label="Connected", stable_semantic_code="connected",
                next_action="complete", counts_as_connected=False,
            )
        )
        db.commit()
        result = ops.classify_campaign(db, campaign)
        assert result.classification == ops.DRAFT_WITH_CONFLICTS
        assert result.conflicting_codes == ("connected",)


def test_classifies_active_and_paused_campaigns():
    with SessionLocal() as db:
        active = _campaign(db, status="active")
        paused = _campaign(db, status="paused")
        db.commit()
        assert ops.classify_campaign(db, active).classification == ops.ACTIVE_OR_PAUSED
        assert ops.classify_campaign(db, paused).classification == ops.ACTIVE_OR_PAUSED


def test_classifies_completed_and_archived_campaigns():
    with SessionLocal() as db:
        completed = _campaign(db, status="completed")
        archived = _campaign(db, status="archived")
        db.commit()
        assert (
            ops.classify_campaign(db, completed).classification == ops.COMPLETED_OR_ARCHIVED
        )
        assert (
            ops.classify_campaign(db, archived).classification == ops.COMPLETED_OR_ARCHIVED
        )


def test_classifies_an_already_installed_policy():
    with SessionLocal() as db:
        campaign = _campaign(db)
        campaigns_service.install_standard_dispositions(
            db, campaign, actor_id=_manager_id()
        )
        db.commit()
        result = ops.classify_campaign(db, campaign)
        assert result.classification == ops.ALREADY_POLICY_V1


def test_classify_all_campaigns_includes_a_freshly_created_one():
    with SessionLocal() as db:
        campaign = _campaign(db)
        db.commit()
        results = ops.classify_all_campaigns(db)
        assert any(r.campaign_id == campaign.id for r in results)


# --- apply -------------------------------------------------------------------------


def test_apply_installs_the_policy_on_a_new_empty_draft():
    actor_id = _manager_id()
    with SessionLocal() as db:
        campaign = _campaign(db)
        db.commit()
        result = ops.apply_to_campaign(db, campaign, actor_id=actor_id, reason="pilot rollout")
        db.commit()
        assert result.classification == ops.ALREADY_POLICY_V1

        rows = db.scalars(
            select(CampaignDispositionDefinition).where(
                CampaignDispositionDefinition.campaign_id == campaign.id
            )
        ).all()
        assert len(rows) == 7


def test_apply_records_its_own_audit_event_with_the_reason():
    actor_id = _manager_id()
    with SessionLocal() as db:
        campaign = _campaign(db)
        db.commit()
        ops.apply_to_campaign(db, campaign, actor_id=actor_id, reason="pilot rollout batch 1")
        db.commit()
        event = db.scalar(
            select(AuditEvent).where(AuditEvent.action == "ops.standardize_dispositions.apply")
            .order_by(AuditEvent.occurred_at.desc())
        )
        assert event is not None
        assert event.actor_user_id == actor_id
        assert event.reason_code == "pilot rollout batch 1"


def test_apply_is_idempotent_when_run_twice():
    actor_id = _manager_id()
    with SessionLocal() as db:
        campaign = _campaign(db)
        db.commit()
        ops.apply_to_campaign(db, campaign, actor_id=actor_id, reason="first run")
        db.commit()
        # Second run finds it already policy version 1 and just re-validates.
        result = ops.apply_to_campaign(db, campaign, actor_id=actor_id, reason="second run")
        db.commit()
        assert result.classification == ops.ALREADY_POLICY_V1

        rows = db.scalars(
            select(CampaignDispositionDefinition).where(
                CampaignDispositionDefinition.campaign_id == campaign.id
            )
        ).all()
        assert len(rows) == 7  # not duplicated


@pytest.mark.parametrize("status", ["active", "paused", "completed", "archived"])
def test_apply_refuses_every_non_actionable_classification(status):
    actor_id = _manager_id()
    with SessionLocal() as db:
        campaign = _campaign(db, status=status)
        db.commit()
        with pytest.raises(DispositionPolicyError):
            ops.apply_to_campaign(db, campaign, actor_id=actor_id, reason="should not apply")


def test_apply_refuses_a_draft_with_existing_dispositions():
    actor_id = _manager_id()
    with SessionLocal() as db:
        campaign = _campaign(db)
        db.add(
            CampaignDispositionDefinition(
                campaign_id=campaign.id, label="Reached", stable_semantic_code="reached_custom",
                next_action="complete",
            )
        )
        db.commit()
        with pytest.raises(DispositionPolicyError):
            ops.apply_to_campaign(db, campaign, actor_id=actor_id, reason="should not apply")


# --- CLI ---------------------------------------------------------------------------


def test_cli_dry_run_exits_zero(capsys):
    exit_code = ops.main(["--dry-run"])
    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Classified" in captured.out


def test_cli_apply_without_required_arguments_exits_nonzero(capsys):
    exit_code = ops.main(["--apply"])
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "requires" in captured.err


def test_cli_apply_end_to_end():
    actor_id = _manager_id()
    with SessionLocal() as db:
        campaign = _campaign(db)
        db.commit()
        campaign_id = str(campaign.id)

    exit_code = ops.main(
        ["--apply", "--campaign", campaign_id, "--actor", str(actor_id), "--reason", "cli test"]
    )
    assert exit_code == 0

    with SessionLocal() as db:
        refreshed = db.get(Campaign, uuid.UUID(campaign_id))
        assert refreshed.disposition_policy_version == 1
