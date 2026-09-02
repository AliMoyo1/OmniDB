"""DNC must serialize with both leasing and import commit for one fingerprint."""

from __future__ import annotations

import queue
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest
from sqlalchemy import func, select, text

from app.db import SessionLocal
from app.imports import service as import_service
from app.models.campaign import Campaign, CampaignUserAssignment
from app.models.contact import CampaignContact, Contact, SuppressionEntry
from app.models.identity import User
from app.models.imports import ImportDecision, ImportJob, ImportRow
from app.models.work import WorkItem
from app.security.encryption import encrypt
from app.security.passwords import hash_password
from app.security.phone import fingerprint
from app.work import service as work_service

pytestmark = pytest.mark.integration


def _unique_protected_phone() -> tuple[str, str]:
    e164 = f"+26377{uuid.uuid4().int % 100_000_000:08d}"
    return encrypt(e164), fingerprint(e164)


def _new_user(db, prefix: str) -> User:
    suffix = uuid.uuid4().hex[:10]
    user = User(
        workforce_id=f"{prefix}-{suffix}",
        email=f"{prefix}-{suffix}@example.com",
        display_name="Suppression Race User",
        password_hash=hash_password("not-used-in-this-test"),
    )
    db.add(user)
    db.flush()
    return user


def _hold_suppression(
    contact_id: uuid.UUID,
    actor_id: uuid.UUID,
    ready: Event,
    release: Event,
) -> None:
    with SessionLocal() as db:
        contact = db.get(Contact, contact_id)
        assert contact is not None
        work_service._suppress_contact_everywhere(  # noqa: SLF001
            db,
            contact,
            source="explicit_contact_request",
            created_by=actor_id,
        )
        db.flush()
        ready.set()
        if not release.wait(timeout=10):
            raise TimeoutError("test did not release suppression transaction")
        db.commit()


def _wait_for_advisory_lock_wait(backend_pid: int) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with SessionLocal() as db:
            row = db.execute(
                text(
                    "SELECT wait_event_type, wait_event "
                    "FROM pg_stat_activity WHERE pid = :pid"
                ),
                {"pid": backend_pid},
            ).one_or_none()
        if row is not None and row.wait_event_type == "Lock" and row.wait_event == "advisory":
            return
        time.sleep(0.02)
    raise AssertionError("competing transaction never waited on the phone advisory lock")


def _setup_lease_race() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    ciphertext, phone_fingerprint = _unique_protected_phone()
    with SessionLocal() as db:
        actor = _new_user(db, "dnc-actor")
        agent = _new_user(db, "lease-agent")
        campaign = Campaign(
            owning_scope_type="organization",
            external_code=f"c-{uuid.uuid4().hex[:8]}",
            name=f"Lease suppression race {uuid.uuid4().hex[:8]}",
            default_region="ZW",
            timezone="Africa/Harare",
            status="active",
        )
        contact = Contact(
            phone_ciphertext=ciphertext,
            phone_fingerprint=phone_fingerprint,
            phone_key_version=1,
        )
        db.add_all([campaign, contact])
        db.flush()
        campaign_contact = CampaignContact(
            campaign_id=campaign.id,
            contact_id=contact.id,
            original_phone_protected=ciphertext,
            status="queued",
            imported_at=datetime.now(UTC),
        )
        db.add(campaign_contact)
        db.flush()
        work_item = WorkItem(
            campaign_contact_id=campaign_contact.id,
            state="queued",
            priority=0,
        )
        assignment = CampaignUserAssignment(
            campaign_id=campaign.id,
            user_id=agent.id,
            campaign_role="agent",
            assignment_type="primary",
            effective_from=datetime.now(UTC),
            status="active",
        )
        db.add_all([work_item, assignment])
        db.commit()
        return contact.id, actor.id, agent.id


def _lease_with_pid(agent_id: uuid.UUID, pid_queue: queue.Queue[int]):
    with SessionLocal() as db:
        pid = db.scalar(select(func.pg_backend_pid()))
        assert pid is not None
        pid_queue.put(pid)
        result = work_service.lease_next(db, agent_id)
        db.commit()
        return result


