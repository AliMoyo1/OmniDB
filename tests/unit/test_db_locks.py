from __future__ import annotations

from app.db_locks import (
    initial_super_admin_bootstrap_lock_id,
    phone_fingerprint_lock_id,
)


def test_phone_lock_id_is_stable_distinct_and_signed_64_bit():
    first = "a" * 64
    second = "b" * 64

    assert phone_fingerprint_lock_id(first) == phone_fingerprint_lock_id(first)
    assert phone_fingerprint_lock_id(first) != phone_fingerprint_lock_id(second)
    assert -(2**63) <= phone_fingerprint_lock_id(first) < 2**63


def test_initial_super_admin_bootstrap_lock_id_is_stable_and_signed_64_bit():
    lock_id = initial_super_admin_bootstrap_lock_id()

    assert lock_id == initial_super_admin_bootstrap_lock_id()
    assert -(2**63) <= lock_id < 2**63
