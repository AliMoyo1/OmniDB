"""Test configuration. Sets dev-only secrets before the app imports settings."""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-not-for-prod")
os.environ.setdefault("FIELD_ENCRYPTION_KEY", "test-field-encryption-key")
os.environ.setdefault("PHONE_FINGERPRINT_HMAC_KEY", "test-hmac-key")
os.environ.setdefault("DB_PASSWORD", "testpass")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "true")
# A fresh directory per test run avoids collisions between parallel or repeated runs.
os.environ.setdefault(
    "QUARANTINE_DIR", tempfile.mkdtemp(prefix="ciphercontact-test-quarantine-")
)
