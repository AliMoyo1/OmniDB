from __future__ import annotations

from unittest.mock import Mock

from app.config import Settings
from app.monitoring import configure_sentry


def test_sentry_is_disabled_without_a_dsn(monkeypatch):
    init = Mock()
    monkeypatch.setattr("app.monitoring.sentry_sdk.init", init)

    configure_sentry(Settings(app_env="test", sentry_dsn=""))

    init.assert_not_called()


def test_sentry_uses_privacy_safe_defaults(monkeypatch):
    init = Mock()
    monkeypatch.setattr("app.monitoring.sentry_sdk.init", init)

    configure_sentry(
        Settings(
            app_env="test",
            sentry_dsn="https://public@example.invalid/1",
            sentry_traces_sample_rate=0.05,
            sentry_release="ciphercontact@abc123",
        )
    )

    kwargs = init.call_args.kwargs
    assert kwargs["environment"] == "test"
    assert kwargs["release"] == "ciphercontact@abc123"
    assert kwargs["send_default_pii"] is False
    assert kwargs["include_local_variables"] is False
    assert kwargs["max_request_body_size"] == "never"
    assert kwargs["enable_logs"] is False
    assert kwargs["traces_sample_rate"] == 0.05
