"""Field-level symmetric encryption for sensitive values (for example TOTP secrets).

The Fernet key is derived from the configured field-encryption key, so any strong
secret string works. Ciphertext is prefixed with the key version for future rotation.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet

from app.config import get_settings


def _fernet(key: str) -> Fernet:
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(plaintext: str) -> str:
    settings = get_settings()
    key = settings.field_encryption_key.get_secret_value()
    token = _fernet(key).encrypt(plaintext.encode("utf-8")).decode("utf-8")
    return f"v{settings.field_encryption_key_version}:{token}"


def decrypt(ciphertext: str) -> str:
    settings = get_settings()
    key = settings.field_encryption_key.get_secret_value()
    _, _, token = ciphertext.partition(":")
    return _fernet(key).decrypt(token.encode("utf-8")).decode("utf-8")
