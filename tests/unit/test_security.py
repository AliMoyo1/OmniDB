from __future__ import annotations

import pyotp

from app.auth import csrf
from app.auth import totp as totp_mod
from app.security.encryption import decrypt, encrypt
from app.security.passwords import hash_password, verify_password
from app.security.tokens import generate_token, hash_token


def test_password_roundtrip():
    hashed = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", hashed)
    assert not verify_password("wrong", hashed)
    assert not verify_password("anything", None)


def test_token_hash_is_deterministic_and_sized():
    token = generate_token()
    assert hash_token(token) == hash_token(token)
    assert len(hash_token(token)) == 64
    assert generate_token() != generate_token()


def test_field_encryption_roundtrip():
    ciphertext = encrypt("s3cr3t-value")
    assert ciphertext.startswith("v")
    assert decrypt(ciphertext) == "s3cr3t-value"


def test_totp_verify():
    secret = totp_mod.new_secret()
    assert totp_mod.verify_code(secret, pyotp.TOTP(secret).now())
    assert not totp_mod.verify_code(secret, "000000")


def test_csrf_is_session_bound():
    token = csrf.issue("session-123")
    assert csrf.validate(token, "session-123")
    assert not csrf.validate(token, "session-999")
    assert not csrf.validate(None, "session-123")
    assert not csrf.validate("garbage", "session-123")
