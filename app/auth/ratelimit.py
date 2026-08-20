"""Best-effort login rate limiting backed by Redis.

Fails open on Redis errors (the caller logs the failure). Full rate limiting by
account, source, and global signals is refined in a later step.
"""

from __future__ import annotations

import redis

from app.config import get_settings

_MAX_ATTEMPTS = 10
_WINDOW_SECONDS = 900


def _client() -> redis.Redis:
    return redis.Redis.from_url(get_settings().redis_url)


def check_and_increment(key: str) -> bool:
    """Return True if the attempt is allowed, False if over the limit."""
    try:
        client = _client()
        redis_key = f"login_attempts:{key}"
        count = client.incr(redis_key)
        if count == 1:
            client.expire(redis_key, _WINDOW_SECONDS)
        return int(count) <= _MAX_ATTEMPTS
    except Exception:
        return True


def reset(key: str) -> None:
    try:
        _client().delete(f"login_attempts:{key}")
    except Exception:
        return
