"""Privacy-conscious Sentry initialization for web and Celery processes."""

from __future__ import annotations

import sentry_sdk
from sentry_sdk.integrations.celery import CeleryIntegration
from sentry_sdk.integrations.fastapi import FastApiIntegration

from app.config import Settings


def configure_sentry(settings: Settings) -> None:
    """Enable Sentry only when a DSN is explicitly configured."""
    dsn = settings.sentry_dsn.get_secret_value().strip()
    if not dsn:
        return

    sentry_sdk.init(
        dsn=dsn,
        environment=settings.app_env,
        release=settings.sentry_release.strip() or None,
        integrations=[FastApiIntegration(), CeleryIntegration()],
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
        enable_logs=False,
        traces_sample_rate=settings.sentry_traces_sample_rate,
    )
