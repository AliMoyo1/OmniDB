from __future__ import annotations

import pytest

from app.auth import password_policy


def test_rejects_password_shorter_than_minimum():
    with pytest.raises(password_policy.WeakPassword, match="at least"):
        password_policy.validate_password_strength("short1234")


def test_rejects_common_password_case_and_whitespace_insensitively():
    with pytest.raises(password_policy.WeakPassword, match="too common"):
        password_policy.validate_password_strength("  Password123  ")


def test_accepts_a_long_passphrase():
    # Permits passphrases (plan 8.1) - no composition rule blocks this.
    password_policy.validate_password_strength("correct horse battery staple zebra")


def test_does_not_reject_a_passphrase_merely_containing_a_blocklisted_word():
    # Substring matching would over-block real passphrases; only an exact
    # (normalized) match against the blocklist is rejected.
    password_policy.validate_password_strength("my dragon flies over the mountain at dawn")


def test_rejects_password_equal_to_a_context_term():
    # Long enough to pass the length check, so this genuinely exercises the
    # context match rather than failing for the wrong reason.
    workforce_email = "jane.doe@example.com"
    with pytest.raises(password_policy.WeakPassword, match="name, email, or ID"):
        password_policy.validate_password_strength(
            workforce_email, context=["Jane Doe", workforce_email, "jd001"]
        )


def test_context_terms_are_not_checked_when_omitted():
    # A password that happens to equal some string is fine unless that string
    # was actually passed as context for this call.
    password_policy.validate_password_strength("jane.doe.long.enough.pw")
