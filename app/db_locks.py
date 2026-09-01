"""Transaction-scoped PostgreSQL advisory locks for cross-table invariants."""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session


def phone_fingerprint_lock_id(phone_fingerprint: str) -> int:
    """Map a keyed phone fingerprint to PostgreSQL's signed 64-bit lock namespace."""
    digest = hashlib.sha256(
        b"ciphercontact:phone-suppression:v1:" + phone_fingerprint.encode("ascii")
    ).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def lock_phone_fingerprint(db: Session, phone_fingerprint: str) -> None:
    """Serialize import, suppression, and leasing decisions for one phone number.

    PostgreSQL releases this lock automatically when the surrounding transaction ends.
    Every code path must acquire it before locking or creating related work rows.
    """
    lock_id = phone_fingerprint_lock_id(phone_fingerprint)
    db.execute(select(func.pg_advisory_xact_lock(lock_id)))


def try_lock_phone_fingerprint(db: Session, phone_fingerprint: str) -> bool:
    """Try to reserve one phone without waiting behind another transaction.

    Leasing uses this form so a DNC or another lease for the first queue record
    cannot stall access to unrelated contacts. The lock, when acquired, is still
    held until the surrounding transaction finishes.
    """
    lock_id = phone_fingerprint_lock_id(phone_fingerprint)
    return db.scalar(select(func.pg_try_advisory_xact_lock(lock_id))) is True


def lock_idempotency_key(
    db: Session, namespace: str, actor_id: uuid.UUID, idempotency_key: str
) -> None:
    """Serialize retry-sensitive writes for one actor, endpoint, and key."""
    material = f"{namespace}:{actor_id}:{idempotency_key}".encode()
    lock_id = int.from_bytes(hashlib.sha256(material).digest()[:8], "big", signed=True)
    db.execute(select(func.pg_advisory_xact_lock(lock_id)))


def initial_super_admin_bootstrap_lock_id() -> int:
    """Return the installation-wide advisory lock used by first-admin provisioning."""
    digest = hashlib.sha256(b"ciphercontact:initial-super-admin-bootstrap:v1").digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def lock_initial_super_admin(db: Session) -> None:
    """Serialize creation of the first active Super Admin for this installation."""
    db.execute(select(func.pg_advisory_xact_lock(initial_super_admin_bootstrap_lock_id())))
