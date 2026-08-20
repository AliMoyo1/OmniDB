"""Application configuration.

Non-secret values come from the environment (or a local .env). Secret values are
read from files under /run/secrets in production (Docker secrets), by field name,
and fall back to environment variables for local development.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        secrets_dir="/run/secrets",
        extra="ignore",
    )

    app_env: str = "development"

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

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        password = self.db_password.get_secret_value()
        return (
            f"postgresql+psycopg://{self.db_user}:{password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
