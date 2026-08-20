"""Test configuration. Sets dev-only secrets before the app imports settings."""

from __future__ import annotations

import os

os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-not-for-prod")
os.environ.setdefault("FIELD_ENCRYPTION_KEY", "test-field-encryption-key")
os.environ.setdefault("PHONE_FINGERPRINT_HMAC_KEY", "test-hmac-key")
os.environ.setdefault("DB_PASSWORD", "testpass")
os.environ.setdefault("COOKIE_SECURE", "false")
