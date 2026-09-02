"""Review finding #3: record_decision previously incremented an already-loaded,
unlocked job object's decision_version and inserted a row claiming the new
value - two simultaneous decision calls could both read the same version and
both insert a row claiming it, leaving "the latest decision" nondeterministic.
record_decision now locks the job row with SELECT ... FOR UPDATE before
incrementing, and workforce_import_decisions.(import_job_id, decision_version)
is now a database-level unique constraint as a backstop. Same pattern as
tests/concurrency/test_staffing_capacity_concurrency.py: each thread opens its
own session/connection and races for real against the others.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db import SessionLocal
from app.models.identity import User
from app.models.workforce_imports import WorkforceImportDecision, WorkforceImportJob
from app.security.passwords import hash_password
from app.workforce_imports import service as import_service

pytestmark = pytest.mark.integration

_CALLERS = 12


def _setup() -> tuple[uuid.UUID, uuid.UUID]:
    with SessionLocal() as db:
        uploader = User(
            workforce_id=f"decrace-uploader-{uuid.uuid4().hex[:8]}",
            email=f"decrace-uploader-{uuid.uuid4().hex[:8]}@example.com",
            display_name="Decision Race Uploader",
            password_hash=hash_password("not-used-in-this-test"),
        )
        db.add(uploader)
        db.flush()

        # decision_tier="standard" + decision="reject" skips every authority
        # check beyond job accessibility (see record_decision), and the
        # uploader always passes that one - a minimal, row-free job is enough
        # to race repeated decisions against.
        job = WorkforceImportJob(
            import_type="users",
            uploader_id=uploader.id,
            source_filename_display="race.csv",
            generated_storage_key=f"race-{uuid.uuid4().hex}",
            file_hash="deadbeef",
            state="parsed",
            total_rows=0,
            valid_rows=0,
            warning_rows=0,
            invalid_rows=0,
            high_risk_rows=0,
            decision_version=0,
        )
        db.add(job)
        db.commit()
        return uploader.id, job.id


def _reject_in_own_session(job_id: uuid.UUID, uploader_id: uuid.UUID) -> bool:
    """True if this call recorded a decision. False only for the specific,
    anticipated-if-broken conflict this test targets (a duplicate version
    caught by the database's own unique constraint); any other exception
    propagates and fails the test."""
    with SessionLocal() as db:
        try:
            import_service.record_decision(
                db, job_id, decided_by=uploader_id, decision="reject",
                decision_tier="standard", note=None,
            )
            db.commit()
            return True
        except IntegrityError:
            db.rollback()
            return False


def test_concurrent_decisions_never_share_a_version():
    uploader_id, job_id = _setup()

    with ThreadPoolExecutor(max_workers=_CALLERS) as pool:
        results = list(
            pool.map(lambda _: _reject_in_own_session(job_id, uploader_id), range(_CALLERS))
        )

    accepted = sum(1 for r in results if r)
    assert accepted == _CALLERS, (
        f"expected all {_CALLERS} concurrent decisions to succeed with distinct "
        f"versions (locking should serialize them, not reject any) - got {accepted}"
    )

    with SessionLocal() as db:
        versions = list(
            db.scalars(
                select(WorkforceImportDecision.decision_version).where(
                    WorkforceImportDecision.import_job_id == job_id
                )
            )
        )
        assert len(versions) == _CALLERS
        assert len(set(versions)) == _CALLERS, (
            "two concurrent decisions recorded the same decision_version - "
            f"versions were: {sorted(versions)}"
        )
        assert set(versions) == set(range(1, _CALLERS + 1))

        job = db.get(WorkforceImportJob, job_id)
        assert job is not None
        assert job.decision_version == _CALLERS