def test_dnc_committing_first_prevents_a_concurrent_lease():
    contact_id, actor_id, agent_id = _setup_lease_race()
    suppression_ready = Event()
    release_suppression = Event()
    pid_queue: queue.Queue[int] = queue.Queue()

    with ThreadPoolExecutor(max_workers=2) as pool:
        suppression_future = pool.submit(
            _hold_suppression,
            contact_id,
            actor_id,
            suppression_ready,
            release_suppression,
        )
        assert suppression_ready.wait(timeout=5)
        lease_future = pool.submit(_lease_with_pid, agent_id, pid_queue)
        pid_queue.get(timeout=5)
        try:
            # Leasing does not wait behind a DNC transaction. It skips the busy
            # phone and must not expose the still-uncommitted contact instead.
            lease_result = lease_future.result(timeout=5)
        finally:
            release_suppression.set()

        suppression_future.result(timeout=10)

    assert lease_result is None
    with SessionLocal() as db:
        work_item = db.scalar(
            select(WorkItem)
            .join(CampaignContact, WorkItem.campaign_contact_id == CampaignContact.id)
            .where(CampaignContact.contact_id == contact_id)
        )
        assert work_item is not None
        assert work_item.state == "suppressed"
        assert work_item.lease_owner_id is None


def _setup_import_race() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    ciphertext, phone_fingerprint = _unique_protected_phone()
    with SessionLocal() as db:
        actor = _new_user(db, "import-dnc-actor")
        uploader = _new_user(db, "import-uploader")
        contact = Contact(
            phone_ciphertext=ciphertext,
            phone_fingerprint=phone_fingerprint,
            phone_key_version=1,
        )
        campaign = Campaign(
            owning_scope_type="organization",
            external_code=f"c-{uuid.uuid4().hex[:8]}",
            name=f"Import suppression race {uuid.uuid4().hex[:8]}",
            default_region="ZW",
            timezone="Africa/Harare",
            status="draft",
            purpose="Race verification",
            data_source="Test fixture",
            data_obtained_at=datetime.now(UTC).date(),
            lawful_basis_or_consent_reference="TEST-RACE",
            created_by=uploader.id,
        )
        db.add_all([contact, campaign])
        db.flush()
        job = ImportJob(
            campaign_id=campaign.id,
            uploader_id=uploader.id,
            source_filename_display="race.csv",
            generated_storage_key=uuid.uuid4().hex,
            file_hash="0" * 64,
            state="parsed",
            total_rows=1,
            valid_rows=1,
            decision_version=1,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        db.add(job)
        db.flush()
        db.add_all(
            [
                ImportRow(
                    import_job_id=job.id,
                    row_number=1,
                    raw_phone_protected=ciphertext,
                    phone_fingerprint=phone_fingerprint,
                    canonical_values={"name": "Race Contact"},
                    validation_result="valid",
                    suppression_match=False,
                ),
                ImportDecision(
                    import_job_id=job.id,
                    decision_version=1,
                    decision="approve",
                    decided_by=uploader.id,
                ),
            ]
        )
        db.commit()
        return contact.id, actor.id, uploader.id, job.id


def _commit_import_with_pid(
    job_id: uuid.UUID,
    uploader_id: uuid.UUID,
    pid_queue: queue.Queue[int],
) -> dict:
    with SessionLocal() as db:
        pid = db.scalar(select(func.pg_backend_pid()))
        assert pid is not None
        pid_queue.put(pid)
        result = import_service.commit_job(
            db,
            job_id,
            actor_id=uploader_id,
            decision_version=1,
            idempotency_key=uuid.uuid4().hex,
        )
        db.commit()
        return result


def test_dnc_committing_first_forces_concurrent_import_to_suppress():
    contact_id, actor_id, uploader_id, job_id = _setup_import_race()
    suppression_ready = Event()
    release_suppression = Event()
    pid_queue: queue.Queue[int] = queue.Queue()

    with ThreadPoolExecutor(max_workers=2) as pool:
        suppression_future = pool.submit(
            _hold_suppression,
            contact_id,
            actor_id,
            suppression_ready,
            release_suppression,
        )
        assert suppression_ready.wait(timeout=5)
        import_future = pool.submit(_commit_import_with_pid, job_id, uploader_id, pid_queue)
        import_pid = pid_queue.get(timeout=5)
        try:
            _wait_for_advisory_lock_wait(import_pid)
        finally:
            release_suppression.set()

        suppression_future.result(timeout=10)
        result = import_future.result(timeout=10)

    assert result["inserted"] == 1
    assert result["suppressed"] == 1
    with SessionLocal() as db:
        campaign_contact = db.scalar(
            select(CampaignContact)
            .join(ImportJob, CampaignContact.campaign_id == ImportJob.campaign_id)
            .where(ImportJob.id == job_id, CampaignContact.contact_id == contact_id)
        )
        assert campaign_contact is not None
        assert campaign_contact.status == "suppressed"
        work_count = db.scalar(
            select(func.count(WorkItem.id)).where(
                WorkItem.campaign_contact_id == campaign_contact.id
            )
        )
        assert work_count == 0
        suppression = db.scalar(
            select(SuppressionEntry.id).where(
                SuppressionEntry.phone_fingerprint
                == db.get(Contact, contact_id).phone_fingerprint,
                SuppressionEntry.status == "active",
            )
        )
        assert suppression is not None
