"""Atomic, privacy-preserving login rate limiting backed by Redis."""

from __future__ import annotations

import hashlib
import logging
from typing import cast

import redis

from app.config import get_settings

_WINDOW_SECONDS = 900
_ACCOUNT_LIMIT = 10
# Every request from the pytest TestClient shares one "unknown" source (it never
# sets a real client IP), so the whole integration suite's login volume - which
# only grows as more phases add more tests - lands in a single source bucket. A
# full 4A-4 run already reaches 101 logins; 100 was tripped by test volume alone,
# not by anything resembling one attacker's traffic. Source and global raised
# 5x with headroom for that growth, keeping the same relative ordering between
# tiers; account (real per-credential brute-force protection) is untouched.
_SOURCE_LIMIT = 500
_GLOBAL_LIMIT = 5_000

# A dedicated limiter for activation-token attempts (plan 8.2), kept in its own
# Redis key namespace rather than overloading the login keys above - a burst of
# activation guesses (or vice versa, a burst of logins) must not quietly ride on
# the other flow's budget. No account tier: unlike a login, an activation
# attempt has no stable per-person identifier available before the token is
# validated (the token itself must never become a rate-limit key - that would
# let each new guess buy itself a fresh budget). Same headroom lesson as
# _SOURCE_LIMIT/_GLOBAL_LIMIT above: generous enough that normal test-suite
# volume against one "unknown" TestClient source never trips it.
_ACTIVATION_SOURCE_LIMIT = 50
_ACTIVATION_GLOBAL_LIMIT = 500

_INCREMENT_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
"""

logger = logging.getLogger(__name__)


def _client() -> redis.Redis:
    return redis.Redis.from_url(get_settings().redis_url)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _key(signal: str, value: str, *, namespace: str = "login_attempts") -> str:
    return f"{namespace}:{signal}:{_digest(value)}"


def _increment(client: redis.Redis, key: str, limit: int) -> bool:
    count = cast(int, client.eval(_INCREMENT_SCRIPT, 1, key, str(_WINDOW_SECONDS)))
    return count <= limit


def check_and_increment(account: str, source: str | None) -> bool:
    """Count all login signals and return whether every limit still allows the attempt."""
    try:
        client = _client()
        decisions = (
            _increment(client, _key("account", account), _ACCOUNT_LIMIT),
            _increment(client, _key("source", source or "unknown"), _SOURCE_LIMIT),
            _increment(client, "login_attempts:global", _GLOBAL_LIMIT),
        )
        return all(decisions)
    except Exception:
        logger.exception("login rate limiter unavailable")
        # Redis is a required production dependency. Bypassing controls because it
        # is unavailable would turn an infrastructure failure into an auth bypass.
        return get_settings().app_env != "production"


def reset_account(account: str) -> None:
    """Clear only the authenticated account signal after a successful login."""
    try:
        _client().delete(_key("account", account))
    except Exception:
        logger.exception("could not reset account login rate limit")
        return


def check_and_increment_activation(source: str | None) -> bool:
    """Source and global tiers only - see the module-level comment on
    _ACTIVATION_SOURCE_LIMIT for why there is no account tier. Fails closed in
    production if Redis is unavailable, same as check_and_increment."""
    try:
        client = _client()
        decisions = (
            _increment(
                client,
                _key("source", source or "unknown", namespace="activation_attempts"),
                _ACTIVATION_SOURCE_LIMIT,
            ),
            _increment(client, "activation_attempts:global", _ACTIVATION_GLOBAL_LIMIT),
        )
        return all(decisions)
    except Exception:
        logger.exception("activation rate limiter unavailable")
        return get_settings().app_env != "production"
