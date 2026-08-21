from __future__ import annotations

from types import SimpleNamespace

from app.auth import ratelimit


class _FakeRedis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.deleted: list[str] = []

    def eval(self, _script: str, _key_count: int, key: str, _window: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    def delete(self, key: str) -> None:
        self.deleted.append(key)


class _UnavailableRedis:
    def eval(self, *_args) -> int:
        raise ConnectionError("redis unavailable")


def test_rate_limit_uses_hashed_account_source_and_global_signals(monkeypatch):
    client = _FakeRedis()
    monkeypatch.setattr(ratelimit, "_client", lambda: client)

    assert ratelimit.check_and_increment("person@example.com", "192.0.2.4")

    keys = set(client.counts)
    assert "login_attempts:global" in keys
    assert all("person@example.com" not in key for key in keys)
    assert all("192.0.2.4" not in key for key in keys)
    assert len(keys) == 3


def test_rate_limit_denies_after_account_threshold(monkeypatch):
    client = _FakeRedis()
    monkeypatch.setattr(ratelimit, "_client", lambda: client)

    decisions = [
        ratelimit.check_and_increment("person@example.com", f"192.0.2.{index}")
        for index in range(1, 12)
    ]

    assert decisions[:10] == [True] * 10
    assert decisions[10] is False


def test_rate_limit_fails_closed_only_in_production(monkeypatch):
    monkeypatch.setattr(ratelimit, "_client", lambda: _UnavailableRedis())

    monkeypatch.setattr(
        ratelimit, "get_settings", lambda: SimpleNamespace(app_env="production")
    )
    assert not ratelimit.check_and_increment("person@example.com", "192.0.2.4")

    monkeypatch.setattr(
        ratelimit, "get_settings", lambda: SimpleNamespace(app_env="development")
    )
    assert ratelimit.check_and_increment("person@example.com", "192.0.2.4")


def test_successful_login_resets_only_hashed_account_signal(monkeypatch):
    client = _FakeRedis()
    monkeypatch.setattr(ratelimit, "_client", lambda: client)

    ratelimit.reset_account("person@example.com")

    assert len(client.deleted) == 1
    assert client.deleted[0].startswith("login_attempts:account:")
    assert "person@example.com" not in client.deleted[0]
