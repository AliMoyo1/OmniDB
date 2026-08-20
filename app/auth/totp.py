"""TOTP second factor (RFC 6238), compatible with Microsoft Authenticator (ADR-004A).

Secrets are stored encrypted at rest via the field-encryption helper.
"""

from __future__ import annotations

import pyotp

from app.security.encryption import decrypt, encrypt

ISSUER = "CipherContact"


def new_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, account_email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=account_email, issuer_name=ISSUER)


def verify_code(secret: str, code: str) -> bool:
    return pyotp.TOTP(secret).verify(code, valid_window=1)


def encrypt_secret(secret: str) -> str:
    return encrypt(secret)


def decrypt_secret(ciphertext: str) -> str:
    return decrypt(ciphertext)
