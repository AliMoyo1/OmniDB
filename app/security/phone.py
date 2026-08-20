"""Phone parsing, keyed-HMAC fingerprinting, and encryption (plan 10.2, ADR-019).

Fingerprints use a keyed HMAC-SHA256 over the normalized E.164 number, never a plain
hash, because the phone-number space is small enough to enumerate (invariant 22). The
HMAC key is held separately from the field-encryption key and is versioned so it can be
rotated later.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

import phonenumbers
from phonenumbers import NumberParseException

from app.config import get_settings
from app.security.encryption import encrypt


class PhoneParseError(ValueError):
    """The input could not be parsed into a valid phone number."""


@dataclass(frozen=True)
class ProtectedPhone:
    e164: str
    ciphertext: str
    fingerprint: str
    fingerprint_key_version: int


def normalize_to_e164(raw: str, default_region: str) -> str:
    """Parse `raw` using `default_region` for national-format numbers. Raise on failure."""
    try:
        parsed = phonenumbers.parse(raw, default_region)
    except NumberParseException as exc:
        raise PhoneParseError(str(exc)) from exc
    if not phonenumbers.is_valid_number(parsed):
        raise PhoneParseError(f"not a valid number for region {default_region}")
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def fingerprint(e164: str) -> str:
    settings = get_settings()
    key = settings.phone_fingerprint_hmac_key.get_secret_value().encode("utf-8")
    return hmac.new(key, e164.encode("utf-8"), hashlib.sha256).hexdigest()


def protect(raw: str, default_region: str) -> ProtectedPhone:
    """Parse, encrypt, and fingerprint in one step. Raises PhoneParseError on bad input."""
    e164 = normalize_to_e164(raw, default_region)
    settings = get_settings()
    return ProtectedPhone(
        e164=e164,
        ciphertext=encrypt(e164),
        fingerprint=fingerprint(e164),
        fingerprint_key_version=settings.phone_fingerprint_key_version,
    )
