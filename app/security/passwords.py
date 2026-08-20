"""Password hashing with Argon2id (via pwdlib)."""

from __future__ import annotations

from pwdlib import PasswordHash

_hasher = PasswordHash.recommended()

# Precomputed hash used to equalize timing when an account does not exist.
_DUMMY_HASH = _hasher.hash("timing-equalization-placeholder")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    return _hasher.verify(password, password_hash)


def dummy_verify() -> None:
    """Run a verify against a dummy hash so a missing account takes similar time."""
    _hasher.verify("wrong-password", _DUMMY_HASH)
