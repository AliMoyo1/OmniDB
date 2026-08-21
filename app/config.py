"""Application configuration.

Non-secret values come from the environment (or a local .env). Secret values are
read from files under /run/secrets in production (Docker secrets), by field name,
and fall back to environment variables for local development.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal, Self

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict
from sqlalchemy.engine import URL

_PLACEHOLDER_MARKERS = (
    "change-me",
    "dev-only",
    "example",
    "not-for-prod",
    "placeholder",
    "replace-with",
    "test-secret",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        secrets_dir="/run/secrets",
        extra="ignore",
    )

    app_env: Literal["development", "test", "production"] = "development"

    # LAN host Caddy serves on (server IP in production)
    server_host: str = "localhost"

    # PostgreSQL connection parts (password is a secret)
    db_host: str = "postgres"
    db_port: int = 5432
    db_name: str = "ciphercontact"
    db_user: str = "ciphercontact"
    db_password: SecretStr = SecretStr("")

    # Redis
    redis_url: str = "redis://redis:6379/0"

    # Sessions
    session_idle_minutes: int = 30
    session_absolute_hours: int = 8
    step_up_minutes: int = 10

    # App secret for session and CSRF signing
    app_secret_key: SecretStr = SecretStr("")

    # Field encryption and phone-fingerprint HMAC (key material from secret files)
    field_encryption_key: SecretStr = SecretStr("")
    field_encryption_key_version: int = 1
    phone_fingerprint_hmac_key: SecretStr = SecretStr("")
    phone_fingerprint_key_version: int = 1

    # Localization / jurisdiction (configurable, Zimbabwe baseline)
    default_timezone: str = "Africa/Harare"
    jurisdiction: str = "ZW"

    # Upload limits
    upload_max_bytes: int = 10_485_760
    upload_max_expanded_bytes: int = 209_715_200
    upload_max_rows: int = 100_000
    upload_max_columns: int = 50
    upload_max_cell_length: int = 500

    # Quarantine storage: outside the webroot, non-executable.
    quarantine_dir: str = "/var/lib/ciphercontact/quarantine"
    import_expiry_hours: int = 72

    # Run Celery tasks synchronously in-process (tests only; never in production).
    celery_task_always_eager: bool = False

    # Agent work queue
    lease_duration_minutes: int = 15
    max_skips_before_review: int = 3

    # Operational
    log_level: str = "INFO"
    health_token: SecretStr = SecretStr("")
    # Set false only for local HTTP development; production is always HTTPS.
    cookie_secure: bool = True

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Docker secret files outrank process and dotenv values. Locally there is no
        # secrets directory, so ordinary environment configuration still works.
        return init_settings, file_secret_settings, env_settings, dotenv_settings

    @model_validator(mode="after")
    def validate_production_safety(self) -> Self:
        if self.app_env != "production":
            return self

        required = {
            "DB_PASSWORD": (self.db_password.get_secret_value(), 24),
            "APP_SECRET_KEY": (self.app_secret_key.get_secret_value(), 32),
            "FIELD_ENCRYPTION_KEY": (self.field_encryption_key.get_secret_value(), 32),
            "PHONE_FINGERPRINT_HMAC_KEY": (
                self.phone_fingerprint_hmac_key.get_secret_value(),
                32,
            ),
            "HEALTH_TOKEN": (self.health_token.get_secret_value(), 32),
        }
        for name, (value, minimum_length) in required.items():
            normalized = value.strip().lower()
            if len(value) < minimum_length:
                raise ValueError(f"{name} must contain at least {minimum_length} characters")
            if any(marker in normalized for marker in _PLACEHOLDER_MARKERS):
                raise ValueError(f"{name} contains a known placeholder value")
            if len(set(value)) < 12:
                raise ValueError(f"{name} does not contain enough character diversity")

        secret_values = [value for value, _ in required.values()]
        if len(set(secret_values)) != len(secret_values):
            raise ValueError("production secrets must use distinct values")
        if not self.cookie_secure:
            raise ValueError("COOKIE_SECURE must be true in production")
        if self.celery_task_always_eager:
            raise ValueError("CELERY_TASK_ALWAYS_EAGER must be false in production")
        return self

    @property
    def database_url(self) -> str:
        return URL.create(
            "postgresql+psycopg",
            username=self.db_user,
            password=self.db_password.get_secret_value(),
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        ).render_as_string(hide_password=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()
