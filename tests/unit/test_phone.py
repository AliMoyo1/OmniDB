from __future__ import annotations

import phonenumbers
import pytest

from app.security.encryption import decrypt
from app.security.phone import PhoneParseError, fingerprint, normalize_to_e164, protect


def _zw_national_number() -> str:
    example = phonenumbers.example_number("ZW")
    if example is None:
        pytest.skip("no example number available for region ZW")
    return str(example.national_number)


def test_normalize_valid_number_to_e164():
    e164 = normalize_to_e164(_zw_national_number(), "ZW")
    assert e164.startswith("+263")


def test_normalize_invalid_raises():
    with pytest.raises(PhoneParseError):
        normalize_to_e164("not-a-number", "ZW")


def test_fingerprint_is_deterministic():
    e164 = normalize_to_e164(_zw_national_number(), "ZW")
    assert fingerprint(e164) == fingerprint(e164)


def test_fingerprint_differs_for_different_numbers():
    zw = normalize_to_e164(_zw_national_number(), "ZW")
    us_example = phonenumbers.example_number("US")
    if us_example is None:
        pytest.skip("no example number available for region US")
    us = phonenumbers.format_number(us_example, phonenumbers.PhoneNumberFormat.E164)
    assert fingerprint(zw) != fingerprint(us)


def test_fingerprint_is_not_a_plain_hash_of_the_number():
    # A keyed HMAC must differ from a plain SHA-256 of the same input (invariant 22).
    import hashlib

    e164 = normalize_to_e164(_zw_national_number(), "ZW")
    assert fingerprint(e164) != hashlib.sha256(e164.encode()).hexdigest()


def test_protect_roundtrip_and_consistency():
    national = _zw_national_number()
    result = protect(national, "ZW")
    assert decrypt(result.ciphertext) == result.e164
    assert result.fingerprint == fingerprint(result.e164)
