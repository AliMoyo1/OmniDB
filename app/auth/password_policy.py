"""Shared password-strength policy for activation (plan 8.1).

One function, called by both the API and browser activation endpoints, so
neither route can accept a password the other rejects. Deliberately no
composition rules (uppercase/lowercase/number/symbol) and no periodic
rotation - current NIST SP 800-63B and OWASP guidance favors adequate length,
a compromised/common-password check, rate limiting, and MFA over predictable
composition and rotation rules, and explicitly permits passphrases.
"""

from __future__ import annotations

MIN_PASSWORD_LENGTH = 12

# An offline blocklist of common and easily-guessed passwords (NIST SP
# 800-63B 5.1.1.2) - not exhaustive and not a network call to a breach
# database, just enough to catch the obvious ones a length-only rule misses.
# Matched case-insensitively against the whole password, not as a substring,
# so a real passphrase that happens to contain one of these words is not
# penalized.
COMMON_PASSWORDS = frozenset(
    {
        "password", "password1", "password123", "passw0rd", "p@ssw0rd", "p@ssword",
        "123456", "1234567", "12345678", "123456789", "1234567890", "12345",
        "qwerty", "qwerty123", "qwertyuiop", "letmein", "letmein123",
        "welcome", "welcome1", "welcome123",
        "admin", "administrator", "changeme", "changeme123",
        "iloveyou", "monkey", "dragon", "football", "baseball", "basketball",
        "master", "superman", "batman", "trustno1", "shadow",
        "abc123", "abc12345", "123123", "111111", "000000", "666666",
        "sunshine", "princess", "flower", "hunter2", "starwars", "solo",
        "correcthorsebatterystaple",
    }
)


class WeakPassword(Exception):
    """The password fails the shared strength policy. The message is safe to
    show the user directly."""


def validate_password_strength(password: str, *, context: list[str] | None = None) -> None:
    """Raises WeakPassword with a user-facing message, or returns silently.

    `context` is a list of context-specific terms (e.g. the account's email
    local part, display name) that the password must not exactly equal -
    passed only by callers that already have the identity in hand before the
    password is set, since activation resolves its token to a user only as
    part of setting the password itself."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise WeakPassword(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    normalized = password.strip().lower()
    if normalized in COMMON_PASSWORDS:
        raise WeakPassword("that password is too common; choose something less guessable")
    for term in context or ():
        term_normalized = term.strip().lower()
        if term_normalized and term_normalized == normalized:
            raise WeakPassword("password must not be the same as your name, email, or ID")
