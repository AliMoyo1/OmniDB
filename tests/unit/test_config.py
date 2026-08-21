from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings


def _strong(label: str) -> str:
    return f"{label}-A7z9-" + "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


def test_secret_files_override_environment_values(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("APP_SECRET_KEY", "environment-value-that-must-not-win")
    file_value = _strong("file-secret")
    (tmp_path / "app_secret_key").write_text(file_value, encoding="utf-8")

    settings = Settings(_secrets_dir=tmp_path)

    assert settings.app_secret_key.get_secret_value() == file_value


def test_production_rejects_placeholder_secrets():
    placeholder_password = "dev-only-" + "change-me"
    with pytest.raises(ValidationError, match="DB_PASSWORD"):
        Settings(
            app_env="production",
            db_password=placeholder_password,
            app_secret_key=_strong("app"),
            field_encryption_key=_strong("field"),
            phone_fingerprint_hmac_key=_strong("phone"),
            health_token=_strong("health"),
        )


def test_production_accepts_distinct_strong_file_style_secrets():
    settings = Settings(
        app_env="production",
        db_password=_strong("database"),
        app_secret_key=_strong("application"),
        field_encryption_key=_strong("encryption"),
        phone_fingerprint_hmac_key=_strong("fingerprint"),
        health_token=_strong("readiness"),
        cookie_secure=True,
        celery_task_always_eager=False,
    )

    assert settings.app_env == "production"


def test_database_url_quotes_special_password_characters():
    reserved_password = "p@ss:word/with?reserved" + "#characters"
    settings = Settings(db_password=reserved_password)

    assert "p%40ss%3Aword%2Fwith%3Freserved%23characters" in settings.database_url
